"""Record executed M3-T01 contract checks, without claiming a functioning formal Agent."""
from hashlib import sha256
from importlib.metadata import version
import json
import platform
from xml.etree import ElementTree
from export_m3_agent_contract import contract
from vibradiag.core.paths import PROJECT_ROOT


def main():
    contract_path=PROJECT_ROOT/'docs/contracts/m3-agent-contract.json'
    assert json.loads(contract_path.read_text(encoding='utf8'))==contract()
    suite=ElementTree.parse(PROJECT_ROOT/'experiments/m3-t01-tests.xml').getroot().find('testsuite')
    assert all(int(suite.attrib[name])==0 for name in ('failures','errors','skipped'))
    cases=[item for item in suite.findall('testcase') if item.attrib['classname']=='tests.unit.test_formal_agent_policy']
    assert len(cases)==21
    paths=['backend/src/vibradiag/agents/formal_contracts.py','backend/src/vibradiag/agents/formal_policy.py',
        'backend/tests/unit/test_formal_agent_policy.py','backend/scripts/export_m3_agent_contract.py',
        'backend/scripts/verify_m3_t01.py','backend/requirements.lock.txt',
        'docs/07-m3-agent-design.md','docs/contracts/m2-tool-contract.json','docs/contracts/m3-agent-contract.json',
        'experiments/m3-t01-tests.xml']
    report={'passed':True,'scope':'M3-T01 detailed design, DTOs and pure policy checks only',
        'full_backend_tests':{key:suite.attrib[key] for key in ('tests','failures','errors','skipped','time')},
        'new_contract_policy_tests':len(cases),'python':platform.python_version(),'langgraph_installed':version('langgraph'),
        'formal_task_api_implemented':False,'formal_agent_graph_executed':False,'database_migration_added':False,
        'worker_or_durable_recovery_implemented':False,'model_called':False,'published_knowledge_integrated':False,
        'semantic_claim_verification_implemented':False,'formal_diagnostic_release_approved':False,
        'source_sha256':{name:sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() for name in paths}}
    target=PROJECT_ROOT/'experiments/m3-t01-contracts.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({key:report[key] for key in ('passed','full_backend_tests','new_contract_policy_tests','formal_task_api_implemented','model_called')}))


if __name__=='__main__':main()
