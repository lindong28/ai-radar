#!/usr/bin/env bash
# Push a consistent snapshot of the primary database to the read-only replica.
#
# Runs on the Mac, which owns the primary. The server never pulls: only the
# writer knows when a consistent snapshot exists.
#
# Four things here are deliberate and were each learned the expensive way:
#
#   .backup, not a file copy   Copying a live database while the pipeline writes
#                              produces a torn file. `VACUUM INTO` was tried and
#                              rejected -- it corrupts the FTS5 inverted index.
#                              The standard WAL reader is made query-only first;
#                              read-only mode cannot create a missing WAL shm.
#
#   GNU rsync, or nothing      macOS ships Apple's openrsync (protocol 29). Its
#                              negotiation with the server's rsync 3.x has been
#                              observed corrupting FTS5 bytes. The previous
#                              workaround was to fall back to scp, which has no
#                              delta algorithm at all -- that is how incremental
#                              transfer was lost without anyone deciding to lose
#                              it. This script fails instead of falling back.
#
#   Persistent base replica    Rebuilding a stripped DB every round renumbers
#                              most SQLite pages and destroys rsync locality.
#                              Bootstrap once, then apply PK deltas in place and
#                              reconcile every non-FTS table before shipping.
#
#   Delta against basis        The transferred base-only replica is compared
#                              with the last *accepted* base-only artifact. The
#                              server basis remains byte-identical to what this
#                              producer sends; serving FTS is rebuilt elsewhere.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DB="${AI_RADAR_SYNC_DB:-$REPO_ROOT/data/radar.db}"
SERVER="${AI_RADAR_SYNC_SERVER:-tencent-webserver-china}"
REMOTE_DATA="${AI_RADAR_SYNC_REMOTE_DATA:-ai-radar/data}"
REMOTE_APPLY_TRIGGER="${AI_RADAR_SYNC_REMOTE_APPLY_TRIGGER:-sudo systemctl start ai-radar-db-apply.service --no-block}"
REMOTE_JOURNAL="${AI_RADAR_SYNC_REMOTE_JOURNAL:-$REMOTE_DATA/switch-journal.json}"
REMOTE_RECEIPT="${AI_RADAR_SYNC_REMOTE_RECEIPT:-$REMOTE_DATA/accepted-snapshot.json}"
REMOTE_PYTHON="${AI_RADAR_SYNC_REMOTE_PYTHON:-python3}"
APPLY_TIMEOUT_S="${AI_RADAR_SYNC_APPLY_TIMEOUT_S:-3600}"
APPLY_POLL_INTERVAL_S="${AI_RADAR_SYNC_APPLY_POLL_INTERVAL_S:-5}"
SNAPSHOT="${AI_RADAR_SYNC_SNAPSHOT:-$REPO_ROOT/data/radar.db.snapshot}"
REPLICA="${AI_RADAR_SYNC_REPLICA:-$REPO_ROOT/data/radar.db.shipping}"
MANIFEST="${AI_RADAR_SYNC_MANIFEST:-$REPO_ROOT/data/radar.db.shipping.fts-manifest.json}"
LOCK_DIR="${AI_RADAR_SYNC_LOCK:-$REPO_ROOT/data/.sync.lock}"
DRY_RUN="${AI_RADAR_SYNC_DRY_RUN:-0}"
PYTHON="${AI_RADAR_PYTHON:-$REPO_ROOT/.venv/bin/python3}"

# Bypass any local proxy: the tunnel does not route to this host and the
# resulting failure reads like a network outage rather than a proxy problem.
SSH_OPTS="${AI_RADAR_SYNC_SSH_OPTS:--o ProxyCommand=none}"

log()  { printf '[sync] %s\n' "$*"; }
fail() { printf '[sync] ✗ %s\n' "$*" >&2; exit 1; }

