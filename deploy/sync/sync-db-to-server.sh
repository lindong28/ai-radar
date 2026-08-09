#!/usr/bin/env bash
# Push a consistent snapshot of the primary database to the read-only replica.
#
# Runs on the Mac, which owns the primary. The server never pulls: only the
# writer knows when a consistent snapshot exists.
#
# Three things here are deliberate and were each learned the expensive way:
#
#   .backup, not a file copy   Copying a live database while the pipeline writes
#                              produces a torn file. `VACUUM INTO` was tried and
#                              rejected -- it corrupts the FTS5 inverted index.
#
#   GNU rsync, or nothing      macOS ships Apple's openrsync (protocol 29). Its
#                              negotiation with the server's rsync 3.x has been
#                              observed corrupting FTS5 bytes. The previous
#                              workaround was to fall back to scp, which has no
#                              delta algorithm at all -- that is how incremental
#                              transfer was lost without anyone deciding to lose
#                              it. This script fails instead of falling back.
#
#   Delta against basis        rsync computes a delta against a reference file.
#                              The basis is the last *accepted* snapshot, kept
#                              untouched by the apply step, so the reference is
#                              a file the server actually holds byte-for-byte.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DB="${AI_RADAR_SYNC_DB:-$REPO_ROOT/data/radar.db}"
SERVER="${AI_RADAR_SYNC_SERVER:-tencent-webserver-china}"
REMOTE_DATA="${AI_RADAR_SYNC_REMOTE_DATA:-ai-radar/data}"
SNAPSHOT="${AI_RADAR_SYNC_SNAPSHOT:-$REPO_ROOT/data/radar.db.snapshot}"
LOCK_DIR="${AI_RADAR_SYNC_LOCK:-$REPO_ROOT/data/.sync.lock}"
DRY_RUN="${AI_RADAR_SYNC_DRY_RUN:-0}"

# Bypass any local proxy: the tunnel does not route to this host and the
# resulting failure reads like a network outage rather than a proxy problem.
SSH_OPTS="${AI_RADAR_SYNC_SSH_OPTS:--o ProxyCommand=none}"

log()  { printf '[sync] %s\n' "$*"; }
fail() { printf '[sync] ✗ %s\n' "$*" >&2; exit 1; }

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
  local proto
  proto="$("$candidate" --version 2>/dev/null | grep -oE 'protocol version [0-9]+' | grep -oE '[0-9]+' || echo 0)"
  (( proto >= 31 )) || fail \
    "'$candidate' speaks rsync protocol $proto; need >= 31.
       Protocol 29 is Apple's openrsync, whose transfers have corrupted FTS5 pages here.
       Refusing rather than silently falling back to a non-delta copy."
  echo "$candidate"
}

main() {
  [[ -f "$DB" ]] || fail "primary database not found at $DB"

  local rsync_bin; rsync_bin="$(resolve_rsync)"
  log "using $rsync_bin ($("$rsync_bin" --version | head -1))"

  # mkdir is the atomic test-and-set available everywhere; two overlapping runs
  # would each snapshot a different instant and race on the same remote path.
  mkdir "$LOCK_DIR" 2>/dev/null || fail "another sync is running (remove $LOCK_DIR if stale)"
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

  log "taking a consistent snapshot"
  rm -f "$SNAPSHOT"
  sqlite3 "$DB" ".backup '$SNAPSHOT'" || fail "snapshot failed"
  log "snapshot: $(du -h "$SNAPSHOT" | cut -f1)"

  # Verify before shipping: sending a snapshot the server will only reject
  # wastes the whole transfer and leaves the operator reading a remote failure
  # for a fault that was visible here.
  "${AI_RADAR_PYTHON:-$REPO_ROOT/.venv/bin/python3}" - "$SNAPSHOT" <<'PY' || exit 1
import sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(db)
if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    sys.exit("[sync] ✗ snapshot failed integrity_check; not shipping it")
try:
    c.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
except sqlite3.DatabaseError as exc:
    sys.exit(f"[sync] ✗ snapshot failed fts5 integrity-check: {exc}")
print(f"[sync] snapshot verified: {c.execute('SELECT COUNT(*) FROM items').fetchone()[0]} items")
PY

  # Upload under a name the apply step never looks at, then rename on the
  # server. The apply timer fires on its own schedule; if it could see the
  # file rsync is still writing --inplace, it would verify pages that are
  # about to change and could switch traffic onto a half-written snapshot.
  # A same-filesystem mv is atomic, so `incoming` only ever exists complete.
  local upload_name="radar.db.upload"

  local rsync_args=(
    --archive --partial --inplace --human-readable --stats
    # Delta reference. --copy-dest takes a DIRECTORY, resolved relative to the
    # *destination* directory, and matches files inside it by basename -- so
    # the basis lives at data/basis/radar.db.upload, kept under the same name
    # the transfer uses. (An earlier revision passed a ../-prefixed file path
    # here; it resolved somewhere that never existed and every transfer fell
    # back to full size without saying so.)
    --copy-dest=basis
    -e "ssh $SSH_OPTS"
  )
  # No --compress: the payload is a mostly-incompressible page image, and
  # compression is where the openrsync interop corruption showed up.

  if [[ "$DRY_RUN" == "1" ]]; then
    rsync_args+=(--dry-run)
    log "dry run: no bytes will be written on the server"
  fi

  log "transferring"
  "$rsync_bin" "${rsync_args[@]}" "$SNAPSHOT" "$SERVER:$REMOTE_DATA/$upload_name"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "dry run complete"
    exit 0
  fi

  log "publishing the upload atomically"
  ssh $SSH_OPTS "$SERVER" "mv -f '$REMOTE_DATA/$upload_name' '$REMOTE_DATA/radar.db.incoming'" \
    || fail "upload completed but could not be published as radar.db.incoming"

  log "handing off to the server's apply step"
  # The server decides whether to accept: it verifies the candidate against its
  # own runtime before any switch, and leaves the active release untouched on
  # rejection.
  ssh $SSH_OPTS "$SERVER" 'sudo systemctl start ai-radar-db-apply.service --no-block' \
    || fail "could not trigger the remote apply (snapshot is staged as radar.db.incoming)"
  log "done; follow with: ssh $SERVER journalctl -u ai-radar-db-apply -f"
}

main "$@"
