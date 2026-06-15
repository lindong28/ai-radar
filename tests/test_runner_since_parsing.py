from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from airadar.enrich.runner import _parse_since as parse_enrich_since
from airadar.prefilter.runner import _parse_since as parse_prefilter_since
from airadar.scorer.runner import _parse_since as parse_scorer_since

PARSERS: list[Callable[[str], datetime]] = [
    parse_enrich_since,
    parse_prefilter_since,
    parse_scorer_since,
]
PARSER_IDS = ["enrich", "prefilter", "scorer"]


@pytest.mark.parametrize("parse_since", PARSERS, ids=PARSER_IDS)
@pytest.mark.parametrize(
    ("value", "delta"),
    [
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("7D", timedelta(days=7)),
    ],
)
def test_parse_since_accepts_relative_units_case_insensitively(
    parse_since: Callable[[str], datetime], value: str, delta: timedelta
) -> None:
    expected_start = datetime.now(UTC) - delta

    parsed = parse_since(value)

    expected_end = datetime.now(UTC) - delta
    assert expected_start <= parsed <= expected_end


@pytest.mark.parametrize("parse_since", PARSERS, ids=PARSER_IDS)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-06-01T10:43:04Z", datetime(2026, 6, 1, 10, 43, 4, tzinfo=UTC)),
        ("2026-06-01 10:43:04+00:00", datetime(2026, 6, 1, 10, 43, 4, tzinfo=UTC)),
    ],
)
def test_parse_since_accepts_iso_timestamps(
    parse_since: Callable[[str], datetime], value: str, expected: datetime
) -> None:
    assert parse_since(value) == expected
