"""Tests for match/team/player generation."""

from __future__ import annotations

import random

from simulator.generation.generator import generate_match
from simulator.generation.teams import generate_two_teams
from simulator.generation.players import generate_players, FORMATION_POSITIONS
from simulator.models.match import Formation


class TestPlayerGeneration:
    def test_correct_player_count(self) -> None:
        rng = random.Random(1)
        players = generate_players("t1", "4-3-3", rng)
        assert len(players) == 11

    def test_has_goalkeeper(self) -> None:
        rng = random.Random(2)
        players = generate_players("t1", "4-4-2", rng)
        gks = [p for p in players if p.is_goalkeeper]
        assert len(gks) == 1

    def test_unique_shirt_numbers(self) -> None:
        rng = random.Random(3)
        players = generate_players("t1", "4-2-3-1", rng)
        numbers = [p.shirt_number for p in players]
        assert len(numbers) == len(set(numbers))

    def test_all_formations_produce_11(self) -> None:
        for formation in FORMATION_POSITIONS:
            rng = random.Random(99)
            players = generate_players("t1", formation, rng)
            assert len(players) == 11, f"Formation {formation} produced {len(players)} players"


class TestTeamGeneration:
    def test_two_different_teams(self) -> None:
        rng = random.Random(10)
        home, away = generate_two_teams(rng)
        assert home.team_id != away.team_id
        assert home.name != away.name  # different clubs

    def test_team_has_11_players(self) -> None:
        rng = random.Random(11)
        home, away = generate_two_teams(rng)
        assert len(home.players) == 11
        assert len(away.players) == 11


class TestMatchGeneration:
    def test_match_has_teams(self) -> None:
        match = generate_match(seed=42)
        assert match.home_team is not None
        assert match.away_team is not None
        assert match.synthetic_data is True

    def test_match_id_stable(self) -> None:
        m1 = generate_match(seed=42)
        m2 = generate_match(seed=42)
        assert m1.match_id == m2.match_id

    def test_players_belong_to_teams(self) -> None:
        match = generate_match(seed=42)
        for p in match.home_team.players:
            assert p.team_id == match.home_team.team_id
        for p in match.away_team.players:
            assert p.team_id == match.away_team.team_id
