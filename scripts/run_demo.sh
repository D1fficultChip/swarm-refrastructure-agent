#!/usr/bin/env bash
set -euo pipefail
demo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$demo_root"
default_deps="$demo_root/.python-deps"
if [[ ! -d "$default_deps" && -d /tmp/cluster-reconstruction-deps ]]; then
  default_deps=/tmp/cluster-reconstruction-deps
fi
export PYTHONPATH="${CLUSTER_DEPS_DIR:-$default_deps}:$demo_root${PYTHONPATH:+:$PYTHONPATH}"
demo_backend_pid=''
demo_frontend_pid=''
cleanup() {
  if [[ -n "$demo_frontend_pid" ]]; then kill "$demo_frontend_pid" 2>/dev/null || true; fi
  if [[ -n "$demo_backend_pid" ]]; then kill "$demo_backend_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
if [[ -f "$demo_root/docs/API" ]]; then
  python3 -m scripts.run_phase5_server --credentials-file "$demo_root/docs/API" --port 8000 &
else
  python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 &
fi
demo_backend_pid=$!
node "$demo_root/frontend/node_modules/vite/bin/vite.js" "$demo_root/frontend" --host 127.0.0.1 --port 5173 --strictPort &
demo_frontend_pid=$!
echo 'Demo: http://127.0.0.1:5173  |  API: http://127.0.0.1:8000/docs'
wait -n "$demo_backend_pid" "$demo_frontend_pid"
