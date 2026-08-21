#!/usr/bin/env bash
# Redacted `docker compose logs` for wechat2rss.
#
# Use this instead of `docker compose logs`. The service prints RSS_TOKEN on
# every startup ("Token: ..."), and the GIN access log records it again in the
# `k=` query parameter of every authenticated request. Dumping raw logs into a
# terminal, a transcript, or an issue therefore discloses the admin credential,
# and disclosure cannot be undone — only the token can be rotated.
#
# Usage: ./logs.sh [any docker compose logs flags]   e.g. ./logs.sh --since 10m -f
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
docker compose logs "$@" 2>&1 | sed -E \
  -e 's/(msg="Token: )[^"]*"/\1<redacted>"/g' \
  -e 's/([?&]k=)[^[:space:]"&]*/\1<redacted>/g'
# The token charset is not constrained anywhere, so redact to the end of the
# value rather than to a guessed alphabet: `k=abc+def/ghi=` against a
# `[A-Za-z0-9_.~-]+` pattern leaves `+def/ghi=` in the terminal, and a leaked
# credential cannot be un-disclosed — only rotated. Stop at `&` so the query
# fields after the token survive: they are what the log is read for.