remote_journal_generation() {
  local remote_command
  printf -v remote_command 'AI_RADAR_JOURNAL_GENERATION=1 %q - %q' \
    "$REMOTE_PYTHON" "$REMOTE_JOURNAL"
  # shellcheck disable=SC2086 # SSH_OPTS intentionally follows the existing env-string contract.
  ssh $SSH_OPTS "$SERVER" "$remote_command" <<'PY'
import hashlib
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    with path.open("rb") as handle:
        payload = handle.read()
        metadata = os.fstat(handle.fileno())
except FileNotFoundError:
    print("missing")
    raise SystemExit(0)

generation = hashlib.sha256()
generation.update(
    f"{metadata.st_dev}:{metadata.st_ino}:{metadata.st_mtime_ns}:{metadata.st_size}:".encode()
)
generation.update(payload)
print(generation.hexdigest())
PY
}

remote_terminal_state() {
  local snapshot_id=$1 manifest_sha256=$2 previous_generation=$3 remote_command
  # The marker lets interface tests execute this exact parser through their
  # SSH transport without replacing it. printf %q keeps configured paths as
  # single remote-shell arguments; REMOTE_PYTHON is an executable path, not a
  # shell command (the apply trigger is the configurable command surface).
  printf -v remote_command 'AI_RADAR_TERMINAL_POLL=1 %q - %q %q %q %q %q' \
    "$REMOTE_PYTHON" "$REMOTE_JOURNAL" "$REMOTE_RECEIPT" \
    "$snapshot_id" "$manifest_sha256" "$previous_generation"
  # shellcheck disable=SC2086 # SSH_OPTS intentionally follows the existing env-string contract.
  ssh $SSH_OPTS "$SERVER" "$remote_command" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import sys

journal_path, receipt_path, snapshot_id, manifest_sha256, previous_generation = sys.argv[1:]
full_sha256 = re.compile(r"[0-9a-f]{64}").fullmatch


def load_json(path: str) -> dict[str, object] | None:
    try:
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


try:
    with pathlib.Path(journal_path).open("rb") as handle:
        journal_bytes = handle.read()
        journal_metadata = os.fstat(handle.fileno())
    journal = json.loads(journal_bytes)
except (OSError, json.JSONDecodeError):
    journal = None
if not isinstance(journal, dict):
    print("pending")
    raise SystemExit(0)

journal_generation = hashlib.sha256()
journal_generation.update(
    (
        f"{journal_metadata.st_dev}:{journal_metadata.st_ino}:"
        f"{journal_metadata.st_mtime_ns}:{journal_metadata.st_size}:"
    ).encode()
)
journal_generation.update(journal_bytes)
if journal_generation.hexdigest() == previous_generation:
    print("pending")
    raise SystemExit(0)

state = journal.get("state")
if state == "committed" and journal.get("snapshot_id") == snapshot_id:
    receipt = load_json(receipt_path)
    authority_matches = (
        journal.get("journal_schema_version") == 2
        and journal.get("manifest_sha256") == manifest_sha256
        and receipt is not None
        and receipt.get("receipt_schema_version") == 2
        and receipt.get("snapshot_id") == snapshot_id
        and receipt.get("manifest_sha256") == manifest_sha256
        and receipt.get("serving_port") == journal.get("serving_port")
    )
    print("committed" if authority_matches else "blocked:committed-authority-mismatch")
    raise SystemExit(0)

if state == "quarantined":
    failure_path = journal.get("failure_path")
    failure_sha256 = journal.get("failure_sha256")
    failure_id = journal.get("failure_id")
    if (
        isinstance(failure_path, str)
        and isinstance(failure_sha256, str)
        and full_sha256(failure_sha256) is not None
        and isinstance(failure_id, str)
    ):
        failure_bytes = None
        try:
            failure_bytes = pathlib.Path(failure_path).read_bytes()
            failure = json.loads(failure_bytes)
        except (OSError, json.JSONDecodeError):
            failure = None
        if (
            failure_bytes is not None
            and hashlib.sha256(failure_bytes).hexdigest() == failure_sha256
        ) and (
            isinstance(failure, dict)
            and failure.get("failure_schema_version") == 1
            and failure.get("failure_id") == failure_id
            and failure.get("snapshot_id") == snapshot_id
        ):
            print("quarantined")
            raise SystemExit(0)

blocked_states = {
    "retry_blocked_verifier_changed",
    "rollback_blocked_invalid_oracle",
    "finalize_blocked_invalid_authority",
}
if (
    state in blocked_states
    and journal.get("snapshot_id") == snapshot_id
):
    print(f"blocked:{state}")
else:
    print("pending")
PY
}

