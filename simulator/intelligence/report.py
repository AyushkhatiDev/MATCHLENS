"""Intelligence report output — console report and JSON export."""

from __future__ import annotations

import json
from pathlib import Path

from simulator.intelligence.models import MatchIntelligence, SignificanceTier


def write_intelligence_json(intel: MatchIntelligence, path: str | Path) -> None:
    """Write intelligence to a JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = intel.model_dump(mode="json")
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def print_intelligence_report(intel: MatchIntelligence) -> None:
    """Print a concise terminal report of match intelligence."""
    sep = "=" * 56
    thin = "-" * 56
    print()
    print(sep)
    print("  MATCHLENS — MATCH INTELLIGENCE  (Phase 2)")
    print(sep)
    print(f"  Match: {intel.home_team.team_name} vs {intel.away_team.team_name}")
    print(f"  Match ID: {intel.match_id}")
    print(f"  Events: {intel.summary.total_events}")
    print(thin)

    # Possession
    print()
    print("  POSSESSION")
    print(f"  {intel.home_team.team_name:>25s}: {intel.home_team.possession.possession_pct:.1f}%")
    print(f"  {intel.away_team.team_name:>25s}: {intel.away_team.possession.possession_pct:.1f}%")

    # Pressure Index
    print()
    print("  MATCHLENS PRESSURE INDEX (0–100)")
    print(f"  {intel.home_team.team_name:>25s}: {intel.home_team.pressure_index:.0f}")
    print(f"  {intel.away_team.team_name:>25s}: {intel.away_team.pressure_index:.0f}")

    # Field Tilt
    print()
    print("  MATCHLENS FIELD TILT")
    print(f"  {intel.home_team.team_name:>25s}: {intel.home_team.field_tilt_pct:.1f}%")
    print(f"  {intel.away_team.team_name:>25s}: {intel.away_team.field_tilt_pct:.1f}%")

    # Passing
    print()
    print("  PASSING")
    h, a = intel.home_team, intel.away_team
    print(f"  {'':>25s}  {'Home':>8s}  {'Away':>8s}")
    print(f"  {'Pass Accuracy':>25s}  {h.passing.accuracy:>7.1f}%  {a.passing.accuracy:>7.1f}%")
    print(f"  {'Progressive Passes':>25s}  {h.passing.progressive:>8d}  {a.passing.progressive:>8d}")
    print(f"  {'Pressured Accuracy':>25s}  {h.passing.pressured_accuracy:>7.1f}%  {a.passing.pressured_accuracy:>7.1f}%")

    # Shots
    print()
    print("  SHOTS")
    print(f"  {'Shots':>25s}  {h.attacking.shots:>8d}  {a.attacking.shots:>8d}")
    print(f"  {'On Target':>25s}  {h.attacking.shots_on_target:>8d}  {a.attacking.shots_on_target:>8d}")
    print(f"  {'Goals':>25s}  {h.attacking.goals:>8d}  {a.attacking.goals:>8d}")

    # Defensive
    print()
    print("  DEFENSIVE")
    print(f"  {'Tackles':>25s}  {h.defensive.tackles:>8d}  {a.defensive.tackles:>8d}")
    print(f"  {'Interceptions':>25s}  {h.defensive.interceptions:>8d}  {a.defensive.interceptions:>8d}")
    print(f"  {'Recoveries':>25s}  {h.defensive.recoveries:>8d}  {a.defensive.recoveries:>8d}")

    # Possession sequences
    print()
    pi = intel.possession_intelligence
    print(f"  POSSESSION SEQUENCES: {pi.total_sequences}")
    print(f"  Avg duration: {pi.avg_duration_s:.1f}s  |  Median: {pi.median_duration_s:.1f}s  |  Longest: {pi.longest_duration_s:.1f}s")
    print(f"  Ending in shot: {pi.sequences_ending_shot}  |  Ending in goal: {pi.sequences_ending_goal}")
    print(f"  With progression: {pi.sequences_with_progression}  |  Reaching att third: {pi.sequences_reaching_attacking_third}")

    # State transitions
    if intel.state_transitions:
        print()
        print("  STATE TRANSITIONS")
        for t in intel.state_transitions:
            print(f"    {t.minute:>3d}' {t.previous_state.value} → {t.new_state.value} (mag={t.magnitude:.2f})")

    # Significant moments
    notable = [m for m in intel.detected_moments
               if m.tier in (SignificanceTier.NOTABLE, SignificanceTier.HIGH, SignificanceTier.CRITICAL)]
    if notable:
        print()
        print("  SIGNIFICANT MOMENTS")
        for m in notable[:20]:  # cap display
            tag = {"CRITICAL": "🔴", "HIGH": "🟠", "NOTABLE": "🟡"}.get(m.tier.value, "")
            print(f"    {tag} {m.minute:>3d}' {m.moment_type.value:20s} score={m.significance_score:.0f} {m.tier.value}")

    # Evidence summary
    print()
    print(f"  EVIDENCE PACKETS: {len(intel.evidence)}")
    print(f"  EXPLANATION CONTEXTS: {len(intel.explanation_contexts)}")
    print(f"  TIMELINE ENTRIES: {len(intel.timeline)}")
    print(f"  ROLLING WINDOWS: {len(intel.rolling_windows)}")

    print(sep)
    print()
