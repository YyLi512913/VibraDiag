"""Verify the current local key is absent from owned source/docs/results; never print it."""
import json
import sys
from pathlib import Path
from vibradiag.core.paths import PROJECT_ROOT as ROOT
from vibradiag.core.config import setting
key=setting('DEEPSEEK_API_KEY')
if not key:
    raise SystemExit('No configured key to check')
leaks=[]
from sqlalchemy.engine import make_url
secrets_to_check=[key]
for name in ['VIBRADIAG_DATABASE_URL','VIBRADIAG_MIGRATION_DATABASE_URL',
             'VIBRADIAG_TEST_DATABASE_URL','VIBRADIAG_TEST_MIGRATION_DATABASE_URL']:
    value=setting(name)
    if value and make_url(value).password:
        secrets_to_check.append(make_url(value).password)
for name in ['backend/src','backend/scripts','backend/tests','frontend/src','frontend/dist','scripts','data','docs','experiments']:
    for file in (ROOT/name).rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix in ['.py','.md','.html','.json','.txt','.toml','.cjs','.js','.ts','.vue','.map']:
            content=file.read_text(encoding='utf-8',errors='ignore')
            if any(secret in content for secret in secrets_to_check):
                leaks.append(str(file.relative_to(ROOT)))
ignored='.env' in (ROOT/'.gitignore').read_text().splitlines()
result={'key_configured':True,'local_env_ignored':ignored,'database_passwords_checked':len(secrets_to_check)-1,'source_doc_result_leak_files':leaks}
print(json.dumps(result,ensure_ascii=False))
if leaks or not ignored:
    raise SystemExit(1)
