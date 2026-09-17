from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from airadar.fetcher import http_client
from airadar.sources.loader import SourceConfig

SPEC = importlib.util.spec_from_file_location(
    'collection_supervisor', Path(__file__).resolve().parents[1] / 'scripts/collection_supervisor.py')
assert SPEC and SPEC.loader
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


def test_retry_recovers_without_changing_command(tmp_path):
    counter = tmp_path / 'counter'
    command = [sys.executable, '-c',
               'import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
               'n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); '
               'sys.exit(0 if n==2 else 1)', str(counter)]
    assert supervisor.run_bounded(command, budget=5, attempts=3, delay=0) == 0
    assert counter.read_text() == '2'


def test_retry_is_bounded(tmp_path):
    counter = tmp_path / 'counter'
    command = [sys.executable, '-c',
               'import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
               'p.write_text(p.read_text()+"x" if p.exists() else "x"); sys.exit(9)', str(counter)]
    assert supervisor.run_bounded(command, budget=5, attempts=3, delay=0) == 9
    assert counter.read_text() == 'xxx'


def test_aihot_publish_retry_recomputes_real_validated_windows(tmp_path, monkeypatch):
    from test_aihot_dataset import WINDOW_ONE, WINDOW_TWO, make_capture_writer

    from airadar.eval import aihot_dataset as ds

    writer, _, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    state = {'capture_start': WINDOW_ONE[0], 'delivered_days': []}
    instant = datetime.fromisoformat(WINDOW_TWO[1])
    flags = []

    def prepare():
        env = supervisor.aihot_environment(state, root, instant)
        flags.append(env['AIHOT_CAPTURE_SKIP_FETCH'])
        if len(flags) == 1:
            # Real producer publishes bytes before the first command's Git failure.
            writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
        return env

    command = [sys.executable, '-c',
               'import os,sys; sys.exit(0 if os.environ["AIHOT_CAPTURE_SKIP_FETCH"]=="1" else 1)']
    assert supervisor.run_bounded(command, budget=5, attempts=3, delay=0,
                                  environment_factory=prepare) == 0
    assert flags == ['0', '1']


def test_timeout_terminates_grandchild(tmp_path):
    pidfile = tmp_path / 'child'
    command = [sys.executable, '-c',
               'import subprocess,sys,time,pathlib; '
               'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
               'pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)', str(pidfile)]
    started = time.monotonic()
    assert supervisor.run_bounded(command, budget=0.5, attempts=3, delay=0) == 124
    assert time.monotonic() - started < 4
    pid = int(pidfile.read_text())
    # Reparenting/reaping can lag delivery of SIGKILL.
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail('collector descendant survived its deadline')


def test_midnight_does_not_forget_previous_missing_day():
    state = {'capture_start': '2026-09-17T00:00:00+00:00', 'delivered_days': ['2026-09-18']}
    issues = supervisor.aihot_problems(state, datetime(2026, 9, 19, 3, tzinfo=UTC))
    assert '2026-09-17' in issues[0]
    assert '2026-09-18' not in issues[0]
    state['delivered_days'].append('2026-09-17')
    assert supervisor.aihot_problems(state, datetime(2026, 9, 19, 3, tzinfo=UTC)) == []


def test_aihot_grace_and_expired_completed_days():
    state = {'capture_start': '2026-09-17T00:00:00+00:00', 'delivered_days': []}
    assert supervisor.aihot_problems(state, datetime(2026, 9, 18, 1, tzinfo=UTC)) == []
    assert supervisor.aihot_problems(state, datetime(2026, 9, 18, 2, tzinfo=UTC))
    instant = datetime(2026, 11, 1, 3, tzinfo=UTC)
    state['delivered_days'] = supervisor.expected_days(state['capture_start'], instant)
    # No old raw bytes are required to prove a previously delivered day stays delivered.
    assert supervisor.aihot_problems(state, instant) == []


def test_notice_failure_retries_and_recovery_notifies(monkeypatch, tmp_path):
    calls = []
    codes = iter([1, 0, 0])

    def send(command, **kwargs):
        calls.append(command)
        return type('Result', (), {'returncode': next(codes)})()

    monkeypatch.setattr(supervisor.subprocess, 'run', send)
    state = tmp_path / 'notify.json'
    with pytest.raises(RuntimeError):
        supervisor.notify_transition(state, 'test', ['problem'], 'notify')
    assert not state.exists()
    supervisor.notify_transition(state, 'test', ['problem'], 'notify')
    supervisor.notify_transition(state, 'test', ['problem changed timestamp'], 'notify')
    supervisor.notify_transition(state, 'test', [], 'notify')
    assert len(calls) == 3
    assert json.loads(state.read_text())['incident'] is False


@pytest.mark.parametrize('failures', [[503, 200], [429, 200], ['timeout', 200], [404], [401], [503, 503, 503]])
def test_http_retry_only_transient(monkeypatch, failures):
    calls = []
    remaining = iter(failures)

    class Client:
        def get(self, url, **kwargs):
            calls.append(url)
            result = next(remaining)
            if result == 'timeout':
                raise httpx.ConnectTimeout('fixture')
            return httpx.Response(result, content=b'<rss/>', request=httpx.Request('GET', url))

    @contextmanager
    def client(**kwargs):
        yield Client()

    monkeypatch.setattr(http_client, 'selector_httpx_client', client)
    monkeypatch.setattr(http_client.time, 'sleep', lambda _: None)
    source = SourceConfig(slug='fixture', name='fixture', url='https://example.com/rss', tier='T2')
    with sqlite3.connect(':memory:') as conn:
        if failures[-1] == 200:
            assert http_client.fetch_feed(source, conn).body == b'<rss/>'
        else:
            with pytest.raises(httpx.HTTPStatusError):
                http_client.fetch_feed(source, conn)
    assert len(calls) == len(failures)
