"""Run the frontend's typecheck/build commands and retain a source-bound receipt."""
from hashlib import sha256
import json
import shutil
import subprocess
from vibradiag.core.paths import PROJECT_ROOT


def main():
    frontend=PROJECT_ROOT/'frontend';node=shutil.which('node')
    if not node:raise RuntimeError('Node.js is required')
    commands=[[node,str(frontend/'node_modules/vue-tsc/bin/vue-tsc.js'),'--noEmit'],
              [node,str(frontend/'node_modules/vite/bin/vite.js'),'build']]
    results=[]
    for command in commands:
        result=subprocess.run(command,cwd=frontend,capture_output=True,encoding='utf8',errors='replace',timeout=120,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        results.append({'command':['node',str(command[1]).replace(str(frontend),'frontend'),*command[2:]],
            'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        if result.returncode:raise RuntimeError('Frontend validation failed')
    paths=list((frontend/'src').rglob('*'))+[frontend/name for name in
        ('package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html')]
    paths=[p for p in paths if p.is_file()]
    receipt={'passed':True,'commands':results,'source_sha256':{p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}}
    (PROJECT_ROOT/'experiments/m2-frontend-build.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':True,'typecheck':0,'build':0,'source_entries':len(paths)}))


if __name__=='__main__':main()
