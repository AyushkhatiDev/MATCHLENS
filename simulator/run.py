"""CLI entry-point for MatchLens simulator.

Usage
-----
    python -m simulator.run --seed 42
    python -m simulator.run --seed 42 --duration 90 --output data/generated/match.jsonl
    python -m simulator.run --seed 42 --speed 10   # 10× real-time pacing
    python -m simulator.run --seed 42 --analyze     # run Phase 2 intelligence
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.output.writer import (
    print_summary,
    write_events_jsonl,
    write_match_metadata,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="matchlens-simulator",
        description="Generate a synthetic 90-minute football match.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for deterministic generation (default: 42).",
    )
    parser.add_argument(
        "--duration", type=int, default=90,
        help="Match duration in minutes (default: 90).",
    )
    parser.add_argument(
        "--output", type=str, default="data/generated/match.jsonl",
        help="Path for the JSONL event output.",
    )
    parser.add_argument(
        "--speed", type=float, default=0,
        help=(
            "Simulation speed multiplier for paced output.  "
            "0 = instant (default).  10 = 10 sim-seconds per real second."
        ),
    )
    parser.add_argument(
        "--analyze", action="store_true", default=False,
        help="Run Phase 2 intelligence analysis and write intelligence.json.",
    )
    parser.add_argument(
        "--explain", action="store_true", default=False,
        help="Run Phase 2 and deterministic Phase 3; write explanations.json.",
    )
    parser.add_argument(
        "--agents", action="store_true", default=False,
        help="Run Phase 4 routed agents (implies --analyze and --explain).",
    )
    parser.add_argument(
        "--agent-moment", type=str, default=None,
        help="Explain one moment ID; otherwise run goal, pressure and state demo examples.",
    )
    args = parser.parse_args(argv)
    if args.agent_moment and not args.agents:
        parser.error("--agent-moment requires --agents")
    agent_config = None
    if args.agents:
        try:
            from simulator.agentic.config import AgenticConfig
            agent_config = AgenticConfig.from_env()
        except ImportError:
            parser.error("Phase 4 dependencies are missing. Install the agentic extra.")
        except ValueError:
            parser.error("Invalid agent configuration. Foundry requires an HTTPS FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL; leave FOUNDRY_AGENT_ID unset.")

    print(f"[MatchLens] Generating match with seed={args.seed}, duration={args.duration}min …")

    # 1. Generate match structure.
    match = generate_match(seed=args.seed, duration_minutes=args.duration)

    # 2. Simulate.
    engine = MatchEngine(match)
    events = engine.simulate()

    # 3. Optional pacing.
    if args.speed > 0:
        _paced_output(events, args.speed)

    # 4. Write outputs.
    jsonl_path = write_events_jsonl(events, args.output)
    meta_path = write_match_metadata(
        match, engine.state,
        str(jsonl_path).replace(".jsonl", "_meta.json"),
    )

    print(f"[MatchLens] Events written to {jsonl_path} ({len(events):,} events)")
    print(f"[MatchLens] Metadata written to {meta_path}")

    # 5. Summary report.
    print_summary(engine.state)

    # 6. Intelligence analysis (Phase 2).
    if args.analyze or args.explain or args.agents:
        from simulator.intelligence.engine import IntelligenceEngine
        from simulator.intelligence.report import (
            write_intelligence_json,
            print_intelligence_report,
        )

        print("[MatchLens] Running intelligence analysis …")
        intel_engine = IntelligenceEngine(match, events)
        intel = intel_engine.analyze()

        intel_path = str(jsonl_path).replace(".jsonl", "_intelligence.json")
        write_intelligence_json(intel, intel_path)
        print(f"[MatchLens] Intelligence written to {intel_path}")

        print_intelligence_report(intel)

        if args.explain or args.agents:
            from simulator.intelligence.explanation import ExplanationEngine
            from simulator.intelligence.explanation.report import (
                write_explanations_json, print_explanations_report,
            )
            report = ExplanationEngine(source_events=events).explain(intel)
            explanation_path = jsonl_path.parent / "explanations.json"
            write_explanations_json(report, explanation_path)
            print(f"[MatchLens] Explanations written to {explanation_path}")
            print_explanations_report(report)

            if args.agents:
                import asyncio
                from simulator.agentic.tools import IntelligenceStore
                from simulator.agentic.runtime.runner import MatchAgentRunner
                from simulator.agentic.report import write_agent_results, print_agent_result
                store = IntelligenceStore(intel, report, events)
                runner = MatchAgentRunner(store, config=agent_config)
                if args.agent_moment:
                    try:
                        selected = [store.explanation(args.agent_moment)]
                    except ValueError:
                        parser.error("Requested moment is unavailable or was filtered by Phase 3.")
                else:
                    selected = []
                    for kind in ("GOAL", "PRESSURE_SHIFT", "STATE_CHANGE"):
                        example = next((e for e in report.explanations if e.explanation_type.value == kind), None)
                        if example is not None:
                            selected.append(example)
                async def run_demo():
                    try:
                        results = []
                        for example in selected:
                            result = await runner.process_moment(example.moment_id)
                            print_agent_result(result)
                            results.append(result)
                        return results
                    finally:
                        await runner.aclose()
                try:
                    results = asyncio.run(run_demo())
                except Exception:
                    parser.error("Agent runtime could not start. Check the installed SDKs and Foundry configuration; no remote success is assumed.")
                agent_path = jsonl_path.parent / "agent_results.json"
                write_agent_results(results, agent_path)
                print(f"[MatchLens] Agent results written to {agent_path}")
                if any(result.workflow_status == "FAILED" for result in results):
                    raise SystemExit(1)


def _paced_output(events: list, speed: float) -> None:
    """Print events with real-time pacing (for demonstration)."""
    prev_ts = 0.0
    for ev in events:
        ts = ev.timestamp
        gap = (ts - prev_ts) / speed
        if gap > 0:
            time.sleep(gap)
        prev_ts = ts
        _type = ev.event_type.value if hasattr(ev.event_type, "value") else ev.event_type
        minute = int(ts // 60)
        print(f"  [{minute:02d}:{int(ts % 60):02d}]  {_type:<22} team={ev.team_id or '-'}")


if __name__ == "__main__":
    main()
