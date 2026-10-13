"""Confidence in explanation logic, never a probability of football causation."""
from .models import TraceEvidence

def confidence(evidence: list[TraceEvidence], *, complete_sequence: bool, full_baseline: bool,
               state_clear: bool, conflicts: bool = False, sparse: bool = False) -> tuple[float, list[str]]:
    # Correlated projections from the same metric family count only once.
    families = set()
    for e in evidence:
        name = e.metric_name
        family = ("pressure" if "pressure" in name else "territory" if "tilt" in name
                  else "possession" if name in ("turnovers", "possession_pct", "possession_sequence")
                  else "state" if "state" in name else name)
        families.add(family)
    score = .35 + .08 * min(len(families), 4)
    score += .1 * complete_sequence + .1 * full_baseline + .08 * state_clear
    caveats = []
    if not complete_sequence:
        caveats.append("A complete source-event sequence is not available for this interpretation.")
    if not full_baseline:
        score -= .1
        caveats.append("The baseline is missing or shorter than the nominal rolling window.")
    if len(families) < 2:
        caveats.append("Only one metric family supports this interpretation.")
    if sparse:
        score -= .15
        caveats.append("The recent rolling window contains sparse activity; interpretation is less stable.")
    if conflicts:
        score -= .15
        caveats.append("Supporting metrics move in different directions; no single-team control conclusion is made.")
    return round(max(0, min(.95, score)), 2), caveats
