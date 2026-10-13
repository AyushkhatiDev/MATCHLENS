import asyncio,json,argparse
from pathlib import Path
from simulator.production.cloud import verify
parser=argparse.ArgumentParser();parser.add_argument('--services',nargs='+',choices=['foundry','eventhub','fabric'],default=['foundry','eventhub','fabric']);args=parser.parse_args()
report=asyncio.run(verify(args.services));path=Path('docs/submission/cloud-verification.json')
previous=json.loads(path.read_text()) if path.exists() else {}
previous.update(report);path.write_text(json.dumps(previous,indent=2)+'\n')
print(json.dumps({name:{k:v for k,v in result.items() if k not in ('result','traces','workflow_results')} for name,result in report.items()}))
raise SystemExit(1 if any(r['status']=='failed' for r in report.values()) else 0)
