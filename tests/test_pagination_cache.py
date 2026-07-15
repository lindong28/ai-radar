from __future__ import annotations

from collections.abc import Callable

import pytest

from airadar.web.routes.pagination import VersionedTotalCache, clamp_page


def _counter(values: list[int], value: int) -> Callable[[], int]:
    def compute() -> int:
        values.append(value)
        return value

    return compute


def test_versioned_total_cache_keys_values_by_signature_and_version() -> None:
    computed: list[int] = []
    cache = VersionedTotalCache(maxsize=4)

    first = cache.get_or_compute(
        signature=("news", "paper"),
        version=("db", 1),
        compute=_counter(computed, 7),
        cacheable=True,
    )
    cached = cache.get_or_compute(
        signature=("news", "paper"),
        version=("db", 1),
        compute=_counter(computed, 99),
        cacheable=True,
    )
    changed_version = cache.get_or_compute(
        signature=("news", "paper"),
        version=("db", 2),
        compute=_counter(computed, 8),
        cacheable=True,
    )

    assert (first, cached, changed_version) == (7, 7, 8)
    assert computed == [7, 8]


def test_versioned_total_cache_bypasses_storage_when_not_cacheable() -> None:
    computed: list[int] = []
    cache = VersionedTotalCache(maxsize=4)

    results = [
        cache.get_or_compute(
            signature=("search",),
            version=("db", 1),
            compute=_counter(computed, value),
            cacheable=False,
        )
        for value in (3, 4)
    ]

    assert results == [3, 4]
    assert computed == [3, 4]


def test_versioned_total_cache_uses_lru_eviction_and_caches_zero() -> None:
    computed: list[int] = []
    cache = VersionedTotalCache(maxsize=2)

    cache.get_or_compute(
        signature=("a",), version=(1,), compute=_counter(computed, 0), cacheable=True
    )
    cache.get_or_compute(
        signature=("b",), version=(1,), compute=_counter(computed, 2), cacheable=True
    )
    cache.get_or_compute(
        signature=("a",), version=(1,), compute=_counter(computed, 99), cacheable=True
    )
    cache.get_or_compute(
        signature=("c",), version=(1,), compute=_counter(computed, 3), cacheable=True
    )
    cache.get_or_compute(
        signature=("b",), version=(1,), compute=_counter(computed, 4), cacheable=True
    )

    assert computed == [0, 2, 3, 4]


def test_versioned_total_cache_prewarm_populates_default_key() -> None:
    computed: list[int] = []
    cache = VersionedTotalCache(maxsize=4)

    cache.prewarm(
        signature=("default",),
        version=("db", 1),
        compute=_counter(computed, 11),
    )
    result = cache.get_or_compute(
        signature=("default",),
        version=("db", 1),
        compute=_counter(computed, 99),
        cacheable=True,
    )

    assert result == 11
    assert computed == [11]


@pytest.mark.parametrize(
    ("page", "total", "limit", "expected"),
    [
        (-5, 12, 5, 1),
        (1, 0, 50, 1),
        (2, 5, 2, 2),
        (999, 5, 2, 3),
    ],
)
def test_clamp_page(page: int, total: int, limit: int, expected: int) -> None:
    assert clamp_page(page=page, total=total, limit=limit) == expected
