"""Start the local API and Vite UI with the current Python on any desktop OS."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    vite = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if node is None:
        print("Node.js was not found. Install Node.js 22.12+ and reopen the terminal.", file=sys.stderr)
        return 1
    if not vite.is_file():
        print("Frontend dependencies are missing. Run 'npm ci' in frontend/.", file=sys.stderr)
        return 1

    env = os.environ.copy()
    python_paths = [str(ROOT)]
    if env.get("CLUSTER_DEPS_DIR"):
        python_paths.insert(0, env["CLUSTER_DEPS_DIR"])
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    credentials = ROOT / "docs" / "API"
    backend = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"]
    if credentials.is_file():
        backend = [sys.executable, "-m", "scripts.run_phase5_server", "--credentials-file", str(credentials), "--port", "8000"]
    frontend = [node, str(vite), str(ROOT / "frontend"), "--host", "127.0.0.1", "--port", "5173", "--strictPort"]

    processes = []
    try:
        processes.append(subprocess.Popen(backend, cwd=ROOT, env=env))
        processes.append(subprocess.Popen(frontend, cwd=ROOT, env=env))
        print("Demo: http://127.0.0.1:5173  |  API: http://127.0.0.1:8000/docs", flush=True)
        while True:
            for process in processes:
                status = process.poll()
                if status is not None:
                    return status or 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
