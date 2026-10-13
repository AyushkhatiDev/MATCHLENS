import asyncio,json,argparse
from pathlib import Path
from simulator.production.evaluation import evaluate
parser=argparse.ArgumentParser();parser.add_argument('--output',default='docs/submission/ai-evaluation.json');parser.add_argument('--cases',type=int,default=50);args=parser.parse_args()
report=asyncio.run(evaluate(args.cases));path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('results','traces')}))
raise SystemExit(0 if report['grounded']==args.cases and all(c['rejected'] for c in report['negative_controls']) and report['specialist_failure']['status']=='PARTIAL' else 1)
