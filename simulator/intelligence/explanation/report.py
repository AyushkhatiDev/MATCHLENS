"""Presentation and filesystem adapters, kept outside the explanation engine."""
from pathlib import Path
from .models import ExplanationReport

def write_explanations_json(report: ExplanationReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path

def print_explanations_report(report: ExplanationReport) -> None:
    ledger = {e.evidence_id: e for e in report.evidence}
    print("\nEXPLANATIONS\n------------")
    for e in report.explanations:
        print(f"\n{int(e.timestamp//60):02d}:{int(e.timestamp%60):02d}  "
              f"{e.explanation_type.value}  {e.significance_tier.value}")
        print(e.what_happened)
        print(e.what_changed)
        print(f"WHY IT MATTERS:\n{e.why_it_matters}")
        print(f"Confidence: {e.confidence:.2f}\nEvidence:")
        for eid in e.evidence_ids:
            evidence = ledger[eid]
            print(f"- {eid}: {evidence.metric_name}={evidence.value}; "
                  f"sources={', '.join(evidence.source_paths + evidence.source_event_ids)}")
