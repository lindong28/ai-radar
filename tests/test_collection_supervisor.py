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


def _radar_root(tmp_path, sources, run_id='r1'):
    """A radar tree whose newest completed manifest carries the given source states."""
    run = tmp_path / 'radar' / 'runs' / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / 'manifest.json').write_text(json.dumps({
        'state': 'completed', 'completed_at': datetime.now(UTC).isoformat(),
        'run_id': run_id, 'sources': sources,
    }))
    return tmp_path / 'radar'


@pytest.fixture
def stub_read_run(monkeypatch):
    monkeypatch.setitem(sys.modules, 'airadar.fetcher.raw_capture',
                        SimpleNamespace(read_run=lambda *_: None))


def test_a_source_must_miss_several_capture_rounds_before_it_is_reported(tmp_path):
    # One round's miss is normal at ~160 sources and the next round collects it;
    # paging on the first miss is what made this alert the noisiest one on the host.
    streaks = tmp_path / 'streaks.json'
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r1') == []
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r2') == []
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r3') == ['claude_youtube']
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r4') == ['claude_youtube']


def test_re_reading_one_manifest_does_not_advance_the_count(tmp_path):
    # The health check runs every 5 minutes while capture rounds land every ~5.5, so a
    # fifth of manifests are sampled three or more times. Counting invocations would let
    # a single round's miss reach the threshold on its own — and then say "三轮".
    streaks = tmp_path / 'streaks.json'
    for _ in range(5):
        assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r1') == []
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r2') == []
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r3') == ['claude_youtube']


def test_a_recovered_source_restarts_its_streak(tmp_path):
    # "Consecutive", not "cumulative": without the reset a source that misses one round
    # every few hours would eventually cross the threshold and alert anyway.
    streaks = tmp_path / 'streaks.json'
    supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r1')
    supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r2')
    supervisor.sustained_source_failures(streaks, [], 'r3')          # recovered
    assert json.loads(streaks.read_text())['counts'] == {}
    assert supervisor.sustained_source_failures(streaks, ['claude_youtube'], 'r4') == []


def test_sources_are_counted_independently(tmp_path):
    streaks = tmp_path / 'streaks.json'
    for run_id in ('r1', 'r2', 'r3'):
        supervisor.sustained_source_failures(streaks, ['claude_youtube'], run_id)
    assert supervisor.sustained_source_failures(
        streaks, ['claude_youtube', 'wx_wechat2rss'], 'r4') == ['claude_youtube']


@pytest.mark.parametrize('content', ['{"counts": {"a": 2}', '', '[]', '{"counts": {"a": "many"}}',
                                     '{"counts": {"a": null}}', '{"counts": []}',
                                     # the pre-run_id shape this version replaces
                                     '{"a": 2}'])
def test_an_unreadable_streak_file_is_forgotten_rather_than_raised(tmp_path, content):
    # main() turns any exception here into one line that REPLACES the whole Radar list —
    # staleness, "no archive", manifest verification all vanish — and notify_transition
    # dedups on a bare boolean, so that substitution costs one page and buys silence.
    streaks = tmp_path / 'streaks.json'
    streaks.write_text(content)
    assert supervisor.sustained_source_failures(streaks, ['a'], 'r1') == []
    assert json.loads(streaks.read_text())['counts'] == {'a': 1}      # and it self-repairs


def test_radar_problems_reports_a_broad_failure_on_the_first_round(tmp_path, stub_read_run):
    # The manifest completes on time, so the staleness check says nothing; without this
    # a round that loses every source would wait three rounds, or never be seen at all.
    sources = {f's{i}': {'status': 'failed'} for i in range(10)}
    root = _radar_root(tmp_path, sources)
    problems = supervisor.radar_problems(root, datetime.now(UTC), None, tmp_path / 'streaks.json')
    assert problems == [f'Radar 本轮 10/10 个来源未收到：{", ".join(sorted(sources))}']


