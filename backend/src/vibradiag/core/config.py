import os
from .paths import BACKEND_ROOT

def setting(name:str,default:str='')->str:
    """Minimal local settings reader. Environment takes precedence; no interpolation."""
    if name in os.environ:
        return os.environ[name]
    file=BACKEND_ROOT/'.env'
    if file.exists():
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key,value=line.split('=',1)
            if key.strip()==name:
                return value.strip().strip('\"').strip("'")
    return default

def external_enabled():
    return setting('VIBRADIAG_ALLOW_EXTERNAL_LLM','false').lower()=='true'

def default_model():
    if setting('VIBRADIAG_LLM_PROVIDER','deepseek')=='deepseek':
        return 'deepseek/'+setting('VIBRADIAG_DEEPSEEK_MODEL','deepseek-flash')
    return 'qwen3.5:2b'
