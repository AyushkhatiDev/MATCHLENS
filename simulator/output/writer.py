"""Output serialisation and match summary reporting.

Produces:
- JSONL event stream  (one event per line)
- JSON match metadata
- Console summary report
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulator.models.events import MatchEvent, EventType
from simulator.models.match import Match
from simulator.engine.match_engine import MatchState


def _event_to_dict(event: MatchEvent) -> dict[str, Any]:
    """Serialise a Pydantic event to a JSON-ready dict."""
    return event.model_dump(mode="json")


# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------

def write_events_jsonl(events: list[MatchEvent], path: str | Path) -> Path:
    """Write the event stream to a JSONL file (one JSON object per line)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(_event_to_dict(ev), separators=(",", ":")) + "\n")
    return p


# ---------------------------------------------------------------------------
# Match metadata writer
# ---------------------------------------------------------------------------

def write_match_metadata(match: Match, state: MatchState, path: str | Path) -> Path:
    """Write match metadata (teams, score, etc.) as a JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "match_id": match.match_id,
        "competition": match.competition_name,
        "season": match.season,
        "matchday": match.matchday,
        "date": match.date.isoformat(),
        "venue": match.venue,
        "synthetic_data": match.synthetic_data,
        "random_seed": match.random_seed,
        "duration_minutes": match.duration_minutes,
        "home_team": {
            "team_id": match.home_team.team_id,
            "name": match.home_team.name,
            "short_name": match.home_team.short_name,
            "formation": match.home_team.formation.value,
            "playing_style": match.home_team.playing_style.value,
            "players": [
                {
                    "player_id": p.player_id,
                    "name": p.name,
                    "shirt_number": p.shirt_number,
                    "position": p.position.value,
                    "preferred_foot": p.preferred_foot.value,
                    "role": p.role,
                }
                for p in match.home_team.players
            ],
        },
        "away_team": {
            "team_id": match.away_team.team_id,
            "name": match.away_team.name,
            "short_name": match.away_team.short_name,
            "formation": match.away_team.formation.value,
            "playing_style": match.away_team.playing_style.value,
            "players": [
                {
                    "player_id": p.player_id,
                    "name": p.name,
                    "shirt_number": p.shirt_number,
                    "position": p.position.value,
                    "preferred_foot": p.preferred_foot.value,
                    "role": p.role,
                }
                for p in match.away_team.players
            ],
        },
        "final_score": {
            "home": state.home_score,
            "away": state.away_score,
        },
    }

    with p.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return p


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(state: MatchState) -> str:
    """Print and return a compact match summary report."""
    m = state.match
    h = m.home_team
    a = m.away_team

    home_poss = state.possession.possession_pct(h.team_id) * 100
    away_poss = 100.0 - home_poss

    home_pass_rate = (
        state.home_pass_success / state.home_passes * 100
        if state.home_passes > 0 else 0
    )
    away_pass_rate = (
        state.away_pass_success / state.away_passes * 100
        if state.away_passes > 0 else 0
    )

    avg_pass_dist = (
        state.total_pass_distance / state.total_passes
        if state.total_passes > 0 else 0
    )
    avg_poss_dur = (
        state.total_possession_duration / state.total_possession_sequences
        if state.total_possession_sequences > 0 else 0
    )

    total_events = len(state.events)

    lines: list[str] = [
        "",
        "=" * 56,
        "  MATCHLENS — MATCH SUMMARY  (synthetic data)",
        "=" * 56,
        f"  {h.name} ({h.short_name}) vs {a.name} ({a.short_name})",
        f"  Venue: {m.venue}",
        f"  Formation: {h.formation.value} vs {a.formation.value}",
        f"  Style: {h.playing_style.value} vs {a.playing_style.value}",
        f"  Seed: {m.random_seed}",
        "-" * 56,
        f"  Final Score: {state.home_score} – {state.away_score}",
        f"  Duration: {m.duration_minutes} minutes",
        f"  Total Events: {total_events:,}",
        "-" * 56,
        f"  {'Statistic':<28} {'Home':>10} {'Away':>10}",
        f"  {'-'*28} {'-'*10} {'-'*10}",
        f"  {'Possession %':<28} {home_poss:>9.1f}% {away_poss:>9.1f}%",
        f"  {'Passes':<28} {state.home_passes:>10} {state.away_passes:>10}",
        f"  {'Pass Success Rate':<28} {home_pass_rate:>9.1f}% {away_pass_rate:>9.1f}%",
        f"  {'Progressive Passes':<28} {state.home_progressive_passes:>10} {state.away_progressive_passes:>10}",
        f"  {'Shots':<28} {state.home_shots:>10} {state.away_shots:>10}",
        f"  {'Shots on Target':<28} {state.home_shots_on_target:>10} {state.away_shots_on_target:>10}",
        f"  {'Goals':<28} {state.home_score:>10} {state.away_score:>10}",
        f"  {'Pressure Events':<28} {state.home_pressure:>10} {state.away_pressure:>10}",
        f"  {'Tackles':<28} {state.home_tackles:>10} {state.away_tackles:>10}",
        f"  {'Interceptions':<28} {state.home_interceptions:>10} {state.away_interceptions:>10}",
        f"  {'Carries':<28} {state.home_carries:>10} {state.away_carries:>10}",
        f"  {'Crosses':<28} {state.home_crosses:>10} {state.away_crosses:>10}",
        f"  {'Fouls':<28} {state.home_fouls:>10} {state.away_fouls:>10}",
        "-" * 56,
        f"  Avg Pass Distance:       {avg_pass_dist:>8.1f} m",
        f"  Avg Possession Duration: {avg_poss_dur:>8.1f} s",
        "=" * 56,
        "",
    ]
    report = "\n".join(lines)
    print(report)
    return report
