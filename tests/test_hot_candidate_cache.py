"""ADR-060 的三条承重不变式各自的失败证据。

1. SQL 窗口子句产出的候选集是 Python 保留集的**超集**——含时间戳格式的边界。
2. 取消 600 条上限修正了一处静默截断，其等价条件按"是否落在旧查询前 600 位"
   陈述，不是"合格条目数是否 ≤ 600"。
3. `peek` 从不计算、也不发起计算；重算窗口内继续供旧值，只受 `max_stale` 硬上界约束。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from airadar import db
from airadar.web.app import create_app
from airadar.web.routes import curated as curated_routes
from airadar.web.routes import curated_archive, hot_cache

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(path: Path) -> None:
    db.migrate(path)
    with db.get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at)
            VALUES ('src', 'Wire', 'https://example.invalid/s', 'T1', 1, 'feed',
                    'https://example.invalid/', NULL, '{}', '2026-07-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO curation_runs (id, ruleset_version, weights_json, threshold,
                                       input_eval_ids, output_curated_ids, created_at)
            VALUES ('run-1', 'r1', '{}', 0, '[]', '[]', '2026-07-01T00:00:00Z')
            """
        )
        conn.commit()


def _add_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    published_at: str,
    fetched_at: str,
    score: float = 5.0,
) -> None:
    conn.execute(
        """
        INSERT INTO items (id, source_id, url, title, author, published_at, fetched_at,
                           content_text, content_html, content_hash, extra_json)
        VALUES (?, 'src', ?, ?, 'A', ?, ?, 'text', NULL, ?, '{}')
        """,
        (item_id, f"https://example.invalid/i/{item_id}", item_id, published_at, fetched_at, item_id),
    )
    conn.execute(
        "INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json) VALUES ('run-1', ?, ?, 1, '{}')",
        (item_id, score),
    )


# --------------------------------------------------------------------------
# 1. 超集不变式
# --------------------------------------------------------------------------


# 边界样本必须落在 SQL 真正用的那个 cutoff 上。compute_candidates 永远按
# WINDOW_HOURS_MAX 取候选（更小的 hours 在 Python 侧派生），所以把样本放在 48h
# 上等于离边界五天远——阴性对照曾因此全绿，改动这里前先重看那次对照。
EDGE = NOW - timedelta(hours=hot_cache.WINDOW_HOURS_MAX)


