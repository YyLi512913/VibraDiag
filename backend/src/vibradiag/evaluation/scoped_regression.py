"""A new frozen observation of v2 on known development cases, preserving v1 failures."""
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.evaluation.feedback_benchmark import run as feedback_run
PROTOCOL=BACKEND_ROOT/'config/agent-scoped-regression-v1.json'


def run(private,progress=lambda value:None):
    return feedback_run(private,progress,numeric_mode='model_v2',protocol_path=PROTOCOL,evaluated_strategies=('agent',))
