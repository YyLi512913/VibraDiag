"""Audit the saved first observation; never call a provider or rewrite the original."""
from hashlib import sha256
import json
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.method_comparison_benchmark import PROTOCOL
from vibradiag.evaluation.method_comparison_review import review


def main():
    source=PROJECT_ROOT/'experiments/m3-t05c2b-method-comparison.json'
    output=PROJECT_ROOT/'experiments/m3-t05c2b-outcome-review.json'
    receipt=json.loads(source.read_text(encoding='utf8'))
    if sha256(PROTOCOL.read_bytes()).hexdigest()!=receipt['protocol_sha256']:raise RuntimeError('Original protocol hash required')
    result=review(receipt,json.loads(PROTOCOL.read_text(encoding='utf8')))
    result.update(original_receipt_sha256=sha256(source.read_bytes()).hexdigest(),original_protocol_sha256=receipt['protocol_sha256'],
                  audit_source_sha256=sha256((PROJECT_ROOT/'backend/src/vibradiag/evaluation/method_comparison_review.py').read_bytes()).hexdigest())
    with output.open('x',encoding='utf8',newline='\n') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(summary=result['summary'],all_cases_met_expectations=result['all_cases_met_expectations'],model_calls_added=0)))


if __name__=='__main__':main()
