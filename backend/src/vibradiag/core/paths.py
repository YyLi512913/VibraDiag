import os
from pathlib import Path

BACKEND_ROOT=Path(__file__).resolve().parents[3]
PROJECT_ROOT=BACKEND_ROOT.parent
RUNTIME_ROOT=Path(os.environ.get('VIBRADIAG_RUNTIME_DIR',str(PROJECT_ROOT/'.runtime'))).resolve()
KNOWLEDGE_CORPUS=PROJECT_ROOT/'data/knowledge/development-notes.json'
