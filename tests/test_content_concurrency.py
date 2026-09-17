import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from airadar.fetcher import content


def test_extraction_does_not_share_parser_concurrently(monkeypatch):
    """trafilatura owns one HTML_PARSER; network workers must not overlap extraction."""
    active = peak = 0
    counter_lock = Lock()
    start = Barrier(8)

    def extract(text, **kwargs):
        nonlocal active, peak
        with counter_lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.01)
            return text
        finally:
            with counter_lock:
                active -= 1

    def work(index):
        start.wait(timeout=5)
        return content.clean_content(f"item {index}")

    monkeypatch.setattr(content.trafilatura, "extract", extract)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(work, range(8))) == [f"item {i}" for i in range(8)]
    assert peak == 1


def test_real_extraction_parallel_matches_serial():
    inputs = [
        None, "", "Plain news headline", "<p>普通新闻，不应由采集器筛除。</p>",
        "<article><h1>Title</h1><p>" + "Technical details and observations. " * 40 + "</p></article>",
        "<div><p>Broken markup <b>still present", "<table><tr><td>Data</td></tr></table>",
    ]
    expected = [content.clean_content(value, fallback="fallback") for value in inputs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        actual = list(pool.map(lambda value: content.clean_content(value, fallback="fallback"), inputs * 8))
    assert actual == expected * 8
