import json
import time
import pytest
from vibradiag.agents import graph as agent

@pytest.mark.parametrize('text,reference',[
    ('RMS存在观测值','EV-999'),
    ('检测到转速为1480rpm','EV-001'),
])
def test_invalid_reference_and_false_parameter_provenance_blocked(monkeypatch,text,reference):
    payload={'status':'analysis_only','summary':'开发测试','observations':[{'text':text,'evidence_ids':[reference]}],
             'missing_information':[],'next_steps':[]}
    # Unit test only: fake model response deliberately violates a publishing constraint.
    monkeypatch.setattr(agent,'chat',lambda *a,**k:({'content':json.dumps(payload)},{}))
    state={'model':'unit-test-only','metadata':{'rpm':1480},'messages':[{}, {'content':'只分析频谱'}],
           'evidence':{'EV-001':{'kind':'signal','features':{'rms':1}}},'metrics':[],'trace':[],
           'deadline':time.perf_counter()+10}
    result=agent.finalize(state)
    assert result['draft'] is None
    assert result['error']

def test_budget_exhaustion_stops_call():
    with pytest.raises(TimeoutError):
        agent.remaining({'deadline':time.perf_counter()-1})
