from __future__ import annotations

import json
import sqlite3
from itertools import chain, combinations

import pytest

from airadar.web.routes import categories

EXPECTED_CATEGORY_TAGS = {
    "ai-models": {"模型发布"},
    "ai-products": {"产品更新", "MCP/工具"},
    "industry": {"行业动态", "安全/对齐", "现象/趋势"},
    "paper": {"论文/研究"},
    "tip": {"教程/实践", "部署/工程"},
}
ORACLE_TAGS = (
    "模型发布",
    "产品更新",
    "MCP/工具",
    "行业动态",
    "安全/对齐",
    "现象/趋势",
    "论文/研究",
    "教程/实践",
    "部署/工程",
    "OpenAI",
)
ORACLE_CATEGORIES = (None, "", "unknown", *EXPECTED_CATEGORY_TAGS)
EXPECTED_PARAMS = {
    "ai-models": ["模型发布", "教程/实践"],
    "ai-products": ["MCP/工具", "产品更新", "模型发布", "产品更新"],
    "industry": ["安全/对齐", "现象/趋势", "行业动态"],
    "paper": ["论文/研究"],
    "tip": ["教程/实践", "部署/工程", "教程/实践", "安全/对齐", "现象/趋势", "行业动态"],
}


def _oracle_matches(tags: set[str], category: str | None) -> bool:
    if not category or category not in EXPECTED_CATEGORY_TAGS:
        return True
    if category == "ai-models" and "教程/实践" in tags:
        return False
    if category == "ai-products" and "模型发布" in tags and "产品更新" not in tags:
        return False
    if (
        category == "tip"
        and "教程/实践" not in tags
        and tags.intersection({"安全/对齐", "现象/趋势", "行业动态"})
    ):
        return False
    return bool(tags.intersection(EXPECTED_CATEGORY_TAGS[category]))