def test_radar_problems_debounces_a_single_flaky_source(tmp_path, stub_read_run):
    sources = {f's{i}': {'status': 'success'} for i in range(10)}
    sources['bad'] = {'status': 'failed'}
    streaks = tmp_path / 'streaks.json'
    instant = datetime.now(UTC)
    for run_id in ('r1', 'r2'):
        root = _radar_root(tmp_path, sources, run_id)
        assert supervisor.radar_problems(root, instant, None, streaks) == []
    root = _radar_root(tmp_path, sources, 'r3')
    assert supervisor.radar_problems(root, instant, None, streaks) == [
        'Radar 连续 3 轮以上未收全来源：bad']


def test_radar_problems_without_streak_state_claims_only_this_round(tmp_path, stub_read_run):
    sources = {f's{i}': {'status': 'success'} for i in range(10)}
    sources['bad'] = {'status': 'failed'}
    root = _radar_root(tmp_path, sources)
    assert supervisor.radar_problems(root, datetime.now(UTC)) == ['Radar 本轮未收全来源：bad']


def test_main_wires_the_streak_file_into_the_radar_check(tmp_path, monkeypatch):
    # Without this the only main() test stubs radar_problems out entirely, so dropping
    # the streak argument — which silently restores the original noise — would pass.
    # --notify is deliberately omitted so no notifier is ever invoked.
    seen: dict[str, object] = {}

    def fake_radar_problems(root, instant, sources_path=None, streak_path=None):
        seen['streak_path'] = streak_path
        return []

    monkeypatch.setattr(supervisor, 'radar_problems', fake_radar_problems)
    monkeypatch.setattr(supervisor, 'aihot_problems', lambda *_, **__: [])
    monkeypatch.setattr(supervisor, 'supervisor_problems', lambda *_: [])
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(sys, 'argv', [
        'collection_supervisor', '--state-dir', str(state),
        'health', '--radar-root', str(tmp_path / 'radar')])

    supervisor.main()

    assert seen['streak_path'] == state / 'radar-source-streaks.json'


def _fake_notifier(tmp_path):
    """A notifier that records its argv. Nothing here may reach a real channel."""
    log = tmp_path / 'notifier.log'
    script = tmp_path / 'fake-notify'
    script.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_NOTIFY_LOG"\n')
    script.chmod(0o755)
    return script, log


def test_a_blackout_breaks_through_an_open_single_source_incident(tmp_path, monkeypatch):
    # The dedup identity used to be a bare boolean, so a round losing every source sent
    # nothing whenever any incident was already open. Replaying the archive, 3 of 6 real
    # 160/160 blackouts were swallowed that way — by an incident held open by the one
    # flaky feed this debounce exists to suppress.
    script, log = _fake_notifier(tmp_path)
    monkeypatch.setenv('FAKE_NOTIFY_LOG', str(log))
    state = tmp_path / 'notification-Radar.json'

    supervisor.notify_transition(state, 'k', ['Radar 连续 3 轮以上未收全来源：claude_youtube'], str(script))
    assert log.read_text().count('--dedup-text') == 1

    supervisor.notify_transition(state, 'k', [f'{supervisor.RADAR_BROAD_PREFIX}160/160 个来源未收到：…'], str(script))
    body = log.read_text()
    assert body.count('--dedup-key') == 2, 'the blackout must page even though an incident was open'
    assert 'True:True' in body and 'True:False' in body


def test_an_ordinary_incident_does_not_re_page_itself(tmp_path, monkeypatch):
    script, log = _fake_notifier(tmp_path)
    monkeypatch.setenv('FAKE_NOTIFY_LOG', str(log))
    state = tmp_path / 'notification-Radar.json'
    for _ in range(3):
        supervisor.notify_transition(state, 'k', ['Radar 连续 3 轮以上未收全来源：a'], str(script))
    assert log.read_text().count('--dedup-key') == 1


def test_a_manifest_without_a_run_id_still_advances_the_count(tmp_path):
    # read_streaks returns {} for "no history", so a None run_id equal to that sentinel
    # would take the "same manifest" branch every round and freeze counting at zero.
    streaks = tmp_path / 'streaks.json'
    for _ in range(3):
        result = supervisor.sustained_source_failures(streaks, ['a'], None)
    assert result == ['a']
