"""Start the separate local API and frontend; stop only the children we own."""
import importlib.util
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    if importlib.util.find_spec('vibradiag') is None:
        raise SystemExit('Install backend first: uv pip install --python .venv/Scripts/python.exe --no-deps -e backend')
    node=shutil.which('node')
    vite=ROOT/'frontend/node_modules/vite/bin/vite.js'
    if not node or not vite.exists():
        raise SystemExit('Install Node and run npm ci in frontend first')
    children=[]
    code=0
    try:
        children.append(subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port','8010'],cwd=ROOT/'backend'))
        children.append(subprocess.Popen([node,str(vite),'--host','127.0.0.1','--port','5173','--strictPort'],cwd=ROOT/'frontend'))
        print('Frontend: http://127.0.0.1:5173 | API docs: http://127.0.0.1:8010/docs',flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        code=next((child.returncode for child in children if child.returncode),1)
    except KeyboardInterrupt:
        print('\nStopping development services…',flush=True)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill();child.wait()
    return code

if __name__=='__main__':
    raise SystemExit(main())