def _category_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE items (id TEXT PRIMARY KEY);
        CREATE TABLE item_evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          output_json TEXT NOT NULL,
          error TEXT
        );
        """
    )
    return conn


def _matching_ids(conn: sqlite3.Connection, category: str | None) -> tuple[set[str], list[object]]:
    clause, params = categories.category_filter_clause(category, "i")
    query = "SELECT i.id FROM items i"
    if clause:
        query += f" WHERE {clause}"
    return {str(row[0]) for row in conn.execute(query, params)}, params


def test_empty_tag_condition_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one tag predicate"):
        categories.TagCondition()


def test_category_tags_are_derived_from_the_declarative_contract() -> None:
    contract = categories.CATEGORY_CONTRACT

    assert set(contract) == set(EXPECTED_CATEGORY_TAGS)
    assert categories.CATEGORY_TAGS == {
        category: set(rule.include_any) for category, rule in contract.items()
    }
    assert {category: set(rule.include_any) for category, rule in contract.items()} == EXPECTED_CATEGORY_TAGS
    assert contract["ai-models"].exclude_when
    assert contract["ai-products"].exclude_when
    assert contract["tip"].exclude_when
    assert not contract["industry"].exclude_when
    assert not contract["paper"].exclude_when


def test_all_tag_subsets_match_the_independent_sql_and_python_oracle() -> None:
    subsets = list(
        chain.from_iterable(combinations(ORACLE_TAGS, size) for size in range(len(ORACLE_TAGS) + 1))
    )
    conn = _category_db()
    rows = [(f"item-{index:04d}",) for index in range(len(subsets))]
    conn.executemany("INSERT INTO items (id) VALUES (?)", rows)
    conn.executemany(
        """
        INSERT INTO item_evaluations (item_id, stage, output_json, error)
        VALUES (?, 'enrich', ?, NULL)
        """,
        [
            (item_id, json.dumps({"tags": subset}, ensure_ascii=False))
            for (item_id,), subset in zip(rows, subsets, strict=True)
        ],
    )

    checked = 0
    for category in ORACLE_CATEGORIES:
        expected_ids = {
            item_id
            for (item_id,), subset in zip(rows, subsets, strict=True)
            if _oracle_matches(set(subset), category)
        }
        sql_ids, params = _matching_ids(conn, category)
        python_ids = {
            item_id
            for (item_id,), subset in zip(rows, subsets, strict=True)
            if categories.matches_category({"topic_tags": list(subset)}, category)
        }

        assert params == EXPECTED_PARAMS.get(category, [])
        assert sql_ids == expected_ids
        assert python_ids == expected_ids
        checked += len(subsets)

    assert checked == 8192


def test_sql_matching_uses_only_the_latest_valid_enrich_evaluation() -> None:
    conn = _category_db()
    item_ids = ("latest-valid", "later-error", "non-enrich", "multiple-valid", "no-eval")
    conn.executemany("INSERT INTO items (id) VALUES (?)", [(item_id,) for item_id in item_ids])

    evaluations = [
        ("latest-valid", "enrich", {"tags": ["模型发布"]}, None),
        ("later-error", "enrich", {"tags": ["模型发布"]}, None),
        ("later-error", "enrich", {"tags": ["论文/研究"]}, "provider failed"),
        ("non-enrich", "scoring", {"tags": ["模型发布"]}, None),
        ("multiple-valid", "enrich", {"tags": ["模型发布"]}, None),
        ("multiple-valid", "enrich", {"tags": ["论文/研究"]}, None),
    ]
    conn.executemany(
        """
        INSERT INTO item_evaluations (item_id, stage, output_json, error)
        VALUES (?, ?, ?, ?)
        """,
        [
            (item_id, stage, json.dumps(payload, ensure_ascii=False), error)
            for item_id, stage, payload, error in evaluations
        ],
    )

    model_ids, _ = _matching_ids(conn, "ai-models")
    paper_ids, _ = _matching_ids(conn, "paper")

    assert model_ids == {"latest-valid", "later-error"}
    assert paper_ids == {"multiple-valid"}


@pytest.mark.parametrize(
    ("topic_tags", "expected"),
    [
        pytest.param(None, False, id="null"),
        pytest.param("模型发布", False, id="scalar"),
        pytest.param({"nested": "模型发布"}, False, id="object"),
        pytest.param([123, None, {"nested": "value"}], False, id="non-string-list"),
        pytest.param([123, "模型发布", None], True, id="mixed-list"),
    ],
)
def test_python_matching_preserves_malformed_topic_tag_behavior(topic_tags: object, expected: bool) -> None:
    assert categories.matches_category({"topic_tags": topic_tags}, "ai-models") is expected
    assert categories.matches_category({"topic_tags": topic_tags}, "unknown") is True


def test_python_matching_rejects_missing_topic_tags_for_known_categories() -> None:
    assert categories.matches_category({}, "ai-models") is False
    assert categories.matches_category({}, "unknown") is True


@pytest.mark.parametrize(
    ("output_json", "expected"),
    [
        pytest.param("{}", False, id="missing"),
        pytest.param('{"tags":null}', False, id="null"),
        pytest.param('{"tags":123}', False, id="number"),
        pytest.param('{"tags":{"nested":"模型发布"}}', True, id="object-values-are-iterated"),
        pytest.param('{"tags":[123,null,{"nested":"value"}]}', False, id="non-string-list"),
        pytest.param('{"tags":[123,"模型发布",null]}', True, id="mixed-list"),
    ],
)
def test_sql_matching_preserves_non_array_json_shape_behavior(output_json: str, expected: bool) -> None:
    conn = _category_db()
    conn.execute("INSERT INTO items (id) VALUES ('item-1')")
    conn.execute(
        """
        INSERT INTO item_evaluations (item_id, stage, output_json, error)
        VALUES ('item-1', 'enrich', ?, NULL)
        """,
        (output_json,),
    )

    matched_ids, _ = _matching_ids(conn, "ai-models")

    assert bool(matched_ids) is expected


@pytest.mark.parametrize(
    "output_json",
    [
        pytest.param('{"tags":"模型发布"}', id="scalar-string"),
        pytest.param("not-json", id="invalid-json"),
    ],
)
def test_sql_matching_preserves_malformed_json_errors(output_json: str) -> None:
    conn = _category_db()
    conn.execute("INSERT INTO items (id) VALUES ('item-1')")
    conn.execute(
        """
        INSERT INTO item_evaluations (item_id, stage, output_json, error)
        VALUES ('item-1', 'enrich', ?, NULL)
        """,
        (output_json,),
    )

    with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
        _matching_ids(conn, "ai-models")


@pytest.mark.parametrize(
    ("category", "tags", "expected", "expected_params"),
    [
        ("ai-models", ["模型发布"], True, ["模型发布", "教程/实践"]),
        ("ai-models", ["模型发布", "教程/实践"], False, ["模型发布", "教程/实践"]),
        (
            "ai-products",
            ["模型发布", "MCP/工具"],
            False,
            ["MCP/工具", "产品更新", "模型发布", "产品更新"],
        ),
        (
            "ai-products",
            ["模型发布", "产品更新"],
            True,
            ["MCP/工具", "产品更新", "模型发布", "产品更新"],
        ),
        ("industry", ["安全/对齐"], True, ["安全/对齐", "现象/趋势", "行业动态"]),
        ("paper", ["论文/研究"], True, ["论文/研究"]),
        (
            "tip",
            ["部署/工程"],
            True,
            ["教程/实践", "部署/工程", "教程/实践", "安全/对齐", "现象/趋势", "行业动态"],
        ),
        (
            "tip",
            ["部署/工程", "行业动态"],
            False,
            ["教程/实践", "部署/工程", "教程/实践", "安全/对齐", "现象/趋势", "行业动态"],
        ),
        (
            "tip",
            ["部署/工程", "安全/对齐"],
            False,
            ["教程/实践", "部署/工程", "教程/实践", "安全/对齐", "现象/趋势", "行业动态"],
        ),
        (
            "tip",
            ["部署/工程", "现象/趋势"],
            False,
            ["教程/实践", "部署/工程", "教程/实践", "安全/对齐", "现象/趋势", "行业动态"],
        ),
        ("unknown", ["模型发布"], True, []),
        (None, ["模型发布", "教程/实践"], True, []),
        ("", ["行业动态"], True, []),
    ],
)
def test_sql_and_python_category_matching_have_parity(
    category: str | None,
    tags: list[str],
    expected: bool,
    expected_params: list[str],
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE items (id TEXT PRIMARY KEY);
        CREATE TABLE item_evaluations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          output_json TEXT NOT NULL,
          error TEXT
        );
        """
    )
    conn.execute("INSERT INTO items (id) VALUES ('item-1')")
    conn.execute(
        """
        INSERT INTO item_evaluations (item_id, stage, output_json, error)
        VALUES ('item-1', 'enrich', ?, NULL)
        """,
        (json.dumps({"tags": tags}, ensure_ascii=False),),
    )

    clause, params = categories.category_filter_clause(category, "i")
    query = "SELECT i.id FROM items i"
    if clause:
        query += f" WHERE {clause}"
    sql_matches = conn.execute(query, params).fetchall()
    python_matches = categories.matches_category({"topic_tags": tags}, category)

    assert params == expected_params
    assert bool(sql_matches) is expected
    assert python_matches is expected
    assert bool(sql_matches) is python_matches
