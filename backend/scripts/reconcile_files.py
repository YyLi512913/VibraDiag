"""Trusted local maintenance entry: report only, never delete data."""
import json
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.file_reconciliation import reconcile_files

if __name__=='__main__':
    try:
        with transaction() as connection:
            report=reconcile_files(connection)
    except Exception:
        raise SystemExit('Reconciliation failed; check private database/storage configuration') from None
    print(json.dumps(report,ensure_ascii=False,indent=2))
