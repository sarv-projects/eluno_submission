#!/usr/bin/env bash
# one command demo, bill -> refund -> day end, kills the mock when it exits
# PORT and DB can be overridden, deletes nothing outside its own db file
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PORT="${PORT:-18787}"
DB="${DB:-$ROOT/pos_demo.sqlite3}"
BASE="http://127.0.0.1:${PORT}"
KEY="ELUNO-DEMO-$(date +%s)"
LOG="${TMPDIR:-/tmp}/eluno-pos-mock-${PORT}.log"
python3 starter-kit/mock-apis/mock_server.py --port "$PORT" --no-flake >"$LOG" 2>&1 &
MOCK_PID=$!
cleanup() { kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
ready=0
for _ in $(seq 1 50); do
  if python3 -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1],timeout=.2).read()' "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
  sleep 0.1
done
if [ "$ready" != 1 ]; then echo "Mock did not start; log: $LOG" >&2; exit 1; fi
common=(--base-url "$BASE" --db "$DB")
checkout_json="$(python3 -m pos.cli "${common[@]}" checkout --phone +919000000005 --store HSR --staff S105 --pair 91000003:91000083 --channel walk_in --payment upi --checkout-key "$KEY")"
printf '%s\n' "$checkout_json"
INVOICE="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["invoice_no"])' <<<"$checkout_json")"
python3 -m pos.cli "${common[@]}" refund --invoice "$INVOICE" --store HSR --reason 'Demo customer return' --reason-code CHANGED_MIND --refund-key "${KEY}-REFUND"
python3 -m pos.cli "${common[@]}" report --store HSR --date "$(date +%F)"