wait_for_apply_terminal() {
  local snapshot_id=$1 manifest_sha256=$2 previous_generation=$3
  local started_at=$SECONDS status
  [[ "$APPLY_TIMEOUT_S" =~ ^[0-9]+$ ]] \
    || fail "AI_RADAR_SYNC_APPLY_TIMEOUT_S must be a non-negative integer"
  [[ "$APPLY_POLL_INTERVAL_S" =~ ^[0-9]+$ ]] \
    || fail "AI_RADAR_SYNC_APPLY_POLL_INTERVAL_S must be a non-negative integer"
  while true; do
    if ! status="$(remote_terminal_state \
      "$snapshot_id" "$manifest_sha256" "$previous_generation" 2>/dev/null)"; then
      status="pending"
    fi
    case "$status" in
      committed)
        log "terminal state committed for snapshot $snapshot_id"
        return 0
        ;;
      quarantined)
        fail "terminal state quarantined for snapshot $snapshot_id; apply did not accept this snapshot. First step: ssh $SERVER cat '$REMOTE_JOURNAL'"
        ;;
      blocked:*)
        fail "terminal state blocked for snapshot $snapshot_id (${status#blocked:}); apply needs manual intervention. First step: ssh $SERVER cat '$REMOTE_JOURNAL'"
        ;;
    esac
    if (( SECONDS - started_at >= APPLY_TIMEOUT_S )); then
      fail "timed out waiting for terminal state for snapshot $snapshot_id after ${APPLY_TIMEOUT_S}s; acceptance is unknown and apply may still be running. First step: ssh $SERVER cat '$REMOTE_JOURNAL'"
    fi
    sleep "$APPLY_POLL_INTERVAL_S"
  done
}

resolve_rsync() {
  local candidate="${AI_RADAR_RSYNC:-}"
  if [[ -z "$candidate" ]]; then
    for guess in /opt/homebrew/bin/rsync /usr/local/bin/rsync; do
      [[ -x "$guess" ]] && { candidate="$guess"; break; }
    done
  fi
  [[ -n "$candidate" && -x "$candidate" ]] || fail \
    "GNU rsync not found. Install it with 'brew install rsync' in an interactive shell
       (brew here is a shell alias carrying proxy settings; a non-interactive shell
       loses them and the install fails with a misleading network error).
       Then re-run, or set AI_RADAR_RSYNC to its path."

  # Version, not just presence: Apple's openrsync also answers to the name
  # 'rsync' and is exactly what must not be used.
  local version proto
  version="$("$candidate" --version 2>/dev/null)" || fail "could not read rsync version from '$candidate'"
  grep -qE '^rsync[[:space:]]+version ' <<<"$version" || fail \
    "'$candidate' is not GNU rsync. Refusing a transfer whose delta/capability semantics are unknown."
  proto="$(grep -oE 'protocol version [0-9]+' <<<"$version" | grep -oE '[0-9]+' || echo 0)"
  (( proto >= 31 )) || fail \
    "'$candidate' speaks rsync protocol $proto; need >= 31.
       Protocol 29 is Apple's openrsync, whose transfers have corrupted FTS5 pages here.
       Refusing rather than silently falling back to a non-delta copy."
  echo "$candidate"
}

supports_zstd_transfer() {
  local rsync_bin=$1 local_version remote_version
  local_version="$("$rsync_bin" --version 2>/dev/null)" || return 1
  grep -qE '^rsync[[:space:]]+version ' <<<"$local_version" || return 1
  grep -qiE 'Compress list:.*(^|[[:space:]])zstd([[:space:]]|$)' <<<"$local_version" || return 1

  # shellcheck disable=SC2086 # SSH_OPTS intentionally follows the script's existing env-string contract.
  remote_version="$(ssh $SSH_OPTS "$SERVER" 'rsync --version' 2>/dev/null)" || return 1
  grep -qE '^rsync[[:space:]]+version ' <<<"$remote_version" || return 1
  grep -qiE 'Compress list:.*(^|[[:space:]])zstd([[:space:]]|$)' <<<"$remote_version"
}

