from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import stat
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE_STAGE_RE = re.compile(r"^run_stage\s+([a-z][a-z0-9_-]*)\b", re.MULTILINE)
_PROVENANCE_TOKEN_RE = re.compile(r"^[0-9A-Fa-f-]{36}$")


def _orchestrated_stages() -> tuple[str, ...]:
    source = (_PROJECT_ROOT / "pipeline.sh").read_text(encoding="utf-8")
    stages = tuple(_PIPELINE_STAGE_RE.findall(source))
    if not stages or len(stages) != len(set(stages)):
        raise RuntimeError("pipeline stage registry is empty or duplicated")
    return stages


ORCHESTRATED_STAGES = _orchestrated_stages()


@dataclass(frozen=True, slots=True)
class StageRegistration:
    canonical_stage: str
    entrypoint: str
    kind: str


STAGE_REGISTRY = (
    tuple(StageRegistration(stage, "pipeline.sh", "orchestrated") for stage in ORCHESTRATED_STAGES)
    + tuple(StageRegistration(stage, stage, "direct") for stage in ORCHESTRATED_STAGES)
    + (StageRegistration("curate", "admin curate", "direct"),)
)


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    generation: str
    sequence: int
    active: tuple[dict[str, object], ...]
    events: tuple[dict[str, object], ...]
    valid: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class LoadClassification:
    load_class: str
    reason: str


