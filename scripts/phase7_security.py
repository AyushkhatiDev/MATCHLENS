import json
from pathlib import Path
from simulator.production.security import scan
report=scan(Path.cwd());Path('docs/submission/security-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if report['status']=='pass' else 1)
