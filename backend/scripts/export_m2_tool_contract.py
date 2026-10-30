"""Export the existing public request schema and bounded M3 tool handoff."""
import json
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.services.tool_contract import m2_tool_contract


def main():
    destination=PROJECT_ROOT/'docs/contracts/m2-tool-contract.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(m2_tool_contract(),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'tools':4,'destination':destination.relative_to(PROJECT_ROOT).as_posix(),'diagnostic_release_approved':False}))


if __name__=='__main__':main()
