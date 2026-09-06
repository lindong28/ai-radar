#!/usr/bin/env bash
# Re-score the archive under the current scoring ruleset, in chunks, without starving the
# scheduled pipeline.
#
# Exits (four of them watch this job; the fifth watches what this job does to everything else):
#   1. a chunk reports processed=0 -- nothing left. Each successful chunk writes rows at the
#      current ruleset version and the candidate query skips items that already have one, so the
#      set shrinks monotonically. Do NOT add --force: it disables that skip and this exit.
#   2. wall-clock bounds, overall and per chunk. A stalled provider call has no timeout of its
#      own, and `timeout`/`gtimeout` are absent here, so the bound is a recorded pid plus a
#      deadline.
#   3. consecutive failures, patient enough to outlast a pipeline phase holding the write lock.
#   4. error rate. A failed scoring call still writes a row and the candidate query skips on
#      ruleset_version alone -- it does not check error IS NULL -- so a rate-limit burst would
#      permanently exclude whatever it touched, silently.
#   5. PIPELINE STARVATION. Added 2026-09-06 after this job, running at eight workers, took the
#      scoring lock in a tight loop and stretched the pipeline's own score stage from 12-66s to
#      over two hours: five consecutive cron rounds logged "already running" and nothing entered
#      the archive. Exits 1-4 all watch this job's own health, so all four read green throughout;
#      a person reading consumer-side counters found it. This one watches the pipeline instead.
#
# BACKFILL_YIELD=0 keeps going while reporting the starvation instead of pausing for it. That is
# a deliberate override, not a default: it means someone has decided the site can wait.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

CHUNK=${BACKFILL_CHUNK:-500}
WORKERS=${BACKFILL_WORKERS:-8}
SINCE=${BACKFILL_SINCE:-400d}
YIELD=${BACKFILL_YIELD:-1}
# Per stage, because normal durations differ by an order of magnitude and a single number is
# wrong for all of them: fetch alone has a MEDIAN of 820s, so any threshold tight enough to catch
# a score stall fires on every healthy fetch and the guard yields forever. Values are 2x the
# measured p95 over 303 completed runs of each stage (see docs/issues/aihot-fit-eval.md
# ISSUE-FIT-29 for the table). An earlier revision used one 350s threshold justified by thirteen
# score samples; that sample covered one stage and one afternoon.
starve_threshold() {
  case "$1" in
    fetch)    echo 3800 ;;
    enrich)   echo 900 ;;
    prefilter) echo 700 ;;
    interpret) echo 450 ;;
    curate)   echo 400 ;;
    score)    echo 300 ;;
    *preflight*) echo 60 ;;
    *)        echo 900 ;;   # a stage this table has not seen
  esac
}
OVERALL_DEADLINE=$(( $(date -u +%s) + ${BACKFILL_HOURS:-8} * 3600 ))
CHUNK_LIMIT_SECONDS=${BACKFILL_CHUNK_SECONDS:-1200}
export AI_RADAR_SQLITE_BUSY_TIMEOUT_MS=${AI_RADAR_SQLITE_BUSY_TIMEOUT_MS:-120000}

started=$(date -u +%s); total=0; errors_total=0; fails=0; yielded=0; unknowns=0

log() { echo "$(date -u +%H:%M:%SZ) $*"; }

while (( $(date -u +%s) < OVERALL_DEADLINE )); do
  # Checked before each chunk, not after, so the pipeline gets the lock back before this job
  # queues up another 500 items behind it. Detection latency is therefore one chunk (~3 min at
  # the measured rate), which is accepted: it is well under every threshold in the table.
  probe=$(uv run python scripts/pipeline_stage_watch.py 2>&1); probe_rc=$?
  if (( probe_rc == 2 )); then
    # "Cannot tell" is not "starved". Treating it as starvation is how a broken probe turns into
    # an eight-hour pause that writes nothing, and the probe's own crash used to exit 1 exactly
    # like a real stall. Loud, and keep going.
    unknowns=$(( unknowns + 1 ))
    log "PROBE UNKNOWN ($probe) -- proceeding; the starvation guard is blind this round (#$unknowns)"
  elif (( probe_rc == 0 )) && [[ "$probe" =~ ^(.+):\ running\ for\ ([0-9]+)s$ ]]; then
    stage=${BASH_REMATCH[1]}; secs=${BASH_REMATCH[2]}
    if (( secs > $(starve_threshold "$stage") )); then
      yielded=$(( yielded + 1 ))
      if (( YIELD )); then
        log "PIPELINE STARVED ($probe, threshold $(starve_threshold "$stage")s) -- pausing 120s (yield #$yielded)"
        sleep 120
        continue
      fi
      log "PIPELINE STARVED ($probe) -- continuing anyway, BACKFILL_YIELD=0 (occurrence #$yielded)"
    fi
  fi

  out_file=$(mktemp)
  ./run.sh score --since "$SINCE" --limit "$CHUNK" --workers "$WORKERS" --commit-every 100 >"$out_file" 2>&1 &
  pid=$!
  chunk_deadline=$(( $(date -u +%s) + CHUNK_LIMIT_SECONDS ))
  while kill -0 "$pid" 2>/dev/null; do
    if (( $(date -u +%s) >= chunk_deadline )); then
      kill -TERM "$pid" 2>/dev/null; sleep 5; kill -KILL "$pid" 2>/dev/null
      log "chunk exceeded ${CHUNK_LIMIT_SECONDS}s and was killed"
      break
    fi
    sleep 5
  done
  wait "$pid" 2>/dev/null
  out=$(tail -1 "$out_file")

  if [[ "$out" =~ processed=([0-9]+)\ errors=([0-9]+) ]]; then
    n=${BASH_REMATCH[1]}; e=${BASH_REMATCH[2]}
    if (( n > 0 && e * 5 > n )); then
      log "STOPPING: chunk had $e errors in $n items (>20%); $total rows written"
      log "errored items are now skipped forever by ruleset_version; they need targeted repair"
      exit 3
    fi
    rm -f "$out_file"
    total=$(( total + n )); errors_total=$(( errors_total + e )); fails=0
    log "chunk=$n err=$e total=$total errs=$errors_total yields=$yielded elapsed=$(( $(date -u +%s) - started ))s"
    if (( n == 0 )); then
      log "backfill complete: $total rows, $errors_total errored, $yielded yields, $unknowns blind rounds"
      exit 0
    fi
  else
    fails=$(( fails + 1 ))
    # The output file is kept, not deleted: exit 1 fires after sixty of these, and sixty
    # 140-character tails with no stack between them is not a diagnosis.
    log "chunk failed ($fails/60): ${out:0:140} [full output: $out_file]"
    if (( fails >= 60 )); then
      log "giving up after 60 consecutive failures (~1h of contention); $total rows, $yielded yields, $unknowns blind rounds"
      exit 1
    fi
    sleep 60
  fi
done

log "overall deadline reached; $total rows written, $yielded yields, $unknowns blind rounds, archive only partly re-scored"
exit 2