main() {
  [[ -f "$DB" ]] || fail "primary database not found at $DB"

  local rsync_bin; rsync_bin="$(resolve_rsync)"
  log "using $rsync_bin ($("$rsync_bin" --version | sed -n '1p'))"

  # mkdir is the atomic test-and-set available everywhere; two overlapping runs
  # would each snapshot a different instant and race on the same remote path.
  mkdir "$LOCK_DIR" 2>/dev/null || fail "another sync is running (remove $LOCK_DIR if stale)"
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

  "$PYTHON" "$SCRIPT_DIR/logical_delta.py" guard \
    --live-db "$DB" --snapshot "$SNAPSHOT" --replica "$REPLICA" --manifest "$MANIFEST" \
    || fail "live database safety guard refused the configured artifact paths"

  log "taking a consistent snapshot"
  if [[ -e "$SNAPSHOT" || -L "$SNAPSHOT" ]]; then
    rm -- "$SNAPSHOT"
  fi
  "$PYTHON" "$SCRIPT_DIR/snapshot_db.py" \
    --source "$DB" --destination "$SNAPSHOT" \
    || fail "query-only WAL snapshot failed"
  log "snapshot: $(du -h "$SNAPSHOT" | cut -f1)"

  # Verify before shipping: sending a snapshot the server will only reject
  # wastes the whole transfer and leaves the operator reading a remote failure
  # for a fault that was visible here.
  "$PYTHON" - "$SNAPSHOT" <<'PY' || exit 1
import sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
    sys.exit("[sync] ✗ snapshot failed integrity_check; not shipping it")
columns = [row[1] for row in c.execute("PRAGMA table_xinfo('items_fts')") if row[6] == 0]
expected = ["item_id", "title", "content_text", "source_name", "author", "title_zh"]
if columns != expected:
    sys.exit(f"[sync] ✗ snapshot items_fts fields differ: expected={expected!r} actual={columns!r}")
print(f"[sync] snapshot verified: {c.execute('SELECT COUNT(*) FROM items').fetchone()[0]} items")
PY

  log "updating the persistent base-only shipping replica"
  "$PYTHON" "$SCRIPT_DIR/logical_delta.py" sync \
    --live-db "$DB" --snapshot "$SNAPSHOT" --replica "$REPLICA" \
    || fail "base-only shipping replica update failed; nothing was transferred"

  log "building the snapshot-bound FTS oracle"
  "$PYTHON" "$SCRIPT_DIR/build_fts_manifest.py" \
    --snapshot "$SNAPSHOT" --artifact "$REPLICA" --output "$MANIFEST" \
    || fail "FTS baseline manifest failed; nothing was transferred"

  local snapshot_id manifest_sha256 identity
  identity="$("$PYTHON" - "$MANIFEST" <<'PY'
import json, re, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
snapshot_id = payload.get("snapshot_id", "")
manifest_sha256 = payload.get("manifest_sha256", "")
if re.fullmatch(r"[0-9a-f]{64}", snapshot_id) is None:
    raise SystemExit("manifest snapshot_id is not a full lowercase SHA-256")
if re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None:
    raise SystemExit("manifest_sha256 is not a full lowercase SHA-256")
print(snapshot_id, manifest_sha256)
PY
)" || fail "could not read the manifest's artifact identity"
  read -r snapshot_id manifest_sha256 <<<"$identity"

  # Upload under a name the apply step never looks at, then rename on the
  # server. The apply timer fires on its own schedule; if it could see the
  # file rsync is still writing --inplace, it would verify pages that are
  # about to change and could switch traffic onto a half-written snapshot.
  # A same-filesystem mv is atomic, so `incoming` only ever exists complete.
  local upload_name="radar.db.upload"
  local manifest_name="radar.db.fts-manifest.$snapshot_id.json"
  local manifest_upload_name="$manifest_name.upload"

  local rsync_args=(
    --archive --partial --inplace --human-readable --stats
    --no-whole-file --block-size=4096
    -e "ssh $SSH_OPTS"
  )
  if supports_zstd_transfer "$rsync_bin"; then
    rsync_args+=(--compress-choice=zstd --compress-level=3)
    log "transfer mode: 4KiB delta blocks + zstd level 3 (GNU rsync/zstd available on both ends)"
  else
    log "transfer mode: falling back to uncompressed 4KiB delta blocks (GNU rsync/zstd capability not available on both ends)"
  fi

  local db_rsync_args=(
    "${rsync_args[@]}"
    # Delta reference. --copy-dest takes a DIRECTORY, resolved relative to the
    # *destination* directory, and matches files inside it by basename -- so
    # the basis lives at data/basis/radar.db.upload, kept under the same name
    # the transfer uses. (An earlier revision passed a ../-prefixed file path
    # here; it resolved somewhere that never existed and every transfer fell
    # back to full size without saying so.)
    --copy-dest=basis
  )

  if [[ "$DRY_RUN" == "1" ]]; then
    rsync_args+=(--dry-run)
    db_rsync_args+=(--dry-run)
    log "dry run: no bytes will be written on the server"
  fi

  log "transferring the content-addressed FTS manifest sidecar"
  "$rsync_bin" "${rsync_args[@]}" "$MANIFEST" \
    "$SERVER:$REMOTE_DATA/$manifest_upload_name"

  if [[ "$DRY_RUN" != "1" ]]; then
    log "publishing the immutable FTS manifest sidecar"
    # shellcheck disable=SC2086 # SSH_OPTS intentionally follows the existing env-string contract.
    ssh $SSH_OPTS "$SERVER" \
      "set -eu; upload='$REMOTE_DATA/$manifest_upload_name'; final='$REMOTE_DATA/$manifest_name'; \
       mv -n \"\$upload\" \"\$final\"; \
       if [ -e \"\$upload\" ]; then \
         if cmp -s \"\$upload\" \"\$final\"; then rm -f \"\$upload\"; \
         else printf '%s\n' 'manifest identity conflict for $snapshot_id; keeping upload for inspection' >&2; exit 42; fi; \
       fi" \
      || fail "immutable manifest publish failed for $manifest_name; database commit marker was not published"
  fi

  log "transferring the reconciled base-only database artifact"
  "$rsync_bin" "${db_rsync_args[@]}" "$REPLICA" "$SERVER:$REMOTE_DATA/$upload_name"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "dry run complete: local replica/manifest prepared; no remote publish or apply was attempted"
    exit 0
  fi

  log "publishing the database commit marker atomically"
  # shellcheck disable=SC2086 # SSH_OPTS intentionally follows the existing env-string contract.
  ssh $SSH_OPTS "$SERVER" "mv -f '$REMOTE_DATA/$upload_name' '$REMOTE_DATA/radar.db.incoming'" \
    || fail "upload completed but could not be published as radar.db.incoming"

  local previous_journal_generation
  previous_journal_generation="$(remote_journal_generation 2>/dev/null)" \
    || fail "could not checkpoint the remote apply journal before triggering snapshot $snapshot_id"
  if [[ "$previous_journal_generation" != "missing" \
        && ! "$previous_journal_generation" =~ ^[0-9a-f]{64}$ ]]; then
    fail "remote apply journal checkpoint is malformed; refusing an unbound terminal wait"
  fi

  log "handing off to the server's apply step"
  # The server decides whether to accept: it verifies the candidate against its
  # own runtime before any switch, and leaves the active release untouched on
  # rejection. An isolation override must name an independent unit/wrapper that
  # is already bound to its own environment file; this script deliberately does
  # not source or reinterpret the remote service environment.
  # shellcheck disable=SC2086 # SSH_OPTS intentionally follows the existing env-string contract.
  ssh $SSH_OPTS "$SERVER" "$REMOTE_APPLY_TRIGGER" \
    || fail "could not trigger the remote apply (snapshot is staged as radar.db.incoming)"
  wait_for_apply_terminal \
    "$snapshot_id" "$manifest_sha256" "$previous_journal_generation"
  log "done: base-only snapshot $snapshot_id and its FTS oracle were committed by apply"
}

main "$@"
