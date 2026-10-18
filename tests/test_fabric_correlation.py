"""Regression: the live MatchEvents.correlation_id column is a guid, so run-prefix reconciliation must use
envelope.correlation_id, while the bounded run keeps its exact-match query. Offline; no cloud calls."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import pytest
from simulator.production.transport import VerificationFailed, key, query_destination, run_envelopes
from tests.test_transport_verification import EXACT_QUERY, TARGET, kusto_select, toguid

RUN = 'c' * 32
OTHER = 'd' * 32


def prefixed(run_id, count=20):
    """Full-match style envelopes: correlation IDs `{run_id}:{sequence}`."""
    _, envelopes = run_envelopes(run_id, datetime(2026, 10, 16, tzinfo=timezone.utc))
    return [e.model_copy(update={'correlation_id': f'{run_id}:{e.sequence_number}'}) for e in envelopes[:count]]


class Table:
    """Fabric reader stand-in over typed rows; records every KQL statement it receives."""

    def __init__(self, stored, fail=False):
        self.stored, self.fail, self.queries = stored, fail, []

    async def query(self, endpoint, database, csl):
        self.queries.append(csl)
        if self.fail:
            raise VerificationFailed('fabric_destination', 'KQL query returned HTTP 503')
        return kusto_select(self.stored, csl)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds


def run_query(stored, published, run_id=RUN, *, prefix=True, min_rows=None, target=TARGET, fail=False):
    table, clock = Table(stored, fail), Clock()
    expected = {key(e): e for e in published}

    async def scenario():
        return await query_destination(table, target, expected, run_id, timeout=30, interval=10, sleep=clock.sleep,
                                       clock=clock, prefix=prefix, min_rows=len(expected) if min_rows is None else min_rows)
    try:
        return asyncio.run(scenario()), table.queries
    except VerificationFailed as failure:
        return failure, table.queries


def test_prefix_mode_filters_on_the_envelope_correlation_id():
    published = prefixed(RUN)
    rows, queries = run_query(published, published)
    assert queries[0] == f'MatchEvents | where tostring(envelope.correlation_id) startswith "{RUN}:" | project envelope'
    assert [key(e) for e in rows] == [key(e) for e in published]


def test_bounded_exact_match_query_is_unchanged():
    _, envelopes = run_envelopes(RUN, datetime(2026, 10, 16, tzinfo=timezone.utc))
    published = envelopes[:10]
    stored = [published[0], *published]
    rows, queries = run_query(stored, published, prefix=False, min_rows=len(published) + 1)
    assert queries[0] == f'MatchEvents | where correlation_id == "{RUN}" | project envelope'
    assert EXACT_QUERY.match(queries[0]) and len(rows) == len(published) + 1


def test_guid_typed_correlation_id_holds_null_for_run_prefixed_ids():
    published = prefixed(RUN)
    assert all(toguid(e.correlation_id) is None for e in published)
    assert toguid(RUN) == '-'.join((RUN[:8], RUN[8:12], RUN[12:16], RUN[16:20], RUN[20:]))
    old = kusto_select(published, f'MatchEvents | where correlation_id startswith "{RUN}:" | project envelope')
    assert old['Tables'][0]['Rows'] == []  # the pre-fix query finds nothing against the live schema
    new = kusto_select(published, f'MatchEvents | where tostring(envelope.correlation_id) startswith "{RUN}:" | project envelope')
    assert len(new['Tables'][0]['Rows']) == len(published)


def test_other_runs_are_excluded():
    published = prefixed(RUN)
    stored = [*prefixed(OTHER), *published, *prefixed(RUN[:31] + 'e')]
    rows, _ = run_query(stored, published)
    assert sorted(key(e) for e in rows) == sorted(key(e) for e in published)
    assert {e.correlation_id.split(':')[0] for e in rows} == {RUN}


def test_duplicates_are_kept_and_counted_not_hidden():
    published = prefixed(RUN)
    rows, _ = run_query([*published, published[3], published[4]], published)
    assert len(rows) == len(published) + 2 and len({key(e) for e in rows}) == len(published)


@pytest.mark.parametrize('stored,reason', [
    (lambda p: p[:-3], 'missing 3'),
    (lambda p: [*p[:-1], p[-1].model_copy(update={'source': 'tampered'})], 'stored envelope differs'),
    (lambda p: [*p, p[0].model_copy(update={'sequence_number': 9999, 'correlation_id': f'{RUN}:9999'})], 'stored envelope differs'),
    (lambda p: [*p[1:], p[0].model_copy(update={'correlation_id': f'{RUN}:x'})], 'stored envelope differs'),
])
def test_missing_altered_or_unexpected_rows_fail(stored, reason):
    published = prefixed(RUN)
    failure, _ = run_query(stored(published), published)
    assert isinstance(failure, VerificationFailed) and failure.stage == 'fabric_destination' and reason in failure.reason


def test_a_failed_query_is_never_treated_as_verified():
    published = prefixed(RUN)
    failure, _ = run_query(published, published, fail=True)
    assert isinstance(failure, VerificationFailed) and failure.reason == 'KQL query returned HTTP 503'


@pytest.mark.parametrize('run_id,table', [
    ('c' * 31 + '"', 'MatchEvents'),
    ('C' * 32, 'MatchEvents'),
    (RUN, 'MatchEvents | take 1'),
    (RUN, 'Match"Events'),
])
def test_unsafe_run_ids_and_table_names_never_reach_the_query(run_id, table):
    published = prefixed(RUN)
    failure, queries = run_query(published, published, run_id=run_id, target=replace(TARGET, table=table))
    assert isinstance(failure, VerificationFailed) and failure.stage == 'configuration' and queries == []
