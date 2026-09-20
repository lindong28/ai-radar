#!/usr/bin/env python3
"""Bound collection jobs and independently watch their durable outputs (no LLM calls)."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def now() -> datetime:
    return datetime.now(UTC)


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def wait_group_gone(process: subprocess.Popen[Any], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        process.poll()  # Reap the leader, but do not confuse it with the whole group.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # Not proof of disappearance; only ESRCH establishes that.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def terminate_group(process: subprocess.Popen[Any]) -> None:
    # Each command owns a new session; never signal the caller's process group.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.poll()
            return
        except PermissionError:
            # macOS can return EPERM during group exit. Persistent denial is fatal.
            if wait_group_gone(process, 2):
                return
            raise
        if wait_group_gone(process, 2):
            return
    raise TimeoutError('collector process group did not exit after SIGKILL')


def run_bounded(command: list[str], *, budget: float, attempts: int, delay: float,
                environment: dict[str, str] | None = None,
                environment_factory: Callable[[], dict[str, str]] | None = None) -> int:
    deadline = time.monotonic() + budget
    code = 1
    for attempt in range(attempts):
        if environment_factory is not None:
            environment = environment_factory()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 124
        print(f'采集尝试 {attempt + 1}/{attempts}，剩余预算 {int(remaining)} 秒', flush=True)
        process = subprocess.Popen(command, start_new_session=True, env=environment)
        previous_handlers = {}

        def interrupted(signum: int, _frame: Any) -> None:
            raise SystemExit(128 + signum)

        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.signal(sig, interrupted)
        try:
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                code = 124
        finally:
            try:
                terminate_group(process)
            finally:
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)
        if code == 0:
            return 0
        if attempt + 1 < attempts and deadline - time.monotonic() > delay:
            time.sleep(delay)
    return code if code > 0 else 128 - code


def expected_days(start: str, instant: datetime) -> list[str]:
    day = datetime.fromisoformat(start).astimezone(UTC)
    end = instant.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    result = []
    while day < end:
        result.append(day.date().isoformat())
        day += timedelta(days=1)
    return result


def window_path(day: str) -> str:
    end = datetime.fromisoformat(day) + timedelta(days=1)
    return f'windows/{day}T000000Z--{end.date().isoformat()}T000000Z/manifest.json'


def aihot_environment(state: dict[str, Any], root: Path, instant: datetime) -> dict[str, str]:
    # Completed days stay recorded even after their raw evidence legally expires.
    days = expected_days(state['capture_start'], instant)
    missing = [day for day in days if day not in state['delivered_days']]
    available_start = (instant - timedelta(days=6)).date().isoformat()
    recoverable = [day for day in missing if day >= available_start]
    env = dict(os.environ)
    env.update(AIHOT_CAPTURE_RESILIENT='1', AIHOT_CAPTURE_FILL_MISSING='1',
               AIHOT_CAPTURE_END=instant.strftime('%Y-%m-%dT00:00:00Z'))
    if recoverable:
        env['AIHOT_CAPTURE_START'] = recoverable[0] + 'T00:00:00Z'
        # Only skip source traffic after replay validation. Publication still runs.
        from airadar.eval.aihot_dataset import validate_persisted_artifact
        for day in recoverable:
            path = window_path(day)
            if not (root / path).is_file():
                break
            validate_persisted_artifact(root, path)
        else:
            env['AIHOT_CAPTURE_SKIP_FETCH'] = '1'
            return env
        env['AIHOT_CAPTURE_SKIP_FETCH'] = '0'
    else:
        env['AIHOT_CAPTURE_SKIP_FETCH'] = '1'
    return env


def run_job(args: argparse.Namespace) -> int:
    directory = args.state_dir
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f'{args.kind}.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('本轮未执行：同类采集仍在运行；独立健康检查继续监测归档。')
            return 0
        path = directory / f'{args.kind}.json'
        state = read(path) if path.exists() else {}
        if args.kind == 'aihot' and not state.get('capture_start'):
            raise ValueError('AIHOT must be explicitly initialized with --capture-start')
        # last_exit remains the previous terminal result until this attempt finishes.
        state.update(last_started_at=now().isoformat())
        save(path, state)
        code = 70
        try:
            environment_factory = (lambda: aihot_environment(state, args.aihot_root, now())) if args.kind == 'aihot' else None
            result = run_bounded(args.command, budget=args.budget, attempts=args.attempts,
                                 delay=args.delay, environment_factory=environment_factory)
            if result == 0:
                if args.kind == 'aihot':
                    from airadar.eval.aihot_dataset import validate_persisted_artifact
                    delivered = list(state['delivered_days'])
                    for day in expected_days(state['capture_start'], now()):
                        if day in delivered or not (args.aihot_root / window_path(day)).is_file():
                            continue
                        validate_persisted_artifact(args.aihot_root, window_path(day))
                        delivered.append(day)
                    state['delivered_days'] = delivered
                state['last_success_at'] = now().isoformat()
            code = result
        except Exception as error:
            print(f'采集监督异常（{type(error).__name__}）；终态记为70，独立健康检查负责告警。', flush=True)
        finally:
            state.update(last_finished_at=now().isoformat(), last_exit=code)
            save(path, state)
        print('本轮采集命令完成；归档完整性另由健康检查确认。' if code == 0 else
              f'本轮采集失败（退出码 {code}）；已留存数据保留，下个调度重试。', flush=True)
        return code


def supervisor_problems(kind: str, state: dict[str, Any], instant: datetime) -> list[str]:
    label = 'AIHOT' if kind == 'aihot' else 'Radar'
    code = state.get('last_exit')
    if code == 70 or (kind == 'aihot' and code not in (None, 0)):
        return [f'{label} 上次采集监督失败（退出码 {code}）；等待后续完成恢复']
    started = state.get('last_started_at')
    finished = state.get('last_finished_at')
    if started and (not finished or datetime.fromisoformat(started) > datetime.fromisoformat(finished)):
        # Existing execution budget plus one health-check interval, not a new cadence.
        limit = (2400 if kind == 'aihot' else 840) + 300
        if (instant - datetime.fromisoformat(started)).total_seconds() > limit:
            return [f'{label} 采集监督超过执行预算仍未记录终态；请检查监督日志']
    return []


RADAR_SOURCE_FAILURE_ROUNDS = 3


RADAR_BROAD_FAILURE_FRACTION = 0.5
# notify_transition matches on this to tell a blackout apart from a flaky feed.
# Producer and consumer share the constant so the two cannot drift.
RADAR_BROAD_PREFIX = 'Radar 本轮 '


def read_streaks(path: Path) -> dict[str, Any]:
    """Load the streak file, treating anything unreadable as "no history".

    Never raises. This function is called from inside `radar_problems`, and main()
    turns any exception there into a single `归档健康状态无法核实` line that *replaces*
    the whole Radar list — the staleness check, the "no archive at all" check and the
    manifest hash verification all vanish with it. `notify_transition` then dedups on a
    bare boolean, so that substitution costs one page and buys permanent silence. A
    truncated write or a hand edit must not be able to do that, and since the file is
    pure derived state, forgetting it is always safe: the worst case is that each
    currently-failing source waits RADAR_SOURCE_FAILURE_ROUNDS rounds again.
    """
    try:
        loaded = read(path)
    except Exception:
        return {}
    if not isinstance(loaded, dict) or not isinstance(loaded.get('counts'), dict):
        return {}
    counts: dict[str, int] = {}
    for slug, value in loaded['counts'].items():
        try:
            counts[str(slug)] = int(value)
        except (TypeError, ValueError):
            continue
    return {'run_id': loaded.get('run_id'), 'counts': counts}


def write_streaks(path: Path, run_id: object, counts: dict[str, int]) -> None:
    """Persist streaks atomically under a process-unique temp name. Never raises.

    `save()` derives its temp path from the target alone, so two health checks writing
    at once race on one filename and can leave a spliced, unparseable file behind —
    which is exactly the input read_streaks must survive, so this closes the loop rather
    than relying on it.
    """
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('w') as stream:
            json.dump({'run_id': run_id, 'counts': counts}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass


def sustained_source_failures(path: Path, failed: list[str], run_id: object) -> list[str]:
    """Report only sources that have missed RADAR_SOURCE_FAILURE_ROUNDS capture rounds.

    One round's miss is not a user-visible failure — the next round collects the source
    — but across ~160 sources at least one misses in most rounds, which made this the
    noisiest alert on the host. Measured over the 613 rounds recorded in
    logs/collection-supervisor/health.log: 84 incident transitions, and 97 of the
    failing rounds were `claude_youtube` alone flickering between HTTP 200 and 404. At
    three rounds that becomes 20 transitions while every multi-round outage in the same
    history still fires — the 86-round one that took out the x_* batch, and a 4-round
    one.

    Counting is keyed to `run_id`, not to invocations. The health check runs every five
    minutes while capture rounds land every ~5.5 on average, so 20% of manifests get
    sampled three or more times; counting invocations would let a single round's miss
    reach the threshold on its own and page with a message claiming three rounds in a
    row. Re-reading the same manifest is therefore a no-op that returns the same answer.

    Only currently-failing sources are written back, so a source that recovers restarts
    at zero and the file stays the size of the failing set.
    """
    previous = read_streaks(path)
    # read_streaks returns {} for no-history, so a None run_id would equal that
    # sentinel every round, freeze the counts at zero, and silence the alert forever.
    if run_id is not None and previous.get('run_id') == run_id:
        counts = previous.get('counts', {})
    else:
        counts = {slug: int(previous.get('counts', {}).get(slug, 0)) + 1 for slug in failed}
    write_streaks(path, run_id, counts)
    return [slug for slug in failed if counts.get(slug, 0) >= RADAR_SOURCE_FAILURE_ROUNDS]


def radar_problems(root: Path, instant: datetime, sources_path: Path | None = None,
                   streak_path: Path | None = None) -> list[str]:
    paths = sorted((root / 'runs').glob('*/manifest.json'), reverse=True)
    latest = None
    for path in paths:
        manifest = read(path)
        if manifest.get('state') == 'completed':
            latest = manifest
            break
    if latest is None:
        return ['Radar 尚无已完成的原始输入归档']
    age = (instant - datetime.fromisoformat(latest['completed_at'])).total_seconds()
    if age < -60 or age > 1200:
        return [f'Radar 原始输入归档已落后 {int(age / 60)} 分钟（阈值20分钟）']
    from airadar.fetcher.raw_capture import read_run
    read_run(root, latest['run_id'])
    expected = set(latest['sources'])
    if sources_path is not None:
        configured = tomllib.loads(sources_path.read_text())['source']
        expected = {s['slug'] for s in configured if s.get('enabled', True)
                    and not s.get('paused', False) and not s.get('wechat_only', False)}
    if not expected:
        return ['Radar 未找到应采集来源，不能判定归档完整']
    failed = sorted(slug for slug in expected
                    if latest['sources'].get(slug, {}).get('status') not in {'success', 'not_modified'})
    if streak_path is None:
        # No debounce state to read: report what this round saw, and say only that.
        return [f'Radar 本轮未收全来源：{", ".join(failed)}'] if failed else []
    sustained = sustained_source_failures(streak_path, failed, latest.get('run_id'))
    # A round that loses most of the sources at once is a different event from a flaky
    # feed, and waiting three rounds for it would hide the thing most worth seeing: the
    # manifest still completes on time, so the staleness check says nothing, and a
    # DNS/proxy/disk failure that lasts a single round would never be reported at all.
    # Two sources is the floor so that a tiny configured set cannot make every single
    # miss "broad".
    if len(failed) >= max(2, round(len(expected) * RADAR_BROAD_FAILURE_FRACTION)):
        return [f'{RADAR_BROAD_PREFIX}{len(failed)}/{len(expected)} 个来源未收到：{", ".join(failed)}']
    return [f'Radar 连续 {RADAR_SOURCE_FAILURE_ROUNDS} 轮以上未收全来源：{", ".join(sustained)}'] if sustained else []


def aihot_problems(state: dict[str, Any], instant: datetime, root: Path | None = None) -> list[str]:
    # Grace is measured from each day's end, not from the last successful job.
    due = expected_days(state['capture_start'], instant - timedelta(hours=2))
    missing = [day for day in due if day not in state['delivered_days']]
    if root is not None and state['delivered_days']:
        latest = max(state['delivered_days'])
        if latest >= (instant - timedelta(days=30)).date().isoformat():
            from airadar.eval.aihot_dataset import validate_persisted_artifact
            validate_persisted_artifact(root, window_path(latest))
    return [f'AIHOT UTC日窗尚未完成采集及发布：{", ".join(missing)}'] if missing else []


def notify_transition(path: Path, key: str, problems: list[str], notifier: str) -> None:
    previous = read(path) if path.exists() else {}
    # Stable incident identity: timestamps and rotating log tails never create new pages.
    # It is deliberately coarse, with one exception. A round that loses most of the
    # sources is a different event from a flaky feed, and a bare boolean cannot say so:
    # replaying the archive, 3 of 6 complete 160/160 blackouts sent nothing at all
    # because a single-source incident already held the channel open — and the source
    # holding it was `claude_youtube`, the flaky one this debounce exists to suppress.
    # Escalation therefore joins the identity so it can break through, and de-escalation
    # back to an ordinary incident is a transition too, which is correct: the blackout
    # ending is worth saying even while the ordinary incident continues.
    incident = bool(problems)
    escalated = any(problem.startswith(RADAR_BROAD_PREFIX) for problem in problems)
    identity = f'{incident}:{escalated}'
    if identity == previous.get('identity', f"{bool(previous.get('incident', False))}:False"):
        return
    title = 'AI Radar 评测数据采集异常' if incident else 'AI Radar 评测数据采集已恢复'
    message = '\n'.join(problems) if incident else f'{key} 本次健康检查通过；旧缺口未被补造。'
    message += '\n请查看 logs/collection-supervisor 与 docs/operations/continuous-eval-data.md。'
    result = subprocess.run([notifier, '--alert', '--dedup-key', key, '--dedup-text', identity,
                             '--title', title, message], timeout=45, check=False)
    if result.returncode:
        raise RuntimeError('采集告警投递失败；下次检查继续重试')
    save(path, {'incident': incident, 'identity': identity, 'sent_at': now().isoformat()})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--aihot-root', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init')
    init.add_argument('--capture-start', required=True)
    run = sub.add_parser('run')
    run.add_argument('--kind', choices=['radar', 'aihot'], required=True)
    run.add_argument('--budget', type=float, required=True)
    run.add_argument('--attempts', type=int, default=3)
    run.add_argument('--delay', type=float, default=15)
    run.add_argument('command', nargs=argparse.REMAINDER)
    health = sub.add_parser('health')
    health.add_argument('--radar-root', type=Path, required=True)
    health.add_argument('--sources-path', type=Path)
    health.add_argument('--notifier', default=str(Path.home() / '.local/bin/im-notify'))
    health.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    if args.action == 'init':
        start = datetime.fromisoformat(args.capture_start).astimezone(UTC)
        if start.time() != datetime.min.time():
            parser.error('capture-start must be UTC midnight')
        path = args.state_dir / 'aihot.json'
        if path.exists():
            parser.error('already initialized; refusing to forget pending days')
        save(path, {'capture_start': start.isoformat(), 'delivered_days': []})
        print('已设置未来采集起点；没有启动任务或回填此前缺口。')
        return 0
    if args.action == 'run':
        if args.command[:1] == ['--']:
            args.command.pop(0)
        if not args.command or args.budget <= 0 or args.attempts < 1 or args.delay < 0:
            parser.error('provide a command, positive budget/attempts and nonnegative delay')
        return run_job(args)
    problems = []
    by_collector = {}
    for label, check in (
        ('Radar', lambda: radar_problems(args.radar_root, now(), args.sources_path,
                                         args.state_dir / 'radar-source-streaks.json')),
        ('AIHOT', lambda: aihot_problems(read(args.state_dir / 'aihot.json'), now(), args.aihot_root)),
    ):
        try:
            by_collector[label] = check()
            state_path = args.state_dir / f'{label.lower()}.json'
            by_collector[label].extend(supervisor_problems(label.lower(), read(state_path), now()))
        except Exception as error:
            by_collector[label] = [f'{label} 归档健康状态无法核实（{type(error).__name__}）']
        problems.extend(by_collector[label])
    print('原始数据采集异常：\n' + '\n'.join(problems) if problems else
          '原始数据采集检查通过：Radar近期归档可回读，AIHOT启用后已到期日窗均已交付。')
    if args.notify:
        args.state_dir.mkdir(parents=True, exist_ok=True)
        with (args.state_dir / 'health.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for label, issues in by_collector.items():
                notify_transition(args.state_dir / f'notification-{label}.json',
                                  f'ai-radar-raw-collection-{label}', issues, args.notifier)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
