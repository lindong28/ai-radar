from __future__ import annotations

import importlib.util
import json
import os
import signal
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize('denied_signal', [signal.SIGTERM, signal.SIGKILL])
def test_cleanup_accepts_permission_race_only_after_group_disappears(monkeypatch, denied_signal):
    ticks = iter(i * 0.25 for i in range(100))
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(supervisor.time, 'sleep', lambda _: None)
    denied = False
    confirmed = False

    def killpg(pid, sig):
        nonlocal denied, confirmed
        assert pid == 12345
        if sig == denied_signal:
            denied = True
            raise PermissionError('injected exit race')
        if sig == 0 and denied:
            confirmed = True
            raise ProcessLookupError('group disappeared')

    monkeypatch.setattr(supervisor.os, 'killpg', killpg)
    process = SimpleNamespace(pid=12345, poll=lambda: 0, wait=lambda **_: 0)
    supervisor.terminate_group(process)
    assert denied and confirmed


def test_cleanup_does_not_swallow_persistent_permission_failure(monkeypatch):
    ticks = iter(i * 0.25 for i in range(100))
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(supervisor.time, 'sleep', lambda _: None)

    def denied(*_):
        raise PermissionError('persistent permission failure')

    monkeypatch.setattr(supervisor.os, 'killpg', denied)
    process = SimpleNamespace(pid=12345, poll=lambda: 0, wait=lambda **_: 0)
    with pytest.raises(PermissionError):
        supervisor.terminate_group(process)


@pytest.mark.parametrize('ignore_term', [False, True])
def test_successful_leader_still_cleans_descendant(tmp_path, ignore_term):
    marker = tmp_path / 'ready'
    child = ('import signal,time,pathlib,os; '
             f'signal.signal(signal.SIGTERM, signal.{"SIG_IGN" if ignore_term else "SIG_DFL"}); '
             f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(5)')
    parent = ('import subprocess,sys,time,pathlib; '
              f'subprocess.Popen([sys.executable,"-c",{child!r}]); '
              f'p=pathlib.Path({str(marker)!r}); '
              '\nwhile not p.exists(): time.sleep(0.01)')
    assert supervisor.run_bounded([sys.executable, '-c', parent], budget=6, attempts=1, delay=0) == 0
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cleanup_failure_restores_handlers(monkeypatch):
    original = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def fail(_):
        raise PermissionError('injected cleanup failure')

    monkeypatch.setattr(supervisor, 'terminate_group', fail)
    try:
        with pytest.raises(PermissionError):
            supervisor.run_bounded([sys.executable, '-c', 'pass'], budget=3, attempts=1, delay=0)
        assert {sig: signal.getsignal(sig) for sig in original} == original
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize('kind', ['radar', 'aihot'])
def test_internal_failure_is_durable_and_retry_start_is_not_recovery(tmp_path, monkeypatch, kind):
    path = tmp_path / f'{kind}.json'
    state = {'last_exit': 70, 'last_success_at': 'earlier',
             'capture_start': '2026-09-17T00:00:00+00:00', 'delivered_days': []}
    supervisor.save(path, state)

    def fail(*_, **__):
        assert supervisor.read(path)['last_exit'] == 70
        raise PermissionError('fixture secret must not appear in output')

    monkeypatch.setattr(supervisor, 'run_bounded', fail)
    args = SimpleNamespace(kind=kind, state_dir=tmp_path, aihot_root=tmp_path,
                           command=['unused'], budget=1, attempts=1, delay=0)
    assert supervisor.run_job(args) == 70
    saved = supervisor.read(path)
    assert saved['last_exit'] == 70
    assert saved['last_finished_at'] >= saved['last_started_at']
    assert saved['last_success_at'] == 'earlier'
    monkeypatch.setattr(supervisor, 'run_bounded', lambda *_, **__: 0)
    monkeypatch.setattr(supervisor, 'now', lambda: datetime(2026, 9, 17, 12, tzinfo=UTC))
    assert supervisor.run_job(args) == 0
    assert supervisor.read(path)['last_exit'] == 0
    assert supervisor.read(path)['last_success_at'] != 'earlier'


@pytest.mark.parametrize('kind,code,expected', [('radar', 70, 1), ('radar', 1, 0),
                                             ('aihot', 70, 1), ('aihot', 1, 1), ('aihot', 0, 0)])
def test_health_entrypoint_observes_supervisor_failures(tmp_path, monkeypatch, capsys, kind, code, expected):
    instant = datetime(2026, 9, 17, 12, tzinfo=UTC)
    for collector in ('radar', 'aihot'):
        supervisor.save(tmp_path / f'{collector}.json', {
            'last_exit': code if collector == kind else 0,
            'last_started_at': instant.isoformat(), 'last_finished_at': instant.isoformat(),
            'capture_start': '2026-09-17T00:00:00+00:00', 'delivered_days': []})
    monkeypatch.setattr(supervisor, 'now', lambda: instant)
    monkeypatch.setattr(supervisor, 'radar_problems', lambda *_: [])
    monkeypatch.setattr(sys, 'argv', ['supervisor', '--state-dir', str(tmp_path),
                                    'health', '--radar-root', str(tmp_path)])
    assert supervisor.main() == expected
    output = capsys.readouterr().out
    assert ('监督' in output) == bool(expected)


@pytest.mark.parametrize('kind,age,expected', [('radar', 1141, True), ('radar', 100, False),
                                            ('aihot', 2701, True), ('aihot', 100, False)])
def test_unfinished_supervisor_deadline(kind, age, expected):
    from datetime import timedelta

    instant = datetime(2026, 9, 17, 12, tzinfo=UTC)
    state = {'last_exit': 0, 'last_started_at': (instant - timedelta(seconds=age)).isoformat(),
             'last_finished_at': (instant - timedelta(days=1)).isoformat()}
    assert bool(supervisor.supervisor_problems(kind, state, instant)) is expected


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
