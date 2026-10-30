"""Capture owned source/config hashes without reading private credentials or data."""
from datetime import datetime,timezone
from hashlib import sha256
import json
from vibradiag.core.paths import PROJECT_ROOT as ROOT

if __name__=='__main__':
    files=[]
    for directory in ['backend/src','backend/tests','backend/scripts','backend/migrations','frontend/src','scripts']:
        for path in (ROOT/directory).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py','.ts','.vue','.css','.sql','.cjs','.ini'}:
                files.append(path)
    for name in ['backend/pyproject.toml','backend/requirements.lock.txt','backend/alembic.ini','frontend/package.json',
                 'frontend/package-lock.json','frontend/vite.config.ts','frontend/tsconfig.json']:
        path=ROOT/name
        if path.is_file():files.append(path)
    entries={path.relative_to(ROOT).as_posix():sha256(path.read_bytes()).hexdigest() for path in sorted(files)}
    payload={'created_at':datetime.now(timezone.utc).isoformat(),'version_basis':'SHA256 manifest; no Git commit available',
             'migration_head':'0008_legacy_imports','files':entries}
    (ROOT/'experiments/m1-source-manifest.json').write_text(json.dumps(payload,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'source_files':len(entries),'private_files_included':False}))
