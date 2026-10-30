"""Explicitly import prototype originals/metadata. Defaults to dry-run; old results stay legacy."""
import argparse
import json
from pathlib import Path
from uuid import UUID
from vibradiag.services.legacy_import_service import import_directory, local_actor

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--actor',type=UUID,required=True)
    parser.add_argument('--batch',required=True)
    parser.add_argument('--manifest',type=Path,help='Optional JSON object mapping legacy IDs to explicit metadata overrides')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        overrides=json.loads(args.manifest.read_text(encoding='utf8')) if args.manifest else {}
        with local_actor(args.actor) as principal:
            report=import_directory(args.source,principal,args.batch,apply=args.apply,overrides=overrides)
    except Exception:
        raise SystemExit('Legacy import failed; check source, actor, manifest and private configuration (credentials hidden)') from None
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(1 if report['rejected_count'] else 0)