class StageLedger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.events_path = root / "events.jsonl"
        self.active_path = root / "active.json"
        self.lock_path = root / "ledger.lock"
        self.generation_path = root / "generation"

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)

    def _initialize_locked(self) -> None:
        if not self.generation_path.exists():
            self.generation_path.write_text(f"{time.time_ns()}-{os.getpid()}\n", encoding="utf-8")
        if not self.active_path.exists():
            self.active_path.write_text("[]\n", encoding="utf-8")
        self.events_path.touch(exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure()
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._initialize_locked()
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read_active(self) -> list[dict[str, object]]:
        value = json.loads(self.active_path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("active ledger is not a list")
        return value

    def _read_events(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("event is not an object")
            rows.append(row)
        return rows

    def _append(self, event: dict[str, object]) -> None:
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _write_active(self, active: list[dict[str, object]]) -> None:
        temporary = self.active_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(active, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.active_path)

    def _transition(self, action: str, lease_id: str, stage: str, entrypoint: str) -> None:
        with self._locked():
            events = self._read_events()
            previous_sequence = events[-1].get("sequence") if events else 0
            if not isinstance(previous_sequence, int):
                raise ValueError("event sequence is not an integer")
            sequence = previous_sequence + 1
            active = self._read_active()
            generation = self.generation_path.read_text(encoding="utf-8").strip()
            lease = {
                "lease_id": lease_id,
                "stage": stage,
                "entrypoint": entrypoint,
                "pid": os.getpid(),
                "started_ns": time.time_ns(),
                "started_monotonic_ns": time.monotonic_ns(),
                "generation": generation,
            }
            if action == "start":
                active.append(lease)
            else:
                existing = next((item for item in active if item.get("lease_id") == lease_id), None)
                if existing is None:
                    raise ValueError("stage end has no active lease")
                lease = existing
                active = [item for item in active if item.get("lease_id") != lease_id]
            event = {
                **lease,
                "action": action,
                "sequence": sequence,
                "observed_ns": time.time_ns(),
                "monotonic_ns": time.monotonic_ns(),
            }
            self._append(event)
            self._write_active(active)

    @contextmanager
    def stage(self, canonical_stage: str, entrypoint: str) -> Iterator[None]:
        lease_id = self.start(canonical_stage, entrypoint)
        try:
            yield
        finally:
            self.end(lease_id)

    def start(self, canonical_stage: str, entrypoint: str) -> str:
        allowed = {(row.canonical_stage, row.entrypoint) for row in STAGE_REGISTRY}
        if (canonical_stage, entrypoint) not in allowed:
            raise ValueError(f"unregistered stage entrypoint: {canonical_stage}/{entrypoint}")
        lease_id = f"{os.getpid()}-{time.time_ns()}"
        self._transition("start", lease_id, canonical_stage, entrypoint)
        return lease_id

    def end(self, lease_id: str) -> None:
        with self._locked():
            active = self._read_active()
            existing = next((item for item in active if item.get("lease_id") == lease_id), None)
        if existing is None:
            raise ValueError("stage end has no active lease")
        self._transition("end", lease_id, str(existing["stage"]), str(existing["entrypoint"]))

    def snapshot(self) -> LedgerSnapshot:
        try:
            with self._locked():
                generation = self.generation_path.read_text(encoding="utf-8").strip()
                events = self._read_events()
                active = self._read_active()
            raw_sequences = [row.get("sequence") for row in events]
            if not all(isinstance(value, int) for value in raw_sequences):
                raise ValueError("event sequence is not an integer")
            sequences = [value for value in raw_sequences if isinstance(value, int)]
            if sequences != list(range(1, len(sequences) + 1)):
                return LedgerSnapshot(
                    generation, sequences[-1] if sequences else 0, tuple(active), tuple(events), False, "sequence_gap"
                )
            if any(row.get("generation") != generation for row in events):
                return LedgerSnapshot(
                    generation,
                    sequences[-1] if sequences else 0,
                    tuple(active),
                    tuple(events),
                    False,
                    "generation_mismatch",
                )
            open_leases: dict[str, dict[str, object]] = {}
            owner_fields = {
                "lease_id",
                "stage",
                "entrypoint",
                "pid",
                "started_ns",
                "started_monotonic_ns",
                "generation",
            }
            for event in events:
                lease_id = event.get("lease_id")
                action = event.get("action")
                if not isinstance(lease_id, str) or action not in {"start", "end"}:
                    raise ValueError("event transition is invalid")
                if action == "start":
                    if lease_id in open_leases:
                        raise ValueError("duplicate stage start")
                    if not owner_fields <= set(event):
                        raise ValueError("stage owner fields are incomplete")
                    open_leases[lease_id] = {field: event[field] for field in owner_fields}
                elif lease_id not in open_leases:
                    raise ValueError("stage end has no start")
                else:
                    expected = open_leases.pop(lease_id)
                    if any(event.get(field) != value for field, value in expected.items()):
                        raise ValueError("stage end owner mismatch")
            active_by_id = {
                str(row.get("lease_id")): {field: row.get(field) for field in owner_fields} for row in active
            }
            if active_by_id != open_leases:
                return LedgerSnapshot(
                    generation,
                    sequences[-1] if sequences else 0,
                    tuple(active),
                    tuple(events),
                    False,
                    "active_event_mismatch",
                )
            return LedgerSnapshot(generation, sequences[-1] if sequences else 0, tuple(active), tuple(events), True)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return LedgerSnapshot("", 0, (), (), False, "ledger_corrupt")


def classify_interval(before: LedgerSnapshot, after: LedgerSnapshot) -> LoadClassification:
    if not before.valid:
        return LoadClassification("unknown", before.error or "ledger_invalid")
    if not after.valid:
        return LoadClassification("unknown", after.error or "ledger_invalid")
    if before.generation != after.generation or after.sequence < before.sequence:
        return LoadClassification("unknown", "generation_mismatch")
    for lease in (*before.active, *after.active):
        pid = lease.get("pid")
        if not isinstance(pid, int) or not _pid_is_live(pid):
            return LoadClassification("unknown", "stale_or_missing_owner")
    interval_events = after.events[before.sequence :]
    if interval_events:
        return LoadClassification("busy", "stage_interval_intersection")
    if before.active or after.active:
        return LoadClassification("busy", "stage_interval_intersection")
    return LoadClassification("idle", "continuous_ledger")


def _pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def validated_pipeline_entrypoint(
    *, ledger_root: Path, environ: dict[str, str] | os._Environ[str], process_pid: int
) -> str | None:
    raw_path = environ.get("AI_RADAR_ORCHESTRATION_LEASE")
    token = environ.get("AI_RADAR_ORCHESTRATION_TOKEN")
    if raw_path is None and token is None:
        return None
    if not raw_path or not token or not _PROVENANCE_TOKEN_RE.fullmatch(token):
        raise ValueError("pipeline orchestration provenance is incomplete")
    provenance_root = (ledger_root.parent / "orchestration").resolve()
    path = Path(raw_path)
    if path.is_symlink() or path.parent.resolve() != provenance_root or path.resolve() != path:
        raise ValueError("pipeline orchestration provenance path is invalid")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("pipeline orchestration provenance ownership is invalid")
        with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
            payload = json.load(stream)
    finally:
        os.close(descriptor)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "token", "owner_pid", "owner_argv", "created_ns"
    }:
        raise ValueError("pipeline orchestration provenance schema is invalid")
    owner_pid = payload.get("owner_pid")
    expected_owner_argv = list(canonical_pipeline_controller_argv())
    try:
        live_owner_argv = shlex.split(_pid_command(owner_pid)) if isinstance(owner_pid, int) else []
    except ValueError:
        live_owner_argv = []
    if (
        payload.get("schema_version") != 1
        or payload.get("token") != token
        or payload.get("owner_argv") != expected_owner_argv
        or not isinstance(payload.get("created_ns"), int)
        or not isinstance(owner_pid, int)
        or owner_pid not in _ancestor_pids(process_pid)
        or not _pid_is_live(owner_pid)
        or live_owner_argv != expected_owner_argv
    ):
        raise ValueError("pipeline orchestration provenance is not live")
    return "pipeline.sh"


def canonical_pipeline_controller_argv() -> tuple[str, str, str]:
    return "/bin/bash", str((_PROJECT_ROOT / "pipeline.sh").resolve()), "--controller"


def _ancestor_pids(pid: int) -> set[int]:
    ancestors: set[int] = set()
    current = pid
    while current > 1:
        result = subprocess.run(
            ["/bin/ps", "-p", str(current), "-o", "ppid="],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            current = int(result.stdout.strip())
        except ValueError:
            break
        if current in ancestors:
            break
        ancestors.add(current)
    return ancestors


def _pid_command(pid: int) -> str:
    return subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
