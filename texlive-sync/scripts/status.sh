#!/bin/bash
# Status for texlive mass-run. Override with TEXLIVE_SYNC_LOGDIR.
LOGDIR="${TEXLIVE_SYNC_LOGDIR:-$HOME/texlive-sync-logs}"
PIDFILE="$LOGDIR/mass-run.pid"
PID=$(cat "$PIDFILE" 2>/dev/null)
echo "=== $(date) ==="
echo "LOGDIR=$LOGDIR"
if [ -n "$PID" ] && ps -p "$PID" >/dev/null 2>&1; then
  echo "mass-run: RUNNING pid=$PID etime=$(ps -p "$PID" -o etime=)"
else
  echo "mass-run: NOT RUNNING (last pid=$PID)"
fi
python3 - <<PY
import json, os
from pathlib import Path
logdir = Path(os.environ.get("TEXLIVE_SYNC_LOGDIR", Path.home() / "texlive-sync-logs"))
st_path = logdir / "mass-state.json"
st = json.loads(st_path.read_text()) if st_path.is_file() else {}
print(f"applied={len(st.get('applied') or [])} built={len(st.get('built') or [])}")
print(f"apply_failed={len(st.get('apply_failed') or {})} build_failed={len(st.get('build_failed') or {})}")
if st.get("apply_failed"):
    print("apply fails sample:", list(st["apply_failed"])[:5])
if st.get("build_failed"):
    print("build fails sample:", list(st["build_failed"])[:5])
PY
echo "--- tail apply ---"
tail -3 "$LOGDIR/apply.log" 2>/dev/null
echo "--- tail build ---"
tail -3 "$LOGDIR/build.log" 2>/dev/null
echo "--- tail out ---"
tail -3 "$LOGDIR/mass-run.out" 2>/dev/null