@pytest.mark.parametrize(
    ("label", "published_at", "fetched_at"),
    [
        # 正好落在 cutoff 上——两秒余量要接得住它
        ("exactly_at_cutoff", _iso(EDGE), _iso(EDGE)),
        # 带毫秒：`.000Z` 的字典序小于同秒的 `Z`，无余量时会被错误排除
        ("fractional_seconds", EDGE.strftime("%Y-%m-%dT%H:%M:%S.000Z"), _iso(EDGE)),
        # 七位小数：位数不固定时"UTC-Z 串必与时间同序"这句话就不成立
        ("high_precision", EDGE.strftime("%Y-%m-%dT%H:%M:%S.0000001Z"), _iso(EDGE)),
        # 未来发布时间 → Python 回退到 fetched_at 分支
        ("future_published", _iso(NOW + timedelta(days=2)), _iso(NOW - timedelta(hours=1))),
        # 带**负**偏移量：真实 UTC 在窗口内，但字典序读的是本地墙钟（早 5 小时），
        # 于是串沉到 cutoff 之下。必须是负偏移——正偏移的串排在 cutoff 之上，
        # 第一析取项就接住了，逃生口根本不会被用到（阴性对照曾因此全绿）。
        (
            "negative_offset_timezone",
            (EDGE + timedelta(hours=1) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S-05:00"),
            _iso(EDGE + timedelta(hours=1)),
        ),
        # 非法串且**字典序在 cutoff 之下**：同理，"not-a-date" 以 'n' 开头排在
        # "2026…" 之上，测不到逃生口。
        ("unparseable_published", "1999/01/01 garbage", _iso(NOW - timedelta(hours=1))),
        # 双 Z：`*Z` 那个 GLOB 会匹配它（`*` 吞掉第一个 Z），而 Python 把**每个** Z
        # 都换成 `+00:00`、解析失败、回退到 fetched_at。只靠 NOT GLOB 接不住，要靠
        # "存在非末位 Z" 这个额外逃生口。
        ("interior_z", "1999-01-01T00:00:00ZZ", _iso(NOW - timedelta(hours=1))),
    ],
)
def test_sql_window_never_drops_a_row_python_would_keep(
    tmp_path: Path,
    label: str,
    published_at: str,
    fetched_at: str,
) -> None:
    path = tmp_path / f"{label}.db"
    _seed(path)
    with db.get_conn(path) as conn:
        _add_item(conn, label, published_at=published_at, fetched_at=fetched_at)
        conn.commit()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        candidates = hot_cache.compute_candidates(conn, NOW)
    finally:
        conn.close()

    assert label in {str(item["id"]) for item in candidates}, (
        f"{label} 会被 SQL 排除，但 Python 的年龄过滤会保留它——超集不变式已破"
    )


def test_declared_residual_gap_has_exactly_the_shape_the_adr_claims(tmp_path: Path) -> None:
    """ADR-060 明示保留的唯一缺口，在这里被钉住形状而不是被假装不存在。

    时间串同时满足三条才会漏：UTC-Z 规范形状（逃生口放不进来）、语义非法
    （Python 解析失败、回退到 fetched_at 分支从而可能保留它）、日期陈旧
    （字典序沉在 cutoff 之下）。缺任何一条都不漏——下面三个对照就是各缺一条。
    """
    path = tmp_path / "residual.db"
    _seed(path)
    recent = _iso(NOW - timedelta(hours=1))
    with db.get_conn(path) as conn:
        # 三条俱全 → 漏（已知、已接受）
        _add_item(conn, "residual", published_at="1999-13-45T00:00:00Z", fetched_at=recent)
        # 缺"陈旧" → 不漏
        _add_item(conn, "invalid_but_recent", published_at="2026-13-45T00:00:00Z", fetched_at=recent)
        # 缺"规范形状" → 逃生口接住，不漏
        _add_item(conn, "invalid_and_old_but_unshaped", published_at="1999-13-45 garbage", fetched_at=recent)
        conn.commit()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ids = {str(item["id"]) for item in hot_cache.compute_candidates(conn, NOW)}
    finally:
        conn.close()

    assert "residual" not in ids
    assert "invalid_but_recent" in ids
    assert "invalid_and_old_but_unshaped" in ids


def test_sql_window_still_excludes_rows_far_outside_it(tmp_path: Path) -> None:
    """阴性对照：上面那批断言若来自一个"什么都放行"的子句，同样会全绿。"""
    path = tmp_path / "outside.db"
    _seed(path)
    with db.get_conn(path) as conn:
        _add_item(conn, "ancient", published_at="2026-01-01T00:00:00Z", fetched_at="2026-01-01T00:00:00Z")
        _add_item(conn, "inside", published_at=_iso(NOW - timedelta(hours=2)), fetched_at=_iso(NOW - timedelta(hours=2)))
        conn.commit()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ids = {str(item["id"]) for item in hot_cache.compute_candidates(conn, NOW)}
    finally:
        conn.close()

    assert "inside" in ids
    assert "ancient" not in ids


# --------------------------------------------------------------------------
# 2. 取消 600 条上限：等价条件按"是否落在前 600 位"陈述
# --------------------------------------------------------------------------


def test_old_600_cap_dropped_a_qualifying_item_that_the_window_query_keeps(tmp_path: Path) -> None:
    """合格条目数远少于 600，旧实现照样丢掉它——因为前 600 位被不合格条目占满。

    这正是"合格条目数 ≤ 600 即等价"这句话不成立的形态。
    """
    path = tmp_path / "truncation.db"
    _seed(path)
    with db.get_conn(path) as conn:
        # 605 条发布时间在未来的条目：它们排在 published_at DESC 的最前面，且
        # Python 会因 published_ts > now 回退到 fetched_at——而 fetched_at 远在
        # 窗口之外，所以它们全部不合格。
        for index in range(605):
            _add_item(
                conn,
                f"future-{index:04d}",
                published_at=_iso(NOW + timedelta(days=30, seconds=index)),
                fetched_at="2026-01-01T00:00:00Z",
            )
        _add_item(
            conn,
            "buried-hot",
            published_at=_iso(NOW - timedelta(hours=1)),
            fetched_at=_iso(NOW - timedelta(hours=1)),
            score=99.0,
        )
        conn.commit()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        old_page, _total, _page = curated_archive._compute_archive_page(
            conn, page=1, limit=600, normalized_category=None, q=None
        )
        new_candidates = hot_cache.compute_candidates(conn, NOW)
    finally:
        conn.close()

    assert "buried-hot" not in {str(item["id"]) for item in old_page}
    assert "buried-hot" in {str(item["id"]) for item in new_candidates}


# --------------------------------------------------------------------------
# 3. 缓存语义
# --------------------------------------------------------------------------


def _cache_with_entry(db_path: Path, candidates: list[dict[str, object]]) -> hot_cache.HotCandidateCache:
    cache = hot_cache.HotCandidateCache()
    cache.bind(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cache._version = hot_cache.candidate_version(conn)
    finally:
        conn.close()
    cache._candidates = candidates
    cache._stored_at = time.monotonic()
    return cache


def test_peek_returns_the_cached_candidates_when_version_and_age_match(tmp_path: Path) -> None:
    path = tmp_path / "hit.db"
    _seed(path)
    cache = _cache_with_entry(path, [{"id": "cached"}])

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert cache.peek() == [{"id": "cached"}]
    finally:
        conn.close()


def test_a_rescoring_run_changes_the_version_even_though_generation_does_not(tmp_path: Path) -> None:
    """新一轮 curation 改了 weighted_score，而 archive_generation 不动——
    migration 014 把 `archive_cache_curated_ai` 收窄成"仅首次精选才 bump"。
    版本键里的 curation_runs 计数就是为这一刀补的。

    版本的职责是告诉 **keeper** 该重新水合了；它不再让 `peek` 拒供旧值（那会
    在每轮 curation 后制造空白窗口）。所以这里断言的是版本元组变了。"""
    path = tmp_path / "version.db"
    _seed(path)
    with db.get_conn(path) as conn:
        _add_item(conn, "item-1", published_at=_iso(NOW - timedelta(hours=1)), fetched_at=_iso(NOW - timedelta(hours=1)))
        conn.commit()
    cache = _cache_with_entry(path, [{"id": "cached"}])

    with db.get_conn(path) as conn:
        generation_before = conn.execute(
            "SELECT archive_generation FROM archive_cache_generations WHERE id=1"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO curation_runs (id, ruleset_version, weights_json, threshold,
                                       input_eval_ids, output_curated_ids, created_at)
            VALUES ('run-0', 'r1', '{}', 0, '[]', '[]', '2026-08-02T00:00:00Z')
            """
        )
        conn.execute(
            "INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json) VALUES ('run-0', 'item-1', 42.0, 1, '{}')"
        )
        conn.commit()
        generation_after = conn.execute(
            "SELECT archive_generation FROM archive_cache_generations WHERE id=1"
        ).fetchone()[0]

    # 阳性对照：确认这次写入**确实**没有推进 archive_generation，否则本测试
    # 会因为一个与被测机制无关的原因而通过。
    assert generation_after == generation_before

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert hot_cache.candidate_version(conn) != cache._version
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("label", "sql"),
    [
        ("disabled", "UPDATE sources SET enabled=0 WHERE id='src'"),
        ("kind_changed", "UPDATE sources SET kind='wechat' WHERE id='src'"),
        ("renamed", "UPDATE sources SET name='Renamed Wire' WHERE id='src'"),
    ],
)
def test_source_edits_that_change_the_payload_change_the_version(
    tmp_path: Path, label: str, sql: str
) -> None:
    """`enabled` / `kind` 决定候选成员资格，`name` 直接进 payload——而
    `archive_cache_sources_au_id` 只认 `UPDATE OF id`，这三种写入都不推进
    generation。版本键里的 sources 指纹就是为它们补的。"""
    path = tmp_path / f"src-{label}.db"
    _seed(path)
    cache = _cache_with_entry(path, [{"id": "cached"}])

    with db.get_conn(path) as conn:
        before = conn.execute("SELECT archive_generation FROM archive_cache_generations WHERE id=1").fetchone()[0]
        conn.execute(sql)
        conn.commit()
        after = conn.execute("SELECT archive_generation FROM archive_cache_generations WHERE id=1").fetchone()[0]

    # 阳性对照：确认这次写入确实没推进 generation，否则测试会因无关原因通过。
    assert after == before

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert hot_cache.candidate_version(conn) != cache._version
    finally:
        conn.close()


def test_rebinding_to_another_database_discards_the_previous_one_s_rows(tmp_path: Path) -> None:
    """`peek` 不再校验版本，而版本正是过去携带 db 路径的那一项。

    所以换库时若不清空，旧库的行会被继续供出最长 `max_stale` 秒。生产是单
    app 单库碰不到，但进程内 lifespan 重启、嵌入第二个 app、以及测试会碰到。
    """
    path_a = tmp_path / "a.db"
    path_b = tmp_path / "b.db"
    _seed(path_a)
    _seed(path_b)
    cache = _cache_with_entry(path_a, [{"id": "from-a"}])
    assert cache.peek() == [{"id": "from-a"}]

    cache.bind(path_b)

    assert cache.peek() is None, "换库后仍在供上一个库的行"


def test_the_sources_digest_cannot_be_forged_by_a_field_containing_a_separator(
    tmp_path: Path,
) -> None:
    """摘要输入用长度前缀而非分隔符。

    分隔符只在"数据里不会出现它"时才无歧义，而 `kind` / `name` 是配置里的自由
    文本。下面这一对在按 `\x1f` 拼接时原像逐字节相同（行数也相同，所以 len 那
    一项也挡不住），内容却不同——歧义的后果是漏失效。
    """

    def version_for(kind: str, name: str) -> tuple[object, ...]:
        path = tmp_path / f"digest-{abs(hash((kind, name)))}.db"
        db.migrate(path)
        with db.get_conn(path) as conn:
            conn.execute(
                "INSERT INTO sources (id, name, url, tier, enabled, kind, homepage_url,"
                " icon_url, meta_json, synced_at) VALUES ('s1', ?, 'https://e.invalid/', 'T1',"
                " 1, ?, NULL, NULL, '{}', '2026-07-01T00:00:00Z')",
                (name, kind),
            )
            conn.commit()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return hot_cache.candidate_version(conn)
        finally:
            conn.close()

    sep = "\x1f"
    # 拼接原像同为 s1 · 1 · feed · A · B —— 只是 A 该归 kind 还是 name 说不清。
    #
    # 只比**摘要那一位**：版本元组还含 db 文件路径，而两个 fixture 库路径本就
    # 不同，比整个元组会因为一个与被测机制无关的原因恒不相等——阴性对照曾因此
    # 全绿。
    left = version_for("feed", f"A{sep}B")[-1]
    right = version_for(f"feed{sep}A", "B")[-1]

    assert left != right


def test_peek_treats_an_entry_past_max_stale_as_unusable(tmp_path: Path) -> None:
    path = tmp_path / "stale.db"
    _seed(path)
    cache = _cache_with_entry(path, [{"id": "cached"}])
    cache._stored_at = time.monotonic() - hot_cache.MAX_STALE_SECONDS - 1

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert cache.peek() is None
    finally:
        conn.close()


def test_peek_computes_nothing_at_all_not_even_on_another_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`peek` 是纯读：既不在调用线程上算，也不去**发起**一次计算。

    "请求可以发起水合"这件事本身就是缺陷来源：客户端最后一次重试可能正好是发起
    那次成功水合的请求，它拿到 503 就放弃，而数据一秒后才落地——访客永久看不到
    已经备好的热点。水合的所有权收在 keeper 线程上，这个形态才不可能出现。
    """
    path = tmp_path / "nocompute.db"
    _seed(path)
    cache = hot_cache.HotCandidateCache()
    cache.bind(path)
    calls: list[str] = []

    def spy(conn: sqlite3.Connection, now: datetime) -> list[dict[str, object]]:
        calls.append(threading.current_thread().name)
        return []

    monkeypatch.setattr(hot_cache, "compute_candidates", spy)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        started = time.monotonic()
        assert cache.peek() is None
        assert time.monotonic() - started < 0.5
    finally:
        conn.close()

    # 给一个可能被误起的后台线程留出充裕时间；真正的实现里根本不该有这个线程。
    time.sleep(0.3)
    assert calls == [], f"peek 发起了水合（线程 {calls}）"


def test_the_keeper_rehydrates_without_any_request_arriving(tmp_path: Path) -> None:
    """pipeline 每 15 分钟改一次版本；没有 keeper 时，冷态由那之后第一个访客承担。"""
    path = tmp_path / "keeper.db"
    _seed(path)
    with db.get_conn(path) as conn:
        _add_item(conn, "item-1", published_at=_iso(NOW - timedelta(hours=1)), fetched_at=_iso(NOW - timedelta(hours=1)))
        conn.commit()

    cache = hot_cache.HotCandidateCache()
    cache.bind(path)
    cache._refresh_if_needed()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # 没有任何请求发生过，缓存已经就绪。
        assert cache.peek() is not None
        first_stored_at = cache._stored_at

        # 版本变了（新一轮 curation run）——keeper 的下一跳应重新水合。
        with db.get_conn(path) as write_conn:
            write_conn.execute(
                """
                INSERT INTO curation_runs (id, ruleset_version, weights_json, threshold,
                                           input_eval_ids, output_curated_ids, created_at)
                VALUES ('run-2', 'r1', '{}', 0, '[]', '[]', '2026-08-02T00:00:00Z')
                """
            )
            write_conn.commit()
        # 用户裁定：重算窗口内继续供旧数据，不留空白（ADR-060 Decision §3）。
        assert cache.peek() is not None, "版本变了就拒供旧值 = 每 15 分钟一次空白窗口"

        cache._refresh_if_needed()
        assert cache.peek() is not None, "keeper 未把新版本水合出来"
        assert cache._stored_at > first_stored_at
    finally:
        conn.close()


def test_old_data_keeps_serving_across_the_whole_rehydration_window(tmp_path: Path) -> None:
    """这条钉住的正是用户报的那个症状不再出现。

    生产每 15 分钟一轮 curation。版本一变就拒供旧值时，keeper 还要一次轮询
    （≤10s）加一次水合（3.5–6.1s）才补得上，那 4–16 秒里到达的访客看到的就是
    空热点区。这里模拟"版本已变、新数据尚未就绪"的整个窗口。
    """
    path = tmp_path / "window.db"
    _seed(path)
    # `_refresh_if_needed` 按**真实**当前时钟取窗口（它是 keeper 的入口，不接受
    # 注入的 now），所以样本时间必须相对真实 now，否则候选集是空的——空列表也
    # 不是 None，断言会静默地什么都没测到。
    recent = _iso(datetime.now(UTC) - timedelta(hours=1))
    with db.get_conn(path) as conn:
        _add_item(conn, "item-1", published_at=recent, fetched_at=recent)
        conn.commit()

    cache = hot_cache.HotCandidateCache()
    cache.bind(path)
    cache._refresh_if_needed()
    served_before = cache.peek()
    assert served_before, "前置条件不成立：候选集本身就是空的，后面的断言测不到东西"

    with db.get_conn(path) as write_conn:
        write_conn.execute(
            """
            INSERT INTO curation_runs (id, ruleset_version, weights_json, threshold,
                                       input_eval_ids, output_curated_ids, created_at)
            VALUES ('run-2', 'r1', '{}', 0, '[]', '[]', '2026-08-02T00:00:00Z')
            """
        )
        write_conn.commit()

    # keeper 尚未跑到——窗口期内的每一次请求都必须拿到数据。
    for _ in range(5):
        assert cache.peek() == served_before

    # 但硬上界仍然管用：超过 max_stale 就不再供了，无论版本匹不匹配。
    cache._stored_at = time.monotonic() - hot_cache.MAX_STALE_SECONDS - 1
    assert cache.peek() is None


def test_the_keeper_skips_hydration_when_nothing_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询本身必须便宜：没变化时只跑版本查询，不碰那 3.5–6 秒的水合。"""
    path = tmp_path / "keeper-skip.db"
    _seed(path)
    cache = hot_cache.HotCandidateCache()
    cache.bind(path)
    cache._refresh_if_needed()

    hydrations: list[int] = []
    real = hot_cache.compute_candidates

    def counting(conn: sqlite3.Connection, now: datetime) -> list[dict[str, object]]:
        hydrations.append(1)
        return real(conn, now)

    monkeypatch.setattr(hot_cache, "compute_candidates", counting)
    for _ in range(3):
        cache._refresh_if_needed()

    assert hydrations == []


# --------------------------------------------------------------------------
# 4. 首页 SSR：命中才直出，未命中不拖住首页
# --------------------------------------------------------------------------


@pytest.fixture
def home_client(tmp_path: Path) -> TestClient:
    path = tmp_path / "home.db"
    _seed(path)
    with db.get_conn(path) as conn:
        _add_item(conn, "item-1", published_at=_iso(NOW - timedelta(hours=1)), fetched_at=_iso(NOW - timedelta(hours=1)))
        conn.commit()
    return TestClient(create_app(path))


def test_home_ssr_emits_hot_topics_when_the_cache_is_warm(
    home_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        curated_routes.hot_cache.HOT_CANDIDATE_CACHE,
        "peek",
        lambda: [
            {
                "id": "item-1",
                "title_zh": "服务端直出的热点",
                "url": "https://example.invalid/i/item-1",
                "published_at": _iso(datetime.now(UTC) - timedelta(hours=1)),
                "fetched_at": _iso(datetime.now(UTC) - timedelta(hours=1)),
                "weighted_score": 5.0,
                "related_discussions": [],
            }
        ],
    )

    soup = BeautifulSoup(home_client.get("/").text, "html.parser")
    box = soup.select_one("#hot-topics")

    assert box.get("data-loaded") == "true", "SSR 命中后必须标记已加载，否则 CSR 会覆盖首屏内容"
    assert box.get("aria-busy") is None
    assert box.select_one(".hot-topics-link").get_text(strip=True) == "服务端直出的热点"


def test_home_ssr_omits_the_block_and_never_computes_when_the_cache_is_cold(
    home_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阻塞计算桩：若首页曾同步等待热点计算，这个测试会挂到超时而不是通过。"""

    def blocking_compute(conn: sqlite3.Connection, now: datetime) -> list[dict[str, object]]:
        time.sleep(30)
        return []

    monkeypatch.setattr(hot_cache, "compute_candidates", blocking_compute)
    curated_routes.hot_cache.HOT_CANDIDATE_CACHE.reset_for_tests()

    started = time.monotonic()
    response = home_client.get("/")
    elapsed = time.monotonic() - started

    soup = BeautifulSoup(response.text, "html.parser")
    box = soup.select_one("#hot-topics")
    assert response.status_code == 200
    assert elapsed < 5.0, f"首页 TTFB 被热点计算拖住了（{elapsed:.1f}s）"
    assert box.get("data-loaded") is None
    assert box.get("aria-busy") == "true"
    assert not box.select(".hot-topics-link")
