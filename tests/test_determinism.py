"""Tests for simulation determinism.

Two runs with the same seed must produce identical event streams.
Two runs with different seeds must produce different streams.
"""

from __future__ import annotations

import json

from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.models.events import MatchEvent


def _simulate(seed: int) -> list[dict]:
    match = generate_match(seed=seed)
    engine = MatchEngine(match)
    events = engine.simulate()
    return [e.model_dump(mode="json") for e in events]


class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        """Identical seeds must produce byte-identical event streams."""
        run1 = _simulate(seed=42)
        run2 = _simulate(seed=42)

        assert len(run1) == len(run2), "Event count differs"
        for i, (a, b) in enumerate(zip(run1, run2)):
            assert a == b, f"Event {i} differs between runs"

    def test_different_seeds_different_output(self) -> None:
        """Different seeds must produce different event streams."""
        run1 = _simulate(seed=42)
        run2 = _simulate(seed=99)

        # They should differ in at least event count or content.
        if len(run1) == len(run2):
            diffs = sum(1 for a, b in zip(run1, run2) if a != b)
            assert diffs > 0, "Different seeds produced identical output"

    def test_determinism_across_many_seeds(self) -> None:
        """Verify determinism holds for several seeds."""
        for seed in [1, 7, 123, 9999]:
            r1 = _simulate(seed=seed)
            r2 = _simulate(seed=seed)
            assert r1 == r2, f"Non-deterministic output for seed={seed}"
