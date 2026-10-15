"""Validate the sanitized transport-verification record and render it as submission evidence (offline)."""
import argparse
import sys
from pathlib import Path

from simulator.production.transport_report import EvidenceError, render_file

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--record', type=Path, default=Path('docs/submission/transport-verification.json'))
parser.add_argument('--output', type=Path, default=Path('docs/submission/transport-verification.md'))
args = parser.parse_args()
try:
    report = render_file(args.record)
except (OSError, ValueError, KeyError, TypeError) as error:
    detail = str(error) if isinstance(error, EvidenceError) else type(error).__name__
    sys.exit(f'Evidence not rendered: {detail}')
args.output.write_text(report)
print(f'Wrote {args.output}')
