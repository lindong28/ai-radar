#!/usr/bin/env python3
"""Apply a database snapshot the Mac has pushed, without a serving gap.

Production state is one atomic active release; each slot (8000/8001) serves its
own database file. The candidate slot is prepared and verified while the active
slot keeps serving; only then does nginx swing over.

Isolation/config surface: filesystem sinks and search endpoints come from the
existing ``AI_RADAR_*`` path variables; ports, serve-unit identity, systemctl,
and nginx control come from ``AI_RADAR_PORTS``, ``AI_RADAR_UNIT_PREFIX``,
``AI_RADAR_SYSTEMCTL``, ``AI_RADAR_NGINX_BIN``, and
``AI_RADAR_NGINX_PREFIX``. Search gates use
``AI_RADAR_HTTP_PROBE_TIMEOUT_S`` (default 30 seconds) for both a single-page,
non-verdict warm-up and the subsequent judged requests, with
``AI_RADAR_HTTP_PROBE_INTERVAL_S`` (default 1 second) between verifier
requests. Loopback route proofs connect to
``AI_RADAR_ROUTE_PROOF_SEARCH_URL`` but send the canonical host (and HTTPS SNI)
derived from ``AI_RADAR_PUBLIC_SEARCH_URL``. Rollback keeps the just-replaced
slot alive for ``AI_RADAR_NGINX_ROLLBACK_DRAIN_S`` (default 90 seconds) after
nginx reload so old workers can drain. Command overrides are shell-split once
into argv and all effects then use those typed tuples. With no control
overrides, argv remains exactly the production commands used before this
surface existed. The nginx prefix is passed as ``-p``, so an isolated instance
resolves its own configuration and runtime files beneath that root.
This module consumes
the environment already supplied to its process; production systemd selects
``/etc/ai-radar/server.env``, while an isolated producer trigger must select an
independent unit or wrapper bound to its own environment file. The apply process
does not open or select systemd EnvironmentFile paths itself.

Why Python and not shell: this is a crash-consistent state machine, and four
adversarial review rounds against the shell version kept finding new windows
that were properties of bash itself -- `fn || fail` suppresses errexit inside
the function, EXIT traps fire on paths they were not written for, and every
hash/JSON operation shells out to another process that can fail half-way.
None of those classes exist here, and the whole machine is unit-testable with
a mocked command runner instead of a stubbed server.

State machine (journal, fsynced before the action it precedes):

    committed -> claiming -> rebuilding -> prepared
      -> switching_pending_consumer -> switched_pending_consumer
      -> old_stopping_pending_consumer -> consumer_verified -> committed

Recovery rules, learned finding by finding:
  * claiming   -> complete identity/manifest validation, then consume the one
                  automatic fresh retry.
  * rebuilding -> retry once from the immutable claimed base. A second crash
                  quarantines; deterministic rebuild/oracle failures quarantine
                  immediately.
  * prepared   -> retire any possibly started candidate, restore the old
                  include if needed, then do the one fresh rebuild from the
                  immutable claimed base. Traffic has not moved.
  * switching_pending_consumer / switched_pending_consumer -> always switch
                  back to the old port, re-prove its captured public state, and
                  quarantine. These states can never roll forward.
  * old_stopping_pending_consumer -> restart old, switch back, re-prove its
                  captured public state, and quarantine. Before entering this
                  state the public semantic gate passed; while in it, old is
                  stopped and a loopback nginx probe proves actual route
                  identity without trusting CDN cache behavior.
  * quarantining -> finish the fixed evidence moves and failure record from the
                  durable intent, then enter quarantined. Every rename replays
                  idempotently after a crash.
  * consumer_verified -> finalize forward. This is the only state allowed to
                  advance basis/receipt or retire the old slot.
  * committed  -> nothing to do.
  * unreadable -> stop loudly. Guessing here is how mixtures survive.

Finalize order (public semantic gate -> durable old-stop intent -> stop old and
prove loopback nginx route -> public semantic recheck -> consumer_verified ->
base-only basis/receipt -> committed): the old database and service definition,
plus the old basis/receipt, remain recoverable until both consumer gates pass.
The serving candidate is never a basis.

Manifest v2 keeps two deliberately separate probe expectations: raw
``matches``/``unqualified_matches`` drive the SQLite-level MATCH gate, while
``timeline_http_matches`` is the producer-recorded result set for candidate and public
timeline HTTP gates after application visibility filtering.
"""

from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC_DIR = Path(__file__).resolve().parent
if str(SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(SYNC_DIR))

from build_fts_manifest import (  # noqa: E402
    FTS_FIELDS,
    NORMALIZATION,
    SEARCH_FIELDS,
    ManifestError,
    _trigger_mutates_items_fts,
    sidecar_name,
    validate_manifest,
)

FULL_SHA256_RE = re.compile(r"[0-9a-f]{64}")
VERIFIER_ID_RE = re.compile(r"fts-apply-v[1-9][0-9]*")
# Retry checkpoints intentionally use a semantic version instead of an
# inferred code hash: the verifier closure includes this module, airadar.db,
# the frozen manifest consumer contract, migrations, and runtime FTS/API
# behavior. Bump whenever base verification, candidate rebuild, manifest/row
# equivalence, MATCH probes, candidate HTTP probes, or their direct contract
# inputs change. A missed bump can incorrectly authorize one fresh retry.
VERIFIER_VERSION = "fts-apply-v4"
FTS_SHADOW_TABLES = {
    "items_fts_config",
    "items_fts_content",
    "items_fts_data",
    "items_fts_docsize",
    "items_fts_idx",
}


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, "") or default)


def _env_command(name: str) -> tuple[str, ...] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    command = tuple(shlex.split(raw))
    if not command:
        raise ValueError(f"{name} must name a command")
    return command


def _env_positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _env_nonnegative_float(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return value


def _default_route_proof_url(public_search_url: str) -> str:
    parsed = urlparse(public_search_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return "http://127.0.0.1/api/v1/timeline"
    try:
        port = parsed.port
    except ValueError:
        return "http://127.0.0.1/api/v1/timeline"
    authority = "127.0.0.1"
    if port is not None:
        authority += f":{port}"
    return parsed._replace(netloc=authority).geturl()


def _env_ports() -> tuple[str, str]:
    raw = os.environ.get("AI_RADAR_PORTS", "8000,8001")
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    if len(values) != 2 or values[0] == values[1]:
        raise ValueError("AI_RADAR_PORTS must contain two distinct comma-separated ports")
    if any(not value.isdigit() or not 1 <= int(value) <= 65535 for value in values):
        raise ValueError("AI_RADAR_PORTS values must be decimal ports in 1..65535")
    return values[0], values[1]


@dataclass
class Config:
    home: Path
    data_dir: Path
    incoming: Path
    claimed: Path
    basis_dir: Path
    receipt: Path
    journal: Path
    active_conf: Path
    lock: Path
    quarantine_dir: Path
    public_search_url: str
    route_proof_search_url: str = "http://127.0.0.1/api/v1/timeline"
    # The root-installed symlink nginx actually includes. Checked, not trusted:
    # installer and runtime resolve the real file from the same env overrides,
    # but nothing guarantees they ran with the same environment.
    nginx_link: Path = Path("/etc/nginx/conf.d/ai-radar-active-upstream.conf")
    ports: tuple[str, str] = ("8000", "8001")
    unit_prefix: str = "ai-radar-serve@"
    systemctl_command: tuple[str, ...] | None = None
    nginx_command: tuple[str, ...] | None = None
    nginx_prefix: Path | None = None
    min_free_mem_mb: int = 1536
    probe_terms: tuple[str, ...] = ("OpenAI", "Anthropic", "GPU")
    health_wait_s: int = 120
    http_probe_timeout_s: int = 30
    http_probe_interval_s: float = 1.0
    nginx_rollback_drain_s: float = 90.0

    @classmethod
    def from_env(cls) -> Config:
        home = _env_path("AI_RADAR_HOME", REPO_ROOT)
        data = _env_path("AI_RADAR_DATA_DIR", home / "data")
        public_search_url = (
            os.environ.get("AI_RADAR_PUBLIC_SEARCH_URL", "").rstrip("/")
            or (
                os.environ.get("AI_RADAR_PUBLIC_URL", "").rstrip("/")
                + "/api/v1/timeline"
                if os.environ.get("AI_RADAR_PUBLIC_URL", "").strip()
                else ""
            )
        )
        return cls(
            home=home,
            data_dir=data,
            incoming=_env_path("AI_RADAR_INCOMING", data / "radar.db.incoming"),
            claimed=_env_path("AI_RADAR_CLAIMED", data / "radar.db.claimed"),
            basis_dir=_env_path("AI_RADAR_BASIS_DIR", data / "basis"),
            receipt=_env_path("AI_RADAR_SNAPSHOT_RECEIPT", data / "accepted-snapshot.json"),
            journal=_env_path("AI_RADAR_SWITCH_JOURNAL", data / "switch-journal.json"),
            # The REAL file lives where the app user can atomically write it;
            # /etc/nginx/conf.d holds a root-installed symlink pointing here
            # (nginx follows symlinks on include). The rewrite's first draft
            # wrote /etc directly and, running as the app user, would have
            # hit PermissionError on every switch -- after journalling
            # `switching`, wedging the machine at the same point forever.
            active_conf=_env_path(
                "AI_RADAR_ACTIVE_UPSTREAM_CONF",
                data / "nginx" / "ai-radar-active-upstream.conf",
            ),
            lock=_env_path("AI_RADAR_DEPLOY_LOCK", data / ".deploy.lock"),
            quarantine_dir=_env_path("AI_RADAR_QUARANTINE_DIR", data / "quarantine"),
            public_search_url=public_search_url,
            route_proof_search_url=os.environ.get(
                "AI_RADAR_ROUTE_PROOF_SEARCH_URL",
                _default_route_proof_url(public_search_url),
            ).rstrip("/"),
            nginx_link=_env_path(
                "AI_RADAR_NGINX_LINK",
                Path("/etc/nginx/conf.d/ai-radar-active-upstream.conf"),
            ),
            ports=_env_ports(),
            unit_prefix=os.environ.get(
                "AI_RADAR_UNIT_PREFIX", "ai-radar-serve@"
            ),
            systemctl_command=_env_command("AI_RADAR_SYSTEMCTL"),
            nginx_command=_env_command("AI_RADAR_NGINX_BIN"),
            nginx_prefix=(
                _env_path("AI_RADAR_NGINX_PREFIX", Path("."))
                if os.environ.get("AI_RADAR_NGINX_PREFIX", "").strip()
                else None
            ),
            min_free_mem_mb=int(os.environ.get("AI_RADAR_MIN_FREE_MEM_MB", "1536")),
            probe_terms=tuple(
                os.environ.get("AI_RADAR_PROBE_TERMS", "OpenAI Anthropic GPU").split()
            ),
            health_wait_s=int(os.environ.get("AI_RADAR_HEALTH_WAIT_S", "120")),
            http_probe_timeout_s=_env_positive_int(
                "AI_RADAR_HTTP_PROBE_TIMEOUT_S", 30
            ),
            http_probe_interval_s=_env_nonnegative_float(
                "AI_RADAR_HTTP_PROBE_INTERVAL_S", 1.0
            ),
            nginx_rollback_drain_s=_env_nonnegative_float(
                "AI_RADAR_NGINX_ROLLBACK_DRAIN_S", 90.0
            ),
        )

    def slot_db(self, port: str) -> Path:
        return self.data_dir / f"radar-{port}.db"

    def serve_unit(self, port: str) -> str:
        return f"{self.unit_prefix}{port}.service"

    def systemctl_args(
        self, *args: str, mutate: bool = False
    ) -> tuple[str, ...]:
        command = self.systemctl_command
        if command is None:
            command = ("sudo", "systemctl") if mutate else ("systemctl",)
        return (*command, *args)

    def nginx_args(self, *args: str) -> tuple[str, ...]:
        command = self.nginx_command or ("sudo", "nginx")
        prefix: tuple[str, ...] = ()
        if self.nginx_prefix is not None:
            prefix = ("-p", f"{self.nginx_prefix}/")
        return (*command, *prefix, *args)

    def other_port(self, port: str) -> str:
        return self.ports[1] if port == self.ports[0] else self.ports[0]

    @property
    def basis(self) -> Path:
        # Directory + fixed basename: rsync --copy-dest matches by basename
        # inside a directory, so the basis keeps the upload's name.
        return self.basis_dir / "radar.db.upload"


class ApplyError(RuntimeError):
    pass


class HttpProbeInfrastructureError(ApplyError):
    pass


class FinalizeAuthorityError(ApplyError):
    def __init__(self, message: str, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def log(msg: str) -> None:
    print(f"[apply] {msg}", flush=True)


class Runner:
    """All external effects go through here so tests can intercept them."""

    def run(self, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(argv, check=check, capture_output=True, text=True)

    def ok(self, *argv: str) -> bool:
        return self.run(*argv, check=False).returncode == 0


def fsync_path(path: Path) -> None:
    """fsync a file and its parent directory.

    rename is atomic against process crashes, but power loss reorders
    unfsynced writes: without this, disk could hold a `switched` journal whose
    include rename never made it. Recovery tolerates a LAGGING journal
    (switching re-runs the switch); it cannot tolerate a LEADING one.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def snapshot_id_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_text(value: Any) -> str:
    return "" if value is None else str(value)


def _frame(value: str) -> bytes:
    payload = value.encode("utf-8")
    return len(payload).to_bytes(8, "big") + payload


def _fts_table_digest(rows: list[tuple[str, ...]]) -> str:
    row_hashes: list[bytes] = []
    for row in rows:
        row_digest = hashlib.sha256(b"ai-radar-items-fts-row-v1\0")
        for value in row:
            row_digest.update(_frame(value))
        row_hashes.append(row_digest.digest())
    digest = hashlib.sha256(b"ai-radar-items-fts-table-v1\0")
    digest.update(len(rows).to_bytes(8, "big"))
    for row_hash in sorted(row_hashes):
        digest.update(row_hash)
    return digest.hexdigest()


class Deploy:
    def __init__(self, cfg: Config, runner: Runner | None = None) -> None:
        self.cfg = cfg
        self.r = runner or Runner()

    # ---------------- journal ----------------

    def journal_write(
        self,
        state: str,
        port: str,
        snapshot_id: str | None,
        **details: object,
    ) -> None:
        tmp = self.cfg.journal.with_suffix(".tmp")
        normalized_details = dict(details)
        retry_count = normalized_details.pop("retry_count", None)
        retry_authority_states = {
            "claiming",
            "rebuilding",
            "prepared",
            "retry_blocked_verifier_changed",
        }
        verifier_identity = normalized_details.pop("verifier_identity", None)
        if state in retry_authority_states and verifier_identity is None:
            verifier_identity = VERIFIER_VERSION
        payload: dict[str, object] = {
            "journal_schema_version": 2,
            "state": state,
            "state_recorded_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }
        if state in {"committed", "legacy_committed_unverified"}:
            payload["serving_port"] = port
        elif state not in {"idle", "quarantined"}:
            payload["candidate_port"] = port
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        if retry_count is not None:
            payload["automatic_fresh_rebuild_retries_used"] = retry_count
            payload["automatic_fresh_rebuild_retry_limit"] = 1
        if verifier_identity is not None:
            payload["verifier_identity"] = verifier_identity
        recovery_actions = {
            "switching_pending_consumer": "rollback-to-previous-serving",
            "switched_pending_consumer": "rollback-to-previous-serving",
            "old_stopping_pending_consumer": "rollback-to-previous-serving",
            "rollback_failed": "rollback-to-previous-serving",
            "rollback_blocked_invalid_oracle": "manual-intervention",
            "consumer_verified": "finalize-forward",
            "finalize_blocked_invalid_authority": "manual-intervention",
            "retry_blocked_verifier_changed": "manual-intervention",
            "quarantining": "complete-quarantine",
            "legacy_committed_unverified": "accept-new-full-identity-release",
        }
        if state in recovery_actions:
            payload["recovery_action"] = recovery_actions[state]
        payload.update(normalized_details)
        self._validate_journal_payload(payload)
        self._validate_journal_semantics(payload)
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        fsync_path(tmp)
        os.replace(tmp, self.cfg.journal)
        fsync_path(self.cfg.journal)

    def journal_read(self) -> dict | None:
        if not self.cfg.journal.exists():
            return None
        try:
            data = json.loads(self.cfg.journal.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ApplyError(
                f"journal {self.cfg.journal} exists but cannot be parsed; "
                f"refusing to guess the release state ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise ApplyError(f"journal {self.cfg.journal} is not a JSON object")
        if "state" not in data:
            raise ApplyError(f"journal {self.cfg.journal} has no state field")
        if data.get("journal_schema_version") == 2:
            self._validate_journal_payload(data)
        return data

    def _validate_journal_semantics(self, payload: Mapping[str, Any]) -> None:
        state = payload.get("state")
        rollback_states = {
            "switching_pending_consumer",
            "switched_pending_consumer",
            "old_stopping_pending_consumer",
            "rollback_failed",
        }
        if state in rollback_states:
            candidate = str(payload["candidate_port"])
            snapshot_id = str(payload["snapshot_id"])
            manifest_sha256 = str(payload["manifest_sha256"])
            bound = self._validate_rollback_oracle(
                payload.get("rollback"), candidate, snapshot_id, manifest_sha256
            )
            self._validate_pending_rollback_inputs(
                bound, snapshot_id, manifest_sha256
            )
        elif state == "consumer_verified":
            self._validate_consumer_verified_authority(
                str(payload["candidate_port"]),
                str(payload["snapshot_id"]),
                str(payload["manifest_sha256"]),
            )

    def _validate_journal_payload(self, payload: Mapping[str, Any]) -> None:
        state = payload.get("state")
        allowed_states = {
            "idle",
            "claiming",
            "rebuilding",
            "prepared",
            "retry_blocked_verifier_changed",
            "switching_pending_consumer",
            "switched_pending_consumer",
            "old_stopping_pending_consumer",
            "consumer_verified",
            "finalize_blocked_invalid_authority",
            "rollback_failed",
            "rollback_blocked_invalid_oracle",
            "quarantining",
            "quarantined",
            "committed",
            "legacy_committed_unverified",
        }
        if payload.get("journal_schema_version") != 2 or state not in allowed_states:
            raise ApplyError("journal payload has an unsupported state/schema version")
        if not isinstance(payload.get("state_recorded_at"), str):
            raise ApplyError("journal payload has no state_recorded_at")
        if state == "idle":
            for key in ("candidate_port", "serving_port", "snapshot_id"):
                if key in payload:
                    raise ApplyError(f"idle journal must not contain {key}")
            return
        if state == "legacy_committed_unverified":
            legacy_snapshot_id = payload.get("legacy_snapshot_id")
            legacy_snapshot_id_status = payload.get("legacy_snapshot_id_status")
            self._validate_file_binding_shape(
                payload.get("legacy_basis"), "legacy basis"
            )
            self._validate_file_binding_shape(
                payload.get("legacy_receipt"), "legacy receipt"
            )
            legacy_identity_is_explicit = (
                legacy_snapshot_id_status == "truncated-16-hex"
                and isinstance(legacy_snapshot_id, str)
                and re.fullmatch(r"[0-9a-f]{16}", legacy_snapshot_id) is not None
            ) or (
                legacy_snapshot_id_status == "unavailable-before-hash"
                and legacy_snapshot_id is None
            )
            if (
                payload.get("serving_port") not in self.cfg.ports
                or not legacy_identity_is_explicit
                or payload.get("identity_status") != "unavailable-legacy"
                or payload.get("recovery_action")
                != "accept-new-full-identity-release"
            ):
                raise ApplyError("legacy committed journal marker is malformed")
            return
        if state == "quarantined":
            required = ("failure_id", "failure_path", "failure_sha256")
            if any(not isinstance(payload.get(key), str) for key in required):
                raise ApplyError("quarantined journal has incomplete failure binding")
            for key in (
                "candidate_port",
                "serving_port",
                "snapshot_id",
                "manifest_sha256",
                "automatic_fresh_rebuild_retries_used",
            ):
                if key in payload:
                    raise ApplyError(f"quarantined journal must not contain {key}")
            return
        if state == "committed":
            if payload.get("serving_port") not in self.cfg.ports:
                raise ApplyError("committed journal has an invalid serving_port")
            for key in ("snapshot_id", "manifest_sha256"):
                value = payload.get(key)
                if not isinstance(value, str) or not FULL_SHA256_RE.fullmatch(value):
                    raise ApplyError(f"committed journal has invalid {key}")
            if "candidate_port" in payload:
                raise ApplyError("committed journal must not contain candidate_port")
            return
        if payload.get("candidate_port") not in self.cfg.ports:
            raise ApplyError(f"{state} journal has an invalid candidate_port")
        if state == "claiming":
            if "snapshot_id" in payload:
                raise ApplyError("claiming journal must not contain snapshot_id")
            if not VERIFIER_ID_RE.fullmatch(str(payload.get("verifier_identity", ""))):
                raise ApplyError("claiming journal has invalid verifier identity")
            return
        snapshot_id = payload.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not FULL_SHA256_RE.fullmatch(snapshot_id):
            raise ApplyError(f"{state} journal has invalid snapshot_id")
        retries = payload.get("automatic_fresh_rebuild_retries_used")
        if type(retries) is not int or retries not in (0, 1):
            raise ApplyError(f"{state} journal has invalid automatic retry usage")
        if payload.get("automatic_fresh_rebuild_retry_limit") != 1:
            raise ApplyError(f"{state} journal has invalid automatic retry limit")
        if state in {"rebuilding", "prepared", "retry_blocked_verifier_changed"}:
            if not VERIFIER_ID_RE.fullmatch(str(payload.get("verifier_identity", ""))):
                raise ApplyError(f"{state} journal has invalid verifier identity")
        manifest_sha256 = payload.get("manifest_sha256")
        if state != "quarantining" or manifest_sha256 is not None:
            if (
                not isinstance(manifest_sha256, str)
                or not FULL_SHA256_RE.fullmatch(manifest_sha256)
            ):
                raise ApplyError(f"{state} journal has invalid manifest_sha256")
        pending_rollback = {
            "switching_pending_consumer",
            "switched_pending_consumer",
            "old_stopping_pending_consumer",
            "rollback_failed",
        }
        if state in pending_rollback and not isinstance(payload.get("rollback"), dict):
            raise ApplyError(f"{state} journal has no rollback oracle")
        if state == "quarantining" and not isinstance(payload.get("quarantine"), dict):
            raise ApplyError("quarantining journal has no quarantine intent")
        recovery_actions = {
            "switching_pending_consumer": "rollback-to-previous-serving",
            "switched_pending_consumer": "rollback-to-previous-serving",
            "old_stopping_pending_consumer": "rollback-to-previous-serving",
            "rollback_failed": "rollback-to-previous-serving",
            "rollback_blocked_invalid_oracle": "manual-intervention",
            "consumer_verified": "finalize-forward",
            "finalize_blocked_invalid_authority": "manual-intervention",
            "retry_blocked_verifier_changed": "manual-intervention",
            "quarantining": "complete-quarantine",
            "legacy_committed_unverified": "accept-new-full-identity-release",
        }
        if state in recovery_actions and payload.get("recovery_action") != recovery_actions[state]:
            raise ApplyError(f"{state} journal has an invalid recovery_action")
        last_failure_keys = {
            "last_failure_category",
            "last_failure_message",
            "last_failure_at",
        }
        present_failure_keys = last_failure_keys.intersection(payload)
        if present_failure_keys and (
            present_failure_keys != last_failure_keys
            or any(
                not isinstance(payload.get(key), str) or not payload[key]
                for key in last_failure_keys
            )
        ):
            raise ApplyError(f"{state} journal has an incomplete last failure record")
        failure_states = {
            "rollback_failed",
            "rollback_blocked_invalid_oracle",
            "finalize_blocked_invalid_authority",
            "retry_blocked_verifier_changed",
        }
        if state in failure_states and present_failure_keys != last_failure_keys:
            raise ApplyError(f"{state} journal has no last failure record")
        if state == "rollback_blocked_invalid_oracle" and "rollback_evidence" not in payload:
            raise ApplyError("rollback blocked journal has no oracle evidence")
        if state == "retry_blocked_verifier_changed":
            observed = payload.get("observed_verifier_identity")
            if (
                not isinstance(observed, str)
                or VERIFIER_ID_RE.fullmatch(observed) is None
                or observed == payload["verifier_identity"]
            ):
                raise ApplyError("retry blocked journal has invalid verifier evidence")
        if state == "finalize_blocked_invalid_authority":
            self._validate_finalize_authority_evidence(
                payload.get("authority_evidence"),
                str(payload["candidate_port"]),
                str(payload["snapshot_id"]),
                str(payload["manifest_sha256"]),
                require_mismatch=True,
            )

    def _atomic_json_write(self, path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        fsync_path(temporary)
        os.replace(temporary, path)
        fsync_path(path)

    def _file_binding(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {"present": False, "sha256": None}
        return {"present": True, "sha256": snapshot_id_of(path)}

    @staticmethod
    def _validate_file_binding_shape(binding: object, label: str) -> dict[str, Any]:
        if not isinstance(binding, dict) or set(binding) != {"present", "sha256"}:
            raise ApplyError(f"{label} binding is malformed")
        present = binding.get("present")
        digest = binding.get("sha256")
        if present is False and digest is None:
            return binding
        if (
            present is not True
            or not isinstance(digest, str)
            or FULL_SHA256_RE.fullmatch(digest) is None
        ):
            raise ApplyError(f"{label} binding has an invalid presence/hash pair")
        return binding

    def _assert_file_binding(self, path: Path, binding: object, label: str) -> None:
        checked = self._validate_file_binding_shape(binding, f"rollback {label}")
        expected_present = checked["present"]
        expected_sha = checked["sha256"]
        if expected_present is False and expected_sha is None:
            if path.exists():
                raise ApplyError(f"rollback {label} appeared after the pre-switch capture")
            return
        if not path.is_file() or snapshot_id_of(path) != expected_sha:
            raise ApplyError(f"rollback {label} no longer matches its full SHA-256 binding")

    # ---------------- environment facts ----------------

    def active_port(self) -> str | None:
        try:
            text = self.cfg.active_conf.read_text()
        except OSError:
            return None
        for token in text.replace(";", " ").split():
            if token.startswith("127.0.0.1:"):
                return token.split(":", 1)[1]
        return None

    def free_mem_mb(self) -> int:
        # MemAvailable, not disk space: an earlier revision ran df here and
        # approved a two-slot start on an OOM-bound host.
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) // 1024
        raise ApplyError("MemAvailable not found in /proc/meminfo")

    def slot_serving(self, port: str) -> bool:
        return self.r.ok(
            *self.cfg.systemctl_args(
                "is-active", "--quiet", self.cfg.serve_unit(port)
            )
        ) and self.r.ok(
            "curl", "-sf", "-m", "5", f"http://127.0.0.1:{port}/api/v1/healthz"
        )

    # ---------------- verification ----------------

    def _airadar_db(self):  # noqa: ANN202
        sys.path.insert(0, str(self.cfg.home / "src"))
        try:
            from airadar import db as airadar_db
        except ImportError as exc:
            raise ApplyError(f"cannot import airadar for FTS rebuild: {exc}") from exc
        return airadar_db

    def verify_base_snapshot(self, db: Path) -> None:
        """Verify the claimed transfer artifact is healthy and base-only."""
        try:
            conn = sqlite3.connect(db)
        except sqlite3.Error as exc:
            raise ApplyError(f"cannot open snapshot: {exc}") from exc
        # close() can itself raise on I/O failure; it lives inside the same
        # wrapper so even that exit path leaves the claiming state cleanly.
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ApplyError("integrity_check failed")
            for table in (
                "items",
                "sources",
                "curated_items",
                "curation_runs",
                "item_evaluations",
            ):
                if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] <= 0:
                    raise ApplyError(f"snapshot has no rows in {table}")
            fts_objects: list[tuple[str, str]] = []
            for object_type, name, sql in conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ):
                if object_type == "table" and (
                    name == "items_fts" or name in FTS_SHADOW_TABLES
                ):
                    fts_objects.append((object_type, name))
                elif object_type == "trigger" and _trigger_mutates_items_fts(sql):
                    fts_objects.append((object_type, name))
            if fts_objects:
                raise ApplyError(
                    f"snapshot is not base-only; contains FTS-owned objects {fts_objects!r}"
                )
            if not conn.execute(
                "SELECT 1 FROM airadar_migrations WHERE id='004_enrich_stage'"
            ).fetchone():
                raise ApplyError("snapshot predates migration 004; this code cannot serve it")
        except (sqlite3.Error, OSError) as exc:
            # Severe corruption surfaces as raw sqlite/OS errors from any of
            # the probes above. They are expected verification outcomes, not
            # programming errors, and must take the same "leave claiming,
            # keep the file for inspection" path as a clean rejection --
            # otherwise the journal stays `claiming` and the next reconcile
            # loops the known-bad snapshot back to incoming forever.
            raise ApplyError(f"snapshot verification errored: {exc}") from exc
        finally:
            try:
                conn.close()
            except (sqlite3.Error, OSError) as exc:  # pragma: no cover - disk I/O edge
                raise ApplyError(f"snapshot connection close failed: {exc}") from exc

    def _manifest_path(self, snapshot_id: str) -> Path:
        try:
            filename = sidecar_name(snapshot_id)
        except ManifestError as exc:
            raise ApplyError(f"manifest identity is invalid: {exc}") from exc
        return self.cfg.data_dir / filename

    @staticmethod
    def _validate_result_set(value: object, label: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ApplyError(f"manifest {label} is not an object")
        count = value.get("count")
        item_ids = value.get("item_ids")
        if type(count) is not int or count < 0:
            raise ApplyError(f"manifest {label}.count is invalid")
        if not isinstance(item_ids, list) or any(not isinstance(item, str) for item in item_ids):
            raise ApplyError(f"manifest {label}.item_ids is invalid")
        if item_ids != sorted(set(item_ids)) or count != len(item_ids):
            raise ApplyError(f"manifest {label} count/IDs are inconsistent")
        return {"count": count, "item_ids": item_ids}

    def _load_manifest(self, snapshot_id: str) -> dict[str, Any]:
        path = self._manifest_path(snapshot_id)
        if not path.is_file():
            raise ApplyError(f"manifest sidecar missing for snapshot {snapshot_id}: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ApplyError(f"manifest sidecar cannot be parsed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApplyError("manifest root is not an object")
        try:
            validate_manifest(payload)
        except ManifestError as exc:
            raise ApplyError(f"manifest validation failed: {exc}") from exc
        if payload.get("snapshot_id") != snapshot_id:
            raise ApplyError("manifest snapshot_id does not match claimed artifact")
        fts = payload.get("fts")
        if not isinstance(fts, dict):
            raise ApplyError("manifest FTS oracle is missing")
        if fts.get("table") != "items_fts" or fts.get("fields") != list(FTS_FIELDS):
            raise ApplyError("manifest FTS table/field contract mismatch")
        if fts.get("normalization") != NORMALIZATION:
            raise ApplyError("manifest normalization contract mismatch")
        row_count = fts.get("row_count")
        digest = fts.get("sha256")
        if not isinstance(row_count, int) or row_count <= 0:
            raise ApplyError("manifest FTS row_count is invalid")
        if not isinstance(digest, str) or FULL_SHA256_RE.fullmatch(digest) is None:
            raise ApplyError("manifest FTS digest is not a full lowercase SHA-256")
        probes = payload.get("probes")
        if not isinstance(probes, dict):
            raise ApplyError("manifest probes are missing")
        for field in SEARCH_FIELDS:
            probe = probes.get(field)
            if not isinstance(probe, dict) or probe.get("field") != field:
                raise ApplyError(f"manifest probe {field} is malformed")
            for key in ("term", "query", "unqualified_query"):
                if not isinstance(probe.get(key), str) or not probe[key]:
                    raise ApplyError(f"manifest probe {field}.{key} is invalid")
            self._validate_result_set(probe.get("matches"), f"probe {field}.matches")
            self._validate_result_set(
                probe.get("unqualified_matches"),
                f"probe {field}.unqualified_matches",
            )
            timeline_http_matches = self._validate_result_set(
                probe.get("timeline_http_matches"),
                f"probe {field}.timeline_http_matches",
            )
            if timeline_http_matches["count"] == 0:
                raise ApplyError(
                    f"manifest probe {field}.timeline_http_matches is empty"
                )
        return payload

    def _verify_candidate_fts(self, db: Path, manifest: Mapping[str, Any]) -> None:
        conn = sqlite3.connect(db)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ApplyError("candidate integrity_check failed")
            try:
                conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
            except sqlite3.DatabaseError as exc:
                raise ApplyError(f"candidate fts5 integrity-check failed: {exc}") from exc
            if not self._airadar_db()._fts_schema_matches(conn):
                raise ApplyError("candidate FTS schema/triggers do not match migration 003")
            columns = [
                row[1]
                for row in conn.execute("PRAGMA table_xinfo('items_fts')")
                if row[6] == 0
            ]
            if columns != list(FTS_FIELDS):
                raise ApplyError(
                    f"candidate FTS fields differ: expected={list(FTS_FIELDS)!r} "
                    f"actual={columns!r}"
                )
            selected = ", ".join(f'"{field}"' for field in FTS_FIELDS)
            rows = [
                tuple(_raw_text(value) for value in row)
                for row in conn.execute(f"SELECT {selected} FROM items_fts")
            ]
            rows.sort()
            fts = manifest["fts"]
            if len(rows) != fts["row_count"]:
                raise ApplyError(
                    f"candidate FTS row count differs: expected={fts['row_count']} "
                    f"actual={len(rows)}"
                )
            actual_digest = _fts_table_digest(rows)
            if actual_digest != fts["sha256"]:
                raise ApplyError(
                    f"candidate FTS digest differs: expected={fts['sha256']} "
                    f"actual={actual_digest}"
                )
            probes = manifest["probes"]
            for field in SEARCH_FIELDS:
                probe = probes[field]
                for query_key, expected_key in (
                    ("query", "matches"),
                    ("unqualified_query", "unqualified_matches"),
                ):
                    raw_rows = conn.execute(
                        "SELECT item_id FROM items_fts WHERE items_fts MATCH ?",
                        (probe[query_key],),
                    ).fetchall()
                    actual_ids = sorted({_raw_text(row[0]) for row in raw_rows})
                    actual = {"count": len(actual_ids), "item_ids": actual_ids}
                    if actual != probe[expected_key]:
                        raise ApplyError(
                            f"candidate FTS probe {field}.{query_key} differs: "
                            f"expected={probe[expected_key]!r} actual={actual!r}"
                        )
        except sqlite3.Error as exc:
            raise ApplyError(f"candidate FTS verification errored: {exc}") from exc
        finally:
            conn.close()

    def _materialize_and_verify_candidate(
        self,
        candidate: str,
        snapshot_id: str,
        manifest: Mapping[str, Any],
    ) -> None:
        if not self.cfg.claimed.is_file() or snapshot_id_of(self.cfg.claimed) != snapshot_id:
            raise ApplyError("immutable claimed base no longer matches its snapshot identity")
        candidate_db = self.cfg.slot_db(candidate)
        temporary = candidate_db.with_suffix(".materializing")
        shutil.copyfile(self.cfg.claimed, temporary)
        fsync_path(temporary)
        os.replace(temporary, candidate_db)
        fsync_path(candidate_db)
        for suffix in ("-wal", "-shm"):
            side = Path(str(candidate_db) + suffix)
            if side.exists():
                side.unlink()
        self._airadar_db().rebuild_fts(candidate_db)
        with sqlite3.connect(candidate_db) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        for suffix in ("-wal", "-shm"):
            side = Path(str(candidate_db) + suffix)
            if side.exists():
                side.unlink()
        fsync_path(candidate_db)
        self._verify_candidate_fts(candidate_db, manifest)
        if snapshot_id_of(self.cfg.claimed) != snapshot_id:
            raise ApplyError("FTS rebuild mutated the immutable claimed base")

    def _http_search_url(self, api_url: str, term: str, page: int) -> str:
        if not api_url:
            raise ApplyError("public search URL is not configured")
        separator = "&" if "?" in api_url else "?"
        return api_url + separator + urlencode(
            {"q": term, "page": page, "limit": 100}
        )

    def _route_proof_curl_target(self, term: str, page: int) -> tuple[str, ...]:
        route = urlparse(self.cfg.route_proof_search_url)
        public = urlparse(self.cfg.public_search_url)
        if (
            public.scheme not in {"http", "https"}
            or public.hostname is None
            or public.username is not None
            or public.password is not None
        ):
            raise ApplyError(
                "AI_RADAR_PUBLIC_SEARCH_URL must provide the canonical HTTP(S) "
                "host for loopback route proof"
            )
        try:
            public_port = public.port or (443 if public.scheme == "https" else 80)
            route_port = route.port or (443 if route.scheme == "https" else 80)
        except ValueError as exc:
            raise ApplyError("route-proof URL has an invalid port") from exc
        if route.scheme != public.scheme:
            raise ApplyError(
                "AI_RADAR_ROUTE_PROOF_SEARCH_URL scheme must match the canonical "
                "public search URL"
            )
        if route.path != public.path or route.query != public.query:
            raise ApplyError(
                "AI_RADAR_ROUTE_PROOF_SEARCH_URL path/query must match the canonical "
                "public search URL"
            )
        route_address = route.hostname
        if route_address == "localhost":
            route_address = "127.0.0.1"
        if route_address is None:
            raise ApplyError("route-proof URL has no loopback address")
        if ":" in route_address:
            route_address = f"[{route_address}]"
        public_connect_host = public.hostname
        if ":" in public_connect_host:
            public_connect_host = f"[{public_connect_host}]"
        canonical_url = self._http_search_url(self.cfg.public_search_url, term, page)
        connect_to = (
            f"{public_connect_host}:{public_port}:{route_address}:{route_port}"
        )
        return "--noproxy", "*", "--connect-to", connect_to, canonical_url

    def _curl_search_args(self, api_url: str, term: str, page: int) -> tuple[str, ...]:
        request_url = self._http_search_url(api_url, term, page)
        if api_url == self.cfg.route_proof_search_url:
            self._validated_route_proof_url()
            return self._route_proof_curl_target(term, page)
        return (request_url,)

    def _pace_http_probe(self) -> None:
        if self.cfg.http_probe_interval_s > 0:
            time.sleep(self.cfg.http_probe_interval_s)

    def _warm_http_search(self, api_url: str, term: str) -> None:
        """Exercise the real page-one search path without contributing a verdict."""
        self.r.run(
            "curl",
            "-sS",
            "-f",
            "--max-time",
            str(self.cfg.http_probe_timeout_s),
            *self._curl_search_args(api_url, term, 1),
            check=False,
        )
        self._pace_http_probe()

    def _http_search_results(self, api_url: str, term: str) -> dict[str, object]:
        self._warm_http_search(api_url, term)
        page = 1
        total: int | None = None
        item_ids: list[str] = []
        while True:
            result = self.r.run(
                "curl",
                "-sS",
                "-f",
                "--max-time",
                str(self.cfg.http_probe_timeout_s),
                *self._curl_search_args(api_url, term, page),
                check=False,
            )
            self._pace_http_probe()
            if result.returncode != 0:
                raise HttpProbeInfrastructureError(
                    f"search HTTP probe failed rc={result.returncode} url={api_url}"
                )
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ApplyError(f"search HTTP probe returned invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ApplyError("search HTTP probe returned a non-object envelope")
            data = payload.get("data")
            if payload.get("success") is not True or not isinstance(data, dict):
                raise ApplyError("search HTTP probe returned a non-success envelope")
            current_total = data.get("total")
            items = data.get("items")
            if type(current_total) is not int or current_total < 0:
                raise ApplyError("search HTTP probe has an invalid total")
            if not isinstance(items, list):
                raise ApplyError("search HTTP probe has no items list")
            if total is None:
                total = current_total
            elif current_total != total:
                raise ApplyError("search HTTP total changed during pagination")
            for item in items:
                item_id = item.get("id") if isinstance(item, dict) else None
                if not isinstance(item_id, str):
                    raise ApplyError("search HTTP result has no string id")
                item_ids.append(item_id)
            if len(item_ids) >= total:
                break
            if not items:
                raise ApplyError("search HTTP pagination ended before total rows were returned")
            page += 1
        unique_ids = sorted(set(item_ids))
        if len(unique_ids) != total or len(item_ids) != total:
            raise ApplyError(
                f"search HTTP count/IDs differ: total={total} rows={len(item_ids)} "
                f"unique={len(unique_ids)}"
            )
        return {"count": total, "item_ids": unique_ids}

    def _verify_http_against_manifest(
        self,
        api_url: str,
        manifest: Mapping[str, Any],
        *,
        vantage: str,
    ) -> None:
        probes = manifest["probes"]
        for field in SEARCH_FIELDS:
            probe = probes[field]
            actual = self._http_search_results(api_url, probe["term"])
            if actual != probe["timeline_http_matches"]:
                raise ApplyError(
                    f"{vantage} consumer probe {field} differs: "
                    f"expected={probe['timeline_http_matches']!r} actual={actual!r}"
                )

    def _capture_public_results(
        self, manifest: Mapping[str, Any]
    ) -> dict[str, dict[str, object]]:
        if not self.cfg.public_search_url:
            raise ApplyError(
                "AI_RADAR_PUBLIC_SEARCH_URL or AI_RADAR_PUBLIC_URL is required "
                "for the post-switch consumer gate"
            )
        probes = manifest["probes"]
        return {
            field: {
                "term": probes[field]["term"],
                "result": self._http_search_results(
                    self.cfg.public_search_url,
                    probes[field]["term"],
                ),
            }
            for field in SEARCH_FIELDS
        }

    def _verify_public_against_manifest(self, manifest: Mapping[str, Any]) -> None:
        self._verify_http_against_manifest(
            self.cfg.public_search_url,
            manifest,
            vantage="post-switch public",
        )

    def _validated_route_proof_url(self) -> str:
        parsed = urlparse(self.cfg.route_proof_search_url)
        try:
            parsed.port
            loopback = parsed.hostname == "localhost" or (
                parsed.hostname is not None
                and ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if (
            parsed.scheme not in {"http", "https"}
            or not loopback
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ApplyError(
                "AI_RADAR_ROUTE_PROOF_SEARCH_URL must be an HTTP(S) loopback "
                "nginx ingress URL"
            )
        return self.cfg.route_proof_search_url

    def _verify_route_proof_against_manifest(
        self, manifest: Mapping[str, Any]
    ) -> None:
        self._verify_http_against_manifest(
            self._validated_route_proof_url(),
            manifest,
            vantage="cache-bypass nginx route",
        )

    def _verify_public_baseline(self, rollback: Mapping[str, Any]) -> None:
        self._verify_baseline_at_url(
            rollback,
            self.cfg.public_search_url,
            vantage="rollback public",
        )

    def _verify_route_proof_baseline(self, rollback: Mapping[str, Any]) -> None:
        self._verify_baseline_at_url(
            rollback,
            self._validated_route_proof_url(),
            vantage="loopback nginx baseline",
        )

    def _verify_baseline_at_url(
        self,
        rollback: Mapping[str, Any],
        api_url: str,
        *,
        vantage: str,
    ) -> None:
        results = rollback.get("previous_serving_public_results")
        if not isinstance(results, dict) or set(results) != set(SEARCH_FIELDS):
            raise ApplyError("rollback oracle public result set is malformed")
        for field in SEARCH_FIELDS:
            record = results[field]
            if not isinstance(record, dict) or not isinstance(record.get("term"), str):
                raise ApplyError(f"rollback oracle public probe {field} is malformed")
            expected = self._validate_result_set(
                record.get("result"), f"rollback public probe {field}"
            )
            actual = self._http_search_results(
                api_url,
                record["term"],
            )
            if actual != expected:
                raise ApplyError(
                    f"{vantage} probe {field} differs: "
                    f"expected={expected!r} actual={actual!r}"
                )

    # ---------------- switch primitives ----------------

    def write_active_include(self, port: str) -> None:
        content = f"upstream ai_radar_active {{ server 127.0.0.1:{port}; }}\n"
        self.cfg.active_conf.parent.mkdir(parents=True, exist_ok=True)
        # Same-directory temp + rename: rename is only atomic within one
        # filesystem, and /tmp is usually a different one.
        tmp = self.cfg.active_conf.with_suffix(".tmp")
        tmp.write_text(content)
        fsync_path(tmp)
        os.replace(tmp, self.cfg.active_conf)
        # Fail-closed durability, same reasoning as the journal: if this sync
        # silently failed, a later power loss could persist `committed` while
        # the include rename never reached disk -- and committed reconcile
        # (correctly) touches nothing, freezing the mixture.
        fsync_path(self.cfg.active_conf)

    def assert_nginx_link_matches(self) -> None:
        """The file we write must be the file nginx reads.

        Config comes from env overrides (AI_RADAR_DATA_DIR, _HOME, explicit
        _ACTIVE_UPSTREAM_CONF), and the installer resolved the same values at
        install time -- possibly with a different environment. If they
        diverged, apply would flip file A while nginx keeps including file B:
        every switch "succeeds", traffic never moves, and finalize then stops
        the slot that is actually serving. Checked before any switch rather
        than trusted; a missing link means install-server.sh never ran.
        """
        link = self.cfg.nginx_link
        if not link.is_symlink():
            raise ApplyError(
                f"{link} is not a symlink to the active include; run install-server.sh"
            )
        target = Path(os.readlink(link))
        if target != self.cfg.active_conf:
            raise ApplyError(
                f"nginx includes {target} but this run writes {self.cfg.active_conf}; "
                "installer and runtime disagree on the include path -- fix the "
                "environment or re-run install-server.sh before any switch"
            )

    def _switch_include(self, port: str) -> None:
        self.assert_nginx_link_matches()
        self.write_active_include(port)
        if not self.r.ok(*self.cfg.nginx_args("-t")):
            raise ApplyError("nginx rejected the generated active upstream")
        if not self.r.ok(*self.cfg.nginx_args("-s", "reload")):
            raise ApplyError("nginx reload failed while switching the active upstream")

    def _capture_rollback_oracle(
        self,
        old: str,
        snapshot_id: str,
        manifest: Mapping[str, Any],
    ) -> dict[str, object]:
        old_db = self.cfg.slot_db(old)
        if not old_db.is_file():
            raise ApplyError(f"old serving slot database is missing: {old_db}")
        return {
            "oracle_schema_version": 1,
            "previous_serving_port": old,
            "old_serving_db": self._file_binding(old_db),
            "old_basis": self._file_binding(self.cfg.basis),
            "old_receipt": self._file_binding(self.cfg.receipt),
            "new_snapshot_id": snapshot_id,
            "new_manifest_sha256": manifest["manifest_sha256"],
            "previous_serving_public_results": self._capture_public_results(manifest),
        }

    def _validate_rollback_oracle(
        self,
        rollback: object,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(rollback, dict)
            or rollback.get("oracle_schema_version") != 1
        ):
            raise ApplyError("rollback oracle is missing or has an unsupported version")
        old = rollback.get("previous_serving_port")
        if old != self.cfg.other_port(candidate):
            raise ApplyError(
                "rollback oracle previous_serving_port does not match the candidate"
            )
        if rollback.get("new_snapshot_id") != snapshot_id:
            raise ApplyError("rollback oracle snapshot identity mismatch")
        if rollback.get("new_manifest_sha256") != manifest_sha256:
            raise ApplyError("rollback oracle manifest identity mismatch")
        return rollback

    def _validate_pending_rollback_inputs(
        self,
        rollback: Mapping[str, Any],
        snapshot_id: str,
        manifest_sha256: str,
    ) -> None:
        if not self.cfg.claimed.is_file() or snapshot_id_of(self.cfg.claimed) != snapshot_id:
            raise ApplyError("pending release immutable base identity mismatch")
        manifest = self._load_manifest(snapshot_id)
        if manifest["manifest_sha256"] != manifest_sha256:
            raise ApplyError("pending release manifest identity mismatch")
        old = str(rollback["previous_serving_port"])
        self._assert_file_binding(
            self.cfg.slot_db(old), rollback.get("old_serving_db"), "old serving DB"
        )
        self._assert_file_binding(self.cfg.basis, rollback.get("old_basis"), "basis")
        self._assert_file_binding(
            self.cfg.receipt, rollback.get("old_receipt"), "receipt"
        )
        results = rollback.get("previous_serving_public_results")
        if not isinstance(results, dict) or set(results) != set(SEARCH_FIELDS):
            raise ApplyError("rollback oracle public result set is malformed")
        for field in SEARCH_FIELDS:
            record = results[field]
            if not isinstance(record, dict) or not isinstance(record.get("term"), str):
                raise ApplyError(f"rollback oracle public probe {field} is malformed")
            if record["term"] != manifest["probes"][field]["term"]:
                raise ApplyError(
                    f"rollback oracle public probe {field} is not bound to the manifest"
                )
            self._validate_result_set(
                record.get("result"), f"rollback public probe {field}"
            )

    def _validate_consumer_verified_authority(
        self,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
    ) -> Mapping[str, Any]:
        evidence = self._observe_finalize_authority(snapshot_id)
        mismatches = self._validate_finalize_authority_evidence(
            evidence,
            candidate,
            snapshot_id,
            manifest_sha256,
            require_mismatch=False,
        )
        if mismatches:
            raise FinalizeAuthorityError("; ".join(mismatches), evidence)
        return evidence

    def _observe_finalize_authority(self, snapshot_id: str) -> dict[str, object]:
        active_port = self.active_port()
        if active_port in self.cfg.ports:
            active_status = "known"
        elif active_port is None:
            active_status = "unavailable-at-capture"
        else:
            active_status = "unknown-port"
        manifest_path = self._manifest_path(snapshot_id)
        observed_snapshot_id: object = None
        observed_manifest_sha256: object = None
        manifest_validation_error: str | None = None
        try:
            raw_manifest = manifest_path.read_bytes()
        except FileNotFoundError:
            manifest_binding: dict[str, object] = {"present": False, "sha256": None}
            manifest_status = "missing"
            manifest_validation_error = "manifest sidecar missing at authority capture"
        except OSError as exc:
            manifest_binding = {"present": None, "sha256": None}
            manifest_status = "unreadable"
            manifest_validation_error = str(exc)
        else:
            manifest_binding = {
                "present": True,
                "sha256": hashlib.sha256(raw_manifest).hexdigest(),
            }
            try:
                payload = json.loads(raw_manifest.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ManifestError("manifest root is not an object")
                validate_manifest(payload)
                observed_snapshot_id = payload.get("snapshot_id")
                observed_manifest_sha256 = payload.get("manifest_sha256")
                manifest_status = "verified"
            except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
                manifest_status = "invalid"
                manifest_validation_error = str(exc)
        return {
            "active_port_observed": active_port,
            "active_port_status": active_status,
            **self._observe_claimed_authority(),
            "manifest": manifest_binding,
            "manifest_identity_status": manifest_status,
            "observed_snapshot_id": observed_snapshot_id,
            "observed_manifest_sha256": observed_manifest_sha256,
            "manifest_validation_error": manifest_validation_error,
        }

    def _observe_claimed_authority(self) -> dict[str, object]:
        digest = hashlib.sha256()
        try:
            with self.cfg.claimed.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except FileNotFoundError:
            return {
                "claimed": {"present": False, "sha256": None},
                "claimed_status": "missing",
                "claimed_validation_error": "claimed DB missing at authority capture",
            }
        except OSError as exc:
            return {
                "claimed": {"present": None, "sha256": None},
                "claimed_status": "unreadable",
                "claimed_validation_error": str(exc),
            }
        return {
            "claimed": {"present": True, "sha256": digest.hexdigest()},
            "claimed_status": "bound",
            "claimed_validation_error": None,
        }

    def _validate_finalize_authority_evidence(
        self,
        evidence: object,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        *,
        require_mismatch: bool,
    ) -> list[str]:
        required = {
            "active_port_observed",
            "active_port_status",
            "claimed",
            "claimed_status",
            "claimed_validation_error",
            "manifest",
            "manifest_identity_status",
            "observed_snapshot_id",
            "observed_manifest_sha256",
            "manifest_validation_error",
        }
        if not isinstance(evidence, dict) or set(evidence) != required:
            raise ApplyError("finalize authority evidence has an invalid shape")
        active = evidence["active_port_observed"]
        active_status = evidence["active_port_status"]
        if not (
            (active_status == "known" and active in self.cfg.ports)
            or (active_status == "unavailable-at-capture" and active is None)
            or (
                active_status == "unknown-port"
                and isinstance(active, str)
                and active not in self.cfg.ports
            )
        ):
            raise ApplyError("finalize authority active port status/value conflict")
        claimed = evidence["claimed"]
        claimed_status = evidence["claimed_status"]
        claimed_error = evidence["claimed_validation_error"]
        if claimed_status == "bound":
            if (
                self._validate_file_binding_shape(
                    claimed, "finalize authority claimed DB"
                )["present"]
                is not True
                or claimed_error is not None
            ):
                raise ApplyError("bound claimed authority evidence is inconsistent")
        elif claimed_status == "missing":
            if (
                self._validate_file_binding_shape(
                    claimed, "finalize authority claimed DB"
                )["present"]
                is not False
                or not isinstance(claimed_error, str)
                or not claimed_error
            ):
                raise ApplyError("missing claimed authority evidence is inconsistent")
        elif claimed_status == "unreadable":
            if (
                claimed != {"present": None, "sha256": None}
                or not isinstance(claimed_error, str)
                or not claimed_error
            ):
                raise ApplyError("unreadable claimed authority evidence is inconsistent")
        else:
            raise ApplyError("claimed authority evidence has an invalid status")
        manifest = evidence["manifest"]
        if not isinstance(manifest, dict) or set(manifest) != {"present", "sha256"}:
            raise ApplyError("finalize authority manifest binding is malformed")
        identity_status = evidence["manifest_identity_status"]
        observed_snapshot = evidence["observed_snapshot_id"]
        observed_manifest = evidence["observed_manifest_sha256"]
        validation_error = evidence["manifest_validation_error"]
        if identity_status == "verified":
            if (
                self._validate_file_binding_shape(
                    manifest, "finalize authority manifest"
                )["present"]
                is not True
                or not isinstance(observed_snapshot, str)
                or FULL_SHA256_RE.fullmatch(observed_snapshot) is None
                or not isinstance(observed_manifest, str)
                or FULL_SHA256_RE.fullmatch(observed_manifest) is None
                or validation_error is not None
            ):
                raise ApplyError("verified manifest authority evidence is inconsistent")
        elif identity_status == "invalid":
            if (
                self._validate_file_binding_shape(
                    manifest, "finalize authority manifest"
                )["present"]
                is not True
                or observed_snapshot is not None
                or observed_manifest is not None
                or not isinstance(validation_error, str)
                or not validation_error
            ):
                raise ApplyError("invalid manifest authority evidence is inconsistent")
        elif identity_status == "missing":
            if (
                self._validate_file_binding_shape(
                    manifest, "finalize authority manifest"
                )["present"]
                is not False
                or observed_snapshot is not None
                or observed_manifest is not None
                or not isinstance(validation_error, str)
                or not validation_error
            ):
                raise ApplyError("missing manifest authority evidence is inconsistent")
        elif identity_status == "unreadable":
            if (
                manifest != {"present": None, "sha256": None}
                or observed_snapshot is not None
                or observed_manifest is not None
                or not isinstance(validation_error, str)
                or not validation_error
            ):
                raise ApplyError("unreadable manifest authority evidence is inconsistent")
        else:
            raise ApplyError("manifest authority evidence has an invalid identity status")
        mismatches: list[str] = []
        if active != candidate:
            mismatches.append("consumer_verified active upstream mismatch")
        if claimed_status != "bound" or claimed["sha256"] != snapshot_id:
            mismatches.append("consumer_verified immutable base identity mismatch")
        if (
            identity_status != "verified"
            or observed_snapshot != snapshot_id
            or observed_manifest != manifest_sha256
        ):
            mismatches.append("consumer_verified manifest identity mismatch")
        if require_mismatch and not mismatches:
            raise ApplyError("finalize blocked evidence does not prove an authority mismatch")
        return mismatches

    def _move_to_quarantine(self, source: Path, destination: Path) -> Path:
        """Move one expected artifact exactly once.

        The destination is fixed in the durable `quarantining` intent.  A
        replay therefore recognizes an already completed rename instead of
        inventing another name and losing the evidence-to-failure binding.
        """
        if destination.exists():
            if source.exists():
                raise ApplyError(
                    f"quarantine source and destination both exist: {source}, "
                    f"{destination}"
                )
            return destination
        if not source.exists():
            raise ApplyError(
                f"quarantine evidence disappeared before it was persisted: {source}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        fsync_path(destination)
        return destination

    def _quarantine_destinations(
        self, snapshot_id: str, failure_id: str
    ) -> dict[str, Path]:
        root = self.cfg.quarantine_dir / snapshot_id
        return {
            "base": root / f"base.{failure_id}.db",
            "candidate": root / f"candidate.{failure_id}.db",
            "manifest": root / f"manifest.{failure_id}.json",
            "failure": root / f"failure.{failure_id}.json",
        }

    def _complete_quarantine(self, entry: Mapping[str, Any]) -> None:
        """Finish a previously journalled quarantine intent idempotently."""
        candidate = entry.get("candidate_port")
        snapshot_id = entry.get("snapshot_id")
        retry_count = entry.get("automatic_fresh_rebuild_retries_used")
        intent = entry.get("quarantine")
        if candidate not in self.cfg.ports:
            raise ApplyError("quarantining journal has an invalid candidate port")
        if not isinstance(snapshot_id, str) or not FULL_SHA256_RE.fullmatch(snapshot_id):
            raise ApplyError("quarantining journal has no full snapshot SHA-256")
        if type(retry_count) is not int or retry_count not in (0, 1):
            raise ApplyError("quarantining journal has an invalid retry_count")
        if not isinstance(intent, dict):
            raise ApplyError("quarantining journal has no quarantine intent")
        failure_id = intent.get("failure_id")
        evidence_status = intent.get("evidence_status")
        if not isinstance(failure_id, str) or not re.fullmatch(r"[0-9a-f]{32}", failure_id):
            raise ApplyError("quarantining journal has an invalid failure_id")
        if (
            not isinstance(evidence_status, dict)
            or set(evidence_status) != {"base", "candidate", "manifest"}
            or any(
                value not in {"captured", "not-applicable", "missing-at-failure"}
                for value in evidence_status.values()
            )
        ):
            raise ApplyError("quarantining journal has invalid evidence status")
        if (
            evidence_status["base"] == "not-applicable"
            or evidence_status["manifest"] == "not-applicable"
        ):
            raise ApplyError("quarantining journal has impossible evidence status")
        for key in ("phase", "failure_category", "message", "failed_at"):
            if not isinstance(intent.get(key), str) or not intent[key]:
                raise ApplyError(f"quarantining journal has invalid {key}")
        active_port_status = intent.get("active_port_status")
        active_port_at_failure = intent.get("active_port_at_failure")
        if (
            active_port_status == "known"
            and active_port_at_failure not in self.cfg.ports
        ) or (
            active_port_status == "unavailable-at-capture"
            and active_port_at_failure is not None
        ):
            raise ApplyError("quarantining journal has inconsistent active port status")
        if active_port_status not in {"known", "unavailable-at-capture"}:
            raise ApplyError("quarantining journal has invalid active port status")
        retire_candidate = intent.get("retire_candidate_before_capture")
        retirement_failure = intent.get("candidate_retirement_failure")
        retirement_failed_at = intent.get("candidate_retirement_failed_at")
        if type(retire_candidate) is not bool:
            raise ApplyError("quarantining journal has invalid retirement intent")
        if (retirement_failure is None) != (retirement_failed_at is None) or (
            retirement_failure is not None
            and (
                not isinstance(retirement_failure, str)
                or not retirement_failure
                or not isinstance(retirement_failed_at, str)
                or not retirement_failed_at
            )
        ):
            raise ApplyError("quarantining journal has incomplete retirement evidence")
        if not retire_candidate and retirement_failure is not None:
            raise ApplyError("quarantining journal has impossible retirement evidence")

        if retire_candidate:
            try:
                self._retire_candidate(str(candidate))
            except ApplyError as exc:
                updated_intent = dict(intent)
                updated_intent["candidate_retirement_failure"] = str(exc)
                updated_intent["candidate_retirement_failed_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                self.journal_write(
                    "quarantining",
                    str(candidate),
                    snapshot_id,
                    manifest_sha256=entry.get("manifest_sha256"),
                    retry_count=retry_count,
                    quarantine=updated_intent,
                )
                raise

        paths = self._quarantine_destinations(snapshot_id, failure_id)
        sources = {
            "base": self.cfg.claimed,
            "candidate": self.cfg.slot_db(candidate),
            "manifest": self._manifest_path(snapshot_id),
        }
        evidence: dict[str, str | None] = {}
        evidence_sha256: dict[str, str | None] = {}
        for label in ("base", "candidate", "manifest"):
            if evidence_status[label] == "captured":
                evidence_path = self._move_to_quarantine(sources[label], paths[label])
                evidence[label] = str(evidence_path)
                evidence_sha256[label] = snapshot_id_of(evidence_path)
            else:
                evidence[label] = None
                evidence_sha256[label] = None

        manifest_sha256 = entry.get("manifest_sha256")
        if manifest_sha256 is not None and (
            not isinstance(manifest_sha256, str)
            or not FULL_SHA256_RE.fullmatch(manifest_sha256)
        ):
            raise ApplyError("quarantining journal has an invalid manifest identity")
        manifest_identity_status = "unavailable"
        observed_manifest_sha256: str | None = None
        manifest_evidence = paths["manifest"]
        if evidence["manifest"] is not None:
            try:
                manifest_payload = json.loads(
                    manifest_evidence.read_text(encoding="utf-8")
                )
                if not isinstance(manifest_payload, dict):
                    raise ManifestError("manifest envelope is not an object")
                validate_manifest(manifest_payload)
                observed = manifest_payload.get("manifest_sha256")
                observed_manifest_sha256 = (
                    observed if isinstance(observed, str) else None
                )
                if (
                    manifest_payload.get("snapshot_id") == snapshot_id
                    and observed_manifest_sha256 == manifest_sha256
                ):
                    manifest_identity_status = "verified"
                else:
                    manifest_identity_status = "mismatch"
            except (OSError, json.JSONDecodeError, ManifestError):
                manifest_identity_status = "mismatch"
        snapshot_identity_status = "unavailable"
        if evidence_sha256["base"] is not None:
            snapshot_identity_status = (
                "verified"
                if evidence_sha256["base"] == snapshot_id
                else "mismatch"
            )
        failure: dict[str, object] = {
            "failure_schema_version": 1,
            "failure_id": failure_id,
            "snapshot_id": snapshot_id,
            "last_validated_manifest_sha256": manifest_sha256,
            "observed_manifest_sha256": observed_manifest_sha256,
            "manifest_identity_status": manifest_identity_status,
            "candidate_port": candidate,
            "active_port_at_failure": intent.get("active_port_at_failure"),
            "active_port_status": intent.get("active_port_status"),
            "phase": intent["phase"],
            "failure_category": intent["failure_category"],
            "message": intent["message"],
            "automatic_retries_used": retry_count,
            "automatic_retry_limit": 1,
            "automatic_retry_disposition": (
                "exhausted" if intent["failure_category"] == "retry-exhausted"
                else "not-eligible"
            ),
            "failed_at": intent["failed_at"],
            "candidate_retirement_required": retire_candidate,
            "candidate_retirement_failure": retirement_failure,
            "candidate_retirement_failed_at": retirement_failed_at,
            "evidence": evidence,
            "evidence_sha256": evidence_sha256,
            "evidence_status": evidence_status,
            "snapshot_identity_status": snapshot_identity_status,
        }
        self._atomic_json_write(paths["failure"], failure)
        failure_sha256 = snapshot_id_of(paths["failure"])
        self.journal_write(
            "quarantined",
            "",
            None,
            failure_id=failure_id,
            failure_path=str(paths["failure"]),
            failure_sha256=failure_sha256,
        )

    def _validate_quarantined_entry(self, entry: Mapping[str, Any]) -> None:
        if entry.get("journal_schema_version") != 2:
            raise ApplyError("quarantined journal has an unsupported schema version")
        failure_path_raw = entry.get("failure_path")
        failure_sha256 = entry.get("failure_sha256")
        failure_id = entry.get("failure_id")
        if not isinstance(failure_path_raw, str) or not failure_path_raw:
            raise ApplyError("quarantined journal has no failure_path")
        if (
            not isinstance(failure_sha256, str)
            or not FULL_SHA256_RE.fullmatch(failure_sha256)
        ):
            raise ApplyError("quarantined journal has no full failure SHA-256")
        if not isinstance(failure_id, str) or not re.fullmatch(r"[0-9a-f]{32}", failure_id):
            raise ApplyError("quarantined journal has an invalid failure_id")
        failure_path = Path(failure_path_raw)
        try:
            failure_path.resolve().relative_to(self.cfg.quarantine_dir.resolve())
        except (OSError, ValueError) as exc:
            raise ApplyError("quarantined journal failure_path escapes quarantine") from exc
        if not failure_path.is_file() or snapshot_id_of(failure_path) != failure_sha256:
            raise ApplyError("quarantined failure record no longer matches its binding")
        try:
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplyError(f"quarantined failure record is unreadable: {exc}") from exc
        if (
            not isinstance(failure, dict)
            or failure.get("failure_schema_version") != 1
            or failure.get("failure_id") != failure_id
            or failure_path.name != f"failure.{failure_id}.json"
        ):
            raise ApplyError("quarantined failure record identity mismatch")
        snapshot_id = failure.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not FULL_SHA256_RE.fullmatch(snapshot_id):
            raise ApplyError("quarantined failure record has no full snapshot SHA-256")
        if failure_path.parent.name != snapshot_id:
            raise ApplyError("quarantined failure path does not match snapshot identity")
        evidence = failure.get("evidence")
        evidence_sha256 = failure.get("evidence_sha256")
        evidence_status = failure.get("evidence_status")
        labels = {"base", "candidate", "manifest"}
        if (
            not isinstance(evidence, dict)
            or set(evidence) != labels
            or not isinstance(evidence_sha256, dict)
            or set(evidence_sha256) != labels
            or not isinstance(evidence_status, dict)
            or set(evidence_status) != labels
        ):
            raise ApplyError("quarantined failure evidence index is malformed")
        if (
            evidence_status["base"] not in {"captured", "missing-at-failure"}
            or evidence_status["manifest"]
            not in {"captured", "missing-at-failure"}
            or evidence_status["candidate"]
            not in {"captured", "not-applicable", "missing-at-failure"}
        ):
            raise ApplyError("quarantined failure evidence status is invalid")
        active_port_status = failure.get("active_port_status")
        active_port_at_failure = failure.get("active_port_at_failure")
        if (
            active_port_status == "known"
            and active_port_at_failure not in self.cfg.ports
        ) or (
            active_port_status == "unavailable-at-capture"
            and active_port_at_failure is not None
        ):
            raise ApplyError("quarantined active port status is inconsistent")
        if active_port_status not in {"known", "unavailable-at-capture"}:
            raise ApplyError("quarantined active port status is invalid")
        expected_paths = self._quarantine_destinations(snapshot_id, failure_id)
        for label in labels:
            evidence_path_raw = evidence[label]
            recorded_sha = evidence_sha256[label]
            status = evidence_status[label]
            if status in {"not-applicable", "missing-at-failure"}:
                if evidence_path_raw is not None or recorded_sha is not None:
                    raise ApplyError(
                        f"quarantined {label} absence conflicts with evidence status"
                    )
                continue
            if (
                status != "captured"
                or
                not isinstance(evidence_path_raw, str)
                or Path(evidence_path_raw) != expected_paths[label]
                or not isinstance(recorded_sha, str)
                or not FULL_SHA256_RE.fullmatch(recorded_sha)
                or not expected_paths[label].is_file()
                or snapshot_id_of(expected_paths[label]) != recorded_sha
            ):
                raise ApplyError(f"quarantined {label} evidence binding mismatch")
        base_sha = evidence_sha256["base"]
        expected_snapshot_status = (
            "unavailable"
            if base_sha is None
            else "verified"
            if base_sha == snapshot_id
            else "mismatch"
        )
        if failure.get("snapshot_identity_status") != expected_snapshot_status:
            raise ApplyError("quarantined snapshot identity status is inconsistent")
        retirement_required = failure.get("candidate_retirement_required")
        retirement_failure = failure.get("candidate_retirement_failure")
        retirement_failed_at = failure.get("candidate_retirement_failed_at")
        if type(retirement_required) is not bool or (
            (retirement_failure is None) != (retirement_failed_at is None)
        ):
            raise ApplyError("quarantined retirement evidence is malformed")
        if retirement_failure is not None and (
            not retirement_required
            or not isinstance(retirement_failure, str)
            or not retirement_failure
            or not isinstance(retirement_failed_at, str)
            or not retirement_failed_at
        ):
            raise ApplyError("quarantined retirement evidence is inconsistent")
        declared_manifest_sha = failure.get("last_validated_manifest_sha256")
        if declared_manifest_sha is not None and (
            not isinstance(declared_manifest_sha, str)
            or not FULL_SHA256_RE.fullmatch(declared_manifest_sha)
        ):
            raise ApplyError("quarantined last validated manifest identity is invalid")
        expected_manifest_status = "unavailable"
        observed_manifest_sha: str | None = None
        if evidence["manifest"] is not None:
            expected_manifest_status = "mismatch"
            try:
                manifest_payload = json.loads(
                    expected_paths["manifest"].read_text(encoding="utf-8")
                )
                if not isinstance(manifest_payload, dict):
                    raise ManifestError("manifest envelope is not an object")
                validate_manifest(manifest_payload)
                observed = manifest_payload.get("manifest_sha256")
                observed_manifest_sha = observed if isinstance(observed, str) else None
                if (
                    manifest_payload.get("snapshot_id") == snapshot_id
                    and observed_manifest_sha == declared_manifest_sha
                ):
                    expected_manifest_status = "verified"
            except (OSError, json.JSONDecodeError, ManifestError):
                pass
        if (
            failure.get("manifest_identity_status") != expected_manifest_status
            or failure.get("observed_manifest_sha256") != observed_manifest_sha
        ):
            raise ApplyError("quarantined manifest identity status is inconsistent")
        retries_used = failure.get("automatic_retries_used")
        if (
            type(retries_used) is not int
            or retries_used not in (0, 1)
            or failure.get("automatic_retry_limit") != 1
            or failure.get("automatic_retry_disposition")
            not in {"not-eligible", "exhausted"}
        ):
            raise ApplyError("quarantined automatic retry disposition is invalid")

    def _validate_committed_entry(self, entry: Mapping[str, Any]) -> None:
        snapshot_id = entry.get("snapshot_id")
        if entry.get("journal_schema_version") != 2:
            raise ApplyError("committed journal has an unsupported schema version")
        manifest_sha256 = entry.get("manifest_sha256")
        serving_port = entry.get("serving_port")
        if not isinstance(snapshot_id, str) or not FULL_SHA256_RE.fullmatch(snapshot_id):
            raise ApplyError("committed journal has no full snapshot SHA-256")
        if (
            not isinstance(manifest_sha256, str)
            or not FULL_SHA256_RE.fullmatch(manifest_sha256)
        ):
            raise ApplyError("committed journal has no full manifest SHA-256")
        if serving_port not in self.cfg.ports or self.active_port() != serving_port:
            raise ApplyError("committed journal does not match the active serving port")
        if not self.cfg.basis.is_file() or snapshot_id_of(self.cfg.basis) != snapshot_id:
            raise ApplyError("committed basis does not match the snapshot identity")
        try:
            receipt = json.loads(self.cfg.receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplyError(f"committed receipt is unreadable: {exc}") from exc
        if (
            not isinstance(receipt, dict)
            or receipt.get("receipt_schema_version") != 2
            or receipt.get("snapshot_id") != snapshot_id
            or receipt.get("manifest_sha256") != manifest_sha256
            or receipt.get("serving_port") != serving_port
            or not isinstance(receipt.get("completed_at"), str)
            or not receipt["completed_at"]
        ):
            raise ApplyError("committed receipt does not match the journal projection")

    def _record_legacy_committed_unverified(
        self, entry: Mapping[str, Any]
    ) -> None:
        legacy_snapshot_id = entry.get("snapshot_id")
        serving_port = self.active_port()
        if isinstance(legacy_snapshot_id, str) and re.fullmatch(
            r"[0-9a-f]{16}", legacy_snapshot_id
        ):
            recorded_snapshot_id: str | None = legacy_snapshot_id
            legacy_snapshot_id_status = "truncated-16-hex"
        elif legacy_snapshot_id == "":
            recorded_snapshot_id = None
            legacy_snapshot_id_status = "unavailable-before-hash"
        else:
            raise ApplyError("legacy committed journal cannot be classified safely")
        if (
            "manifest_sha256" in entry
            or serving_port not in self.cfg.ports
        ):
            raise ApplyError("legacy committed journal cannot be classified safely")
        self.journal_write(
            "legacy_committed_unverified",
            serving_port,
            None,
            legacy_snapshot_id=recorded_snapshot_id,
            legacy_snapshot_id_status=legacy_snapshot_id_status,
            identity_status="unavailable-legacy",
            legacy_basis=self._file_binding(self.cfg.basis),
            legacy_receipt=self._file_binding(self.cfg.receipt),
        )

    def _unlink_durable(self, path: Path) -> None:
        if not path.exists():
            return
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _retire_candidate(self, candidate: str) -> None:
        unit = self.cfg.serve_unit(candidate)
        if not self.r.ok(
            *self.cfg.systemctl_args("disable", unit, mutate=True)
        ):
            raise ApplyError(f"could not disable candidate unit {unit}")
        if not self.r.ok(*self.cfg.systemctl_args("stop", unit, mutate=True)):
            raise ApplyError(f"could not stop candidate unit {unit}")

    def _ensure_slot_serving(self, port: str) -> None:
        if self.slot_serving(port):
            return
        unit = self.cfg.serve_unit(port)
        if not self.r.ok(*self.cfg.systemctl_args("restart", unit, mutate=True)):
            raise ApplyError(f"could not restart slot {port}")
        deadline = time.monotonic() + self.cfg.health_wait_s
        while time.monotonic() < deadline:
            if self.slot_serving(port):
                return
            time.sleep(2)
        raise ApplyError(f"slot {port} did not become healthy")

    def _stop_slot_and_confirm(self, port: str) -> None:
        unit = self.cfg.serve_unit(port)
        if not self.r.ok(*self.cfg.systemctl_args("stop", unit, mutate=True)):
            raise ApplyError(f"could not stop old slot {port} for route proof")
        if self.slot_serving(port):
            raise ApplyError(f"old slot {port} still serves after systemctl stop")

    def _quarantine(
        self,
        *,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str | None,
        phase: str,
        kind: str,
        message: str,
        retry_count: int,
        include_candidate: bool = True,
        retire_candidate_before_capture: bool = False,
    ) -> None:
        failure_id = uuid.uuid4().hex
        active_port = self.active_port()
        candidate_exists = self.cfg.slot_db(candidate).exists()
        intent = {
            "failure_id": failure_id,
            "phase": phase,
            "failure_category": kind,
            "message": message,
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "active_port_at_failure": (
                active_port if active_port in self.cfg.ports else None
            ),
            "active_port_status": (
                "known" if active_port in self.cfg.ports else "unavailable-at-capture"
            ),
            "retire_candidate_before_capture": retire_candidate_before_capture,
            "evidence_status": {
                "base": (
                    "captured" if self.cfg.claimed.exists() else "missing-at-failure"
                ),
                "candidate": (
                    "not-applicable"
                    if not include_candidate
                    else "captured"
                    if candidate_exists
                    else "missing-at-failure"
                ),
                "manifest": (
                    "captured"
                    if self._manifest_path(snapshot_id).exists()
                    else "missing-at-failure"
                ),
            },
        }
        self.journal_write(
            "quarantining",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            quarantine=intent,
        )
        entry = self.journal_read()
        if entry is None:
            raise ApplyError("quarantining intent disappeared after durable write")
        self._complete_quarantine(entry)

    def _rollback_pending_consumer(
        self,
        *,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        rollback: object,
        retry_count: int,
        reason: str,
        failure_category: str,
    ) -> None:
        try:
            bound = self._validate_rollback_oracle(
                rollback, candidate, snapshot_id, manifest_sha256
            )
            self._validate_pending_rollback_inputs(
                bound, snapshot_id, manifest_sha256
            )
        except ApplyError as exc:
            message = f"{reason}; rollback oracle invalid: {exc}"
            self._record_rollback_blocked(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                rollback_evidence=rollback,
                message=message,
            )
            raise ApplyError(
                f"consumer rollback blocked by invalid oracle ({exc}); "
                "basis/receipt not advanced"
            ) from exc
        old = str(bound["previous_serving_port"])
        try:
            self._ensure_slot_serving(old)
            self._switch_include(old)
            rollback_started = time.monotonic()
            if not self.slot_serving(old):
                raise ApplyError(f"old slot {old} is not serving after rollback")
            self._assert_file_binding(
                self.cfg.slot_db(old), bound.get("old_serving_db"), "old serving DB"
            )
            self._assert_file_binding(self.cfg.basis, bound.get("old_basis"), "basis")
            self._assert_file_binding(
                self.cfg.receipt, bound.get("old_receipt"), "receipt"
            )
            self._verify_public_baseline(bound)
            self._verify_route_proof_baseline(bound)
            drain_remaining = self.cfg.nginx_rollback_drain_s - (
                time.monotonic() - rollback_started
            )
            if drain_remaining > 0:
                time.sleep(drain_remaining)
            self._retire_candidate(candidate)
        except ApplyError as exc:
            failure_message = f"{reason}; rollback failed: {exc}"
            self._record_pending_failure(
                state="rollback_failed",
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                rollback=bound,
                category=failure_category,
                message=failure_message,
            )
            raise ApplyError(
                f"consumer rollback failed ({exc}); basis/receipt not advanced"
            ) from exc
        self._quarantine(
            candidate=candidate,
            snapshot_id=snapshot_id,
            manifest_sha256=manifest_sha256,
            phase="post-switch-consumer",
            kind=failure_category,
            message=reason,
            retry_count=retry_count,
        )

    def finalize(
        self,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
    ) -> None:
        """Finalize only after the public consumer gate is durable."""
        cfg = self.cfg
        old = cfg.other_port(candidate)
        new_unit = cfg.serve_unit(candidate)
        old_unit = cfg.serve_unit(old)

        if self.active_port() != candidate:
            raise ApplyError(
                "active nginx upstream does not match the consumer-verified candidate"
            )
        manifest = self._load_manifest(snapshot_id)
        if manifest["manifest_sha256"] != manifest_sha256:
            raise ApplyError(
                "manifest identity changed before final commit; basis/receipt unchanged"
            )
        if not cfg.claimed.is_file() or snapshot_id_of(cfg.claimed) != snapshot_id:
            raise ApplyError(
                "immutable base-only artifact is missing at final commit; refusing "
                "to use the serving candidate as basis"
            )
        self.verify_base_snapshot(cfg.claimed)

        if not self.slot_serving(candidate):
            self.r.ok(*cfg.systemctl_args("restart", new_unit, mutate=True))
            deadline = time.monotonic() + self.cfg.health_wait_s
            while time.monotonic() < deadline:
                if self.slot_serving(candidate):
                    break
                time.sleep(2)
            else:
                raise ApplyError(
                    f"candidate slot {candidate} is not serving; staying consumer_verified"
                )

        if not self.r.ok(*cfg.systemctl_args("enable", new_unit, mutate=True)):
            raise ApplyError("enable candidate failed; staying consumer_verified")
        if not self.r.ok(*cfg.systemctl_args("disable", old_unit, mutate=True)):
            raise ApplyError(f"disable {old_unit} failed; staying consumer_verified")
        self.r.ok(*cfg.systemctl_args("stop", old_unit, mutate=True))

        cfg.basis_dir.mkdir(parents=True, exist_ok=True)
        basis_tmp = cfg.basis.with_suffix(".tmp")
        shutil.copyfile(cfg.claimed, basis_tmp)
        fsync_path(basis_tmp)
        os.replace(basis_tmp, cfg.basis)
        fsync_path(cfg.basis)

        tmp = cfg.receipt.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "receipt_schema_version": 2,
                    "snapshot_id": snapshot_id,
                    "manifest_sha256": manifest_sha256,
                    "serving_port": candidate,
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        )
        fsync_path(tmp)
        os.replace(tmp, cfg.receipt)
        fsync_path(cfg.receipt)
        self.journal_write(
            "committed",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
        )
        self._unlink_durable(cfg.claimed)
        self._unlink_durable(self._manifest_path(snapshot_id))
        log(f"committed: snapshot {snapshot_id} serving on {candidate}")

    # ---------------- recovery ----------------

    def _release_entry(
        self, entry: Mapping[str, Any]
    ) -> tuple[str, str, str, int]:
        if entry.get("journal_schema_version") != 2:
            raise ApplyError(
                "legacy in-flight journal cannot be recovered by this state machine; "
                "settle it before rollout"
            )
        candidate = entry.get("candidate_port")
        snapshot_id = entry.get("snapshot_id")
        manifest_sha256 = entry.get("manifest_sha256")
        retry_count = entry.get("automatic_fresh_rebuild_retries_used")
        if candidate not in self.cfg.ports:
            raise ApplyError("journal release entry has an invalid candidate port")
        if not isinstance(snapshot_id, str) or FULL_SHA256_RE.fullmatch(snapshot_id) is None:
            raise ApplyError("journal release entry has no full snapshot SHA-256")
        if (
            not isinstance(manifest_sha256, str)
            or FULL_SHA256_RE.fullmatch(manifest_sha256) is None
        ):
            raise ApplyError("journal release entry has no full manifest SHA-256")
        if type(retry_count) is not int or retry_count not in (0, 1):
            raise ApplyError("journal release entry has an invalid retry_count")
        return candidate, snapshot_id, manifest_sha256, retry_count

    def _retry_verifier_identity(self, entry: Mapping[str, Any]) -> str:
        identity = entry.get("verifier_identity")
        if not isinstance(identity, str) or VERIFIER_ID_RE.fullmatch(identity) is None:
            raise ApplyError("retry checkpoint has an invalid verifier identity")
        return identity

    def _record_verifier_identity_block(
        self,
        *,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        retry_count: int,
        checkpoint_identity: str,
    ) -> None:
        message = (
            "verifier identity changed since the retry checkpoint: "
            f"{checkpoint_identity} -> {VERIFIER_VERSION}; "
            "automatic fresh retry blocked"
        )
        self.journal_write(
            "retry_blocked_verifier_changed",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            verifier_identity=checkpoint_identity,
            observed_verifier_identity=VERIFIER_VERSION,
            last_failure_category="verifier-identity-changed",
            last_failure_message=message,
            last_failure_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def _record_pending_failure(
        self,
        *,
        state: str,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        retry_count: int,
        category: str,
        message: str,
        rollback: Mapping[str, Any] | None = None,
    ) -> None:
        details: dict[str, object] = {
            "manifest_sha256": manifest_sha256,
            "retry_count": retry_count,
            "last_failure_category": category,
            "last_failure_message": message,
            "last_failure_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }
        if rollback is not None:
            details["rollback"] = rollback
        self.journal_write(state, candidate, snapshot_id, **details)

    def _record_rollback_blocked(
        self,
        *,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        retry_count: int,
        rollback_evidence: object,
        message: str,
    ) -> None:
        self.journal_write(
            "rollback_blocked_invalid_oracle",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            rollback_evidence=rollback_evidence,
            last_failure_category="rollback-oracle-invalid",
            last_failure_message=message,
            last_failure_at=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        )

    def _record_finalize_blocked(
        self,
        *,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        retry_count: int,
        authority_evidence: Mapping[str, Any],
        message: str,
    ) -> None:
        self.journal_write(
            "finalize_blocked_invalid_authority",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            authority_evidence=authority_evidence,
            last_failure_category="finalize-authority-invalid",
            last_failure_message=message,
            last_failure_at=time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        )

    def _finalize_with_failure_record(
        self,
        candidate: str,
        snapshot_id: str,
        manifest_sha256: str,
        retry_count: int,
    ) -> None:
        try:
            self._validate_consumer_verified_authority(
                candidate, snapshot_id, manifest_sha256
            )
        except FinalizeAuthorityError as exc:
            message = f"finalize authority invalid: {exc}"
            self._record_finalize_blocked(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                authority_evidence=exc.evidence,
                message=message,
            )
            raise ApplyError(
                f"finalize blocked by invalid authority ({exc}); "
                "basis/receipt not advanced"
            ) from exc
        try:
            self.finalize(candidate, snapshot_id, manifest_sha256)
        except ApplyError as exc:
            try:
                self._record_pending_failure(
                    state="consumer_verified",
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    retry_count=retry_count,
                    category="finalize-failed",
                    message=str(exc),
                )
            except FinalizeAuthorityError as record_exc:
                self._record_finalize_blocked(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    retry_count=retry_count,
                    authority_evidence=record_exc.evidence,
                    message=(
                        f"finalize failed: {exc}; authority could not be "
                        f"revalidated for retry: {record_exc}"
                    ),
                )
            raise

    def _continue_release(
        self,
        *,
        candidate: str,
        snapshot_id: str,
        manifest: Mapping[str, Any],
        retry_count: int,
        rebuild: bool,
    ) -> None:
        manifest_sha256 = str(manifest["manifest_sha256"])
        if rebuild:
            try:
                self.verify_base_snapshot(self.cfg.claimed)
            except Exception as exc:  # deterministic verifier failures, not crashes
                failure_message = f"{type(exc).__name__}: {exc}"
                self._quarantine(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    phase="base-verification",
                    kind="deterministic-gate",
                    message=failure_message,
                    retry_count=retry_count,
                    include_candidate=False,
                )
                if isinstance(exc, ApplyError):
                    raise
                raise ApplyError(
                    "base verification failed deterministically; snapshot quarantined "
                    f"({failure_message})"
                ) from exc
            try:
                self._materialize_and_verify_candidate(candidate, snapshot_id, manifest)
            except Exception as exc:  # deterministic verifier failures, not crashes
                failure_message = f"{type(exc).__name__}: {exc}"
                self._quarantine(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    phase="candidate-rebuild-verification",
                    kind="deterministic-gate",
                    message=failure_message,
                    retry_count=retry_count,
                )
                if isinstance(exc, ApplyError):
                    raise
                raise ApplyError(
                    "candidate rebuild/verification failed deterministically; "
                    f"snapshot quarantined ({failure_message})"
                ) from exc
            self.journal_write(
                "prepared",
                candidate,
                snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
            )
        elif not self.cfg.slot_db(candidate).is_file():
            self._quarantine(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                phase="prepared",
                kind="verified-checkpoint-missing",
                message="prepared candidate database is missing",
                retry_count=retry_count,
            )
            raise ApplyError("prepared candidate database is missing; snapshot quarantined")

        candidate_unit = self.cfg.serve_unit(candidate)
        log("starting candidate slot")
        if not self.r.ok(
            *self.cfg.systemctl_args("restart", candidate_unit, mutate=True)
        ):
            message = "could not restart the candidate slot; prepared retry retained"
            try:
                self._retire_candidate(candidate)
            except ApplyError as exc:
                message = (
                    "could not restart or retire the candidate slot; "
                    f"prepared retry retained ({exc})"
                )
            self._record_pending_failure(
                state="prepared",
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                category="candidate-restart-failed",
                message=message,
            )
            raise ApplyError(message)
        deadline = time.monotonic() + self.cfg.health_wait_s
        while time.monotonic() < deadline:
            if self.slot_serving(candidate):
                break
            time.sleep(2)
        else:
            message = "candidate never became healthy; prepared retry retained"
            try:
                self._retire_candidate(candidate)
            except ApplyError as exc:
                message = (
                    "candidate never became healthy and could not be retired; "
                    f"prepared retry retained ({exc})"
                )
            self._record_pending_failure(
                state="prepared",
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                category="candidate-health-failed",
                message=message,
            )
            raise ApplyError(message)
        if not self.r.ok(
            *self.cfg.systemctl_args("enable", candidate_unit, mutate=True)
        ):
            message = "could not enable the candidate slot; prepared retry retained"
            try:
                self._retire_candidate(candidate)
            except ApplyError as exc:
                message = (
                    "candidate enable failed and it could not be retired; "
                    f"prepared retry retained ({exc})"
                )
            self._record_pending_failure(
                state="prepared",
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                category="candidate-enable-failed",
                message=message,
            )
            raise ApplyError(message)

        try:
            self._verify_http_against_manifest(
                f"http://127.0.0.1:{candidate}/api/v1/timeline",
                manifest,
                vantage="candidate-slot",
            )
        except HttpProbeInfrastructureError as exc:
            message = (
                f"candidate HTTP infrastructure probe failed ({exc}); "
                "prepared retry retained"
            )
            try:
                self._retire_candidate(candidate)
            except ApplyError as retire_exc:
                message = (
                    f"candidate HTTP infrastructure probe failed ({exc}) and candidate "
                    f"could not be retired ({retire_exc}); prepared retry retained"
                )
            self._record_pending_failure(
                state="prepared",
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                category="candidate-http-infrastructure-failed",
                message=message,
            )
            raise ApplyError(message) from exc
        except ApplyError as exc:
            try:
                self._quarantine(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    phase="candidate-http",
                    kind="deterministic-gate",
                    message=str(exc),
                    retry_count=retry_count,
                    retire_candidate_before_capture=True,
                )
            except ApplyError as retire_exc:
                raise ApplyError(
                    "candidate HTTP gate failed and candidate could not be retired; "
                    f"quarantine remains pending: {retire_exc}"
                ) from exc
            raise

        old = self.cfg.other_port(candidate)
        try:
            rollback = self._capture_rollback_oracle(old, snapshot_id, manifest)
            self._verify_route_proof_baseline(rollback)
            self.assert_nginx_link_matches()
        except ApplyError as exc:
            message = f"pre-switch preparation failed ({exc}); prepared retry retained"
            try:
                self._retire_candidate(candidate)
            except ApplyError as retire_exc:
                message = (
                    f"pre-switch preparation failed ({exc}) and candidate could not "
                    f"be retired ({retire_exc}); prepared retry retained"
                )
            self._record_pending_failure(
                state="prepared",
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
                category="pre-switch-preparation-failed",
                message=message,
            )
            raise ApplyError(message) from exc
        self.journal_write(
            "switching_pending_consumer",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            rollback=rollback,
        )
        try:
            self._switch_include(candidate)
        except ApplyError as exc:
            self._rollback_pending_consumer(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                rollback=rollback,
                retry_count=retry_count,
                reason=f"switch failed before consumer gate: {exc}",
                failure_category="switch-failed",
            )
            raise ApplyError(f"switch failed and candidate was quarantined: {exc}") from exc

        self.journal_write(
            "switched_pending_consumer",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            rollback=rollback,
        )
        try:
            self._verify_public_against_manifest(manifest)
        except ApplyError as exc:
            self._rollback_pending_consumer(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                rollback=rollback,
                retry_count=retry_count,
                reason=f"consumer gate failed: {exc}",
                failure_category="consumer-gate-failed",
            )
            raise ApplyError(f"consumer gate failed; switched back and quarantined: {exc}") from exc

        old = self.cfg.other_port(candidate)
        self.journal_write(
            "old_stopping_pending_consumer",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
            rollback=rollback,
        )
        try:
            self._stop_slot_and_confirm(old)
            self._verify_route_proof_against_manifest(manifest)
            self._verify_public_against_manifest(manifest)
        except ApplyError as exc:
            self._rollback_pending_consumer(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                rollback=rollback,
                retry_count=retry_count,
                reason=f"route identity gate failed: {exc}",
                failure_category="route-identity-gate-failed",
            )
            raise ApplyError(
                f"route identity gate failed; switched back and quarantined: {exc}"
            ) from exc

        self.journal_write(
            "consumer_verified",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=retry_count,
        )
        self._finalize_with_failure_record(
            candidate, snapshot_id, manifest_sha256, retry_count
        )

    def reconcile(self) -> None:
        entry = self.journal_read()
        if entry is None:
            return
        state = entry["state"]
        if (
            entry.get("journal_schema_version") is None
            and state in {"claiming", "prepared", "switching", "switched"}
        ):
            raise ApplyError(
                f"legacy in-flight journal state {state!r} cannot be recovered by "
                "this state machine; settle it before rollout"
            )
        if state == "committed" and entry.get("journal_schema_version") is None:
            self._record_legacy_committed_unverified(entry)
            return
        if state == "legacy_committed_unverified":
            return
        if state == "committed":
            self._validate_committed_entry(entry)
            snapshot_id = entry.get("snapshot_id")
            manifest_sha256 = entry.get("manifest_sha256")
            if (
                isinstance(snapshot_id, str)
                and FULL_SHA256_RE.fullmatch(snapshot_id)
                and isinstance(manifest_sha256, str)
                and FULL_SHA256_RE.fullmatch(manifest_sha256)
                and self.cfg.claimed.is_file()
                and self.cfg.basis.is_file()
                and snapshot_id_of(self.cfg.claimed) == snapshot_id
                and snapshot_id_of(self.cfg.basis) == snapshot_id
            ):
                self._unlink_durable(self.cfg.claimed)
            sidecar = (
                self._manifest_path(snapshot_id)
                if isinstance(snapshot_id, str) and FULL_SHA256_RE.fullmatch(snapshot_id)
                else None
            )
            if sidecar is not None and sidecar.is_file():
                manifest = self._load_manifest(snapshot_id)
                if manifest["manifest_sha256"] != manifest_sha256:
                    raise ApplyError("committed manifest identity changed before cleanup")
                self._unlink_durable(sidecar)
            return
        if state == "idle":
            return
        if state == "quarantining":
            self._complete_quarantine(entry)
            return
        if state == "quarantined":
            self._validate_quarantined_entry(entry)
            return
        if state == "claiming":
            candidate = entry.get("candidate_port")
            if candidate not in self.cfg.ports:
                raise ApplyError("claiming journal has an invalid candidate port")
            if not self.cfg.claimed.exists():
                self.journal_write("idle", self.active_port() or "", None)
                return
            snapshot_id = snapshot_id_of(self.cfg.claimed)
            checkpoint_identity = self._retry_verifier_identity(entry)
            if checkpoint_identity != VERIFIER_VERSION:
                message = (
                    "verifier identity changed since the claiming checkpoint: "
                    f"{checkpoint_identity} -> {VERIFIER_VERSION}; "
                    "automatic fresh retry blocked"
                )
                self._quarantine(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=None,
                    phase="claiming",
                    kind="verifier-identity-changed",
                    message=message,
                    retry_count=0,
                    include_candidate=False,
                )
                raise ApplyError(message)
            try:
                manifest = self._load_manifest(snapshot_id)
            except ApplyError as exc:
                self._quarantine(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=None,
                    phase="manifest",
                    kind="deterministic-gate",
                    message=str(exc),
                    retry_count=1,
                    include_candidate=False,
                )
                raise
            manifest_sha256 = str(manifest["manifest_sha256"])
            self.journal_write(
                "rebuilding",
                candidate,
                snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=1,
            )
            self._continue_release(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest=manifest,
                retry_count=1,
                rebuild=True,
            )
            return
        if state in {"rebuilding", "prepared"}:
            candidate, snapshot_id, manifest_sha256, retry_count = self._release_entry(entry)
            checkpoint_identity = self._retry_verifier_identity(entry)
            if checkpoint_identity != VERIFIER_VERSION:
                self._record_verifier_identity_block(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    retry_count=retry_count,
                    checkpoint_identity=checkpoint_identity,
                )
                raise ApplyError(
                    "verifier identity changed since the retry checkpoint; "
                    "automatic fresh retry blocked"
                )
            if retry_count >= 1:
                self._retire_candidate(candidate)
                self._quarantine(
                    candidate=candidate,
                    snapshot_id=snapshot_id,
                    manifest_sha256=manifest_sha256,
                    phase=state,
                    kind="retry-exhausted",
                    message="second pre-switch crash; automatic fresh retry exhausted",
                    retry_count=retry_count,
                )
                return
            if not self.cfg.claimed.is_file() or snapshot_id_of(self.cfg.claimed) != snapshot_id:
                raise ApplyError("original immutable base cannot be recovered by journal hash")
            manifest = self._load_manifest(snapshot_id)
            if manifest["manifest_sha256"] != manifest_sha256:
                raise ApplyError("manifest identity changed since the crashed attempt")
            retry_count = 1
            if state == "prepared":
                old = self.cfg.other_port(candidate)
                if self.active_port() != old:
                    self._switch_include(old)
                self._retire_candidate(candidate)
            self.journal_write(
                state,
                candidate,
                snapshot_id,
                manifest_sha256=manifest_sha256,
                retry_count=retry_count,
            )
            self._continue_release(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest=manifest,
                retry_count=retry_count,
                rebuild=True,
            )
            return
        if state in {
            "switching_pending_consumer",
            "switched_pending_consumer",
            "old_stopping_pending_consumer",
            "rollback_failed",
        }:
            candidate, snapshot_id, manifest_sha256, retry_count = self._release_entry(entry)
            self._rollback_pending_consumer(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=manifest_sha256,
                rollback=entry.get("rollback"),
                retry_count=retry_count,
                reason=str(
                    entry.get("last_failure_message")
                    or f"crash recovered from {state}"
                ),
                failure_category=str(
                    entry.get("last_failure_category")
                    or "post-switch-crash-recovered"
                ),
            )
            return
        if state == "rollback_blocked_invalid_oracle":
            raise ApplyError(
                str(
                    entry.get("last_failure_message")
                    or "rollback is blocked by an invalid oracle"
                )
            )
        if state == "finalize_blocked_invalid_authority":
            raise ApplyError(
                str(
                    entry.get("last_failure_message")
                    or "finalize authority invalid; manual intervention required"
                )
            )
        if state == "retry_blocked_verifier_changed":
            raise ApplyError(
                str(
                    entry.get("last_failure_message")
                    or "verifier identity changed; automatic fresh retry blocked"
                )
            )
        if state == "consumer_verified":
            candidate, snapshot_id, manifest_sha256, retry_count = self._release_entry(entry)
            self._finalize_with_failure_record(
                candidate, snapshot_id, manifest_sha256, retry_count
            )
            return
        raise ApplyError(f"unrecognised journal state {state!r}")

    # ---------------- main ----------------

    def apply(self) -> int:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)

        lock_fd = os.open(cfg.lock, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ApplyError(f"another deploy or sync holds the lock ({cfg.lock})")
            return self._apply_locked()
        finally:
            # Process exit would release the lock anyway, but callers that
            # reuse this object in-process (tests, a future daemon) must not
            # inherit a lock held by a finished run.
            os.close(lock_fd)

    def _apply_locked(self) -> int:
        cfg = self.cfg
        self.reconcile()

        if not cfg.incoming.exists():
            log("no incoming snapshot; nothing to do")
            return 0

        if not cfg.public_search_url:
            raise ApplyError(
                "AI_RADAR_PUBLIC_SEARCH_URL or AI_RADAR_PUBLIC_URL is required; "
                "incoming snapshot left untouched"
            )

        active = self.active_port()
        if active not in cfg.ports:
            raise ApplyError(
                f"active upstream in {cfg.active_conf} is not one of {cfg.ports}; "
                "run install-server.sh or repair the release include first"
            )
        candidate = cfg.other_port(active)
        log(f"active={active} candidate={candidate}")

        free = self.free_mem_mb()
        if free < cfg.min_free_mem_mb:
            # Fail closed: a stop-start fallback would quietly retract the
            # zero-downtime guarantee this mechanism exists to provide.
            raise ApplyError(
                f"only {free}MB memory available, need {cfg.min_free_mem_mb}MB "
                "to run two slots; keeping the current release"
            )

        # Claim the DB before hashing or selecting a sidecar. The exact full
        # hash-keyed sidecar is unknown until this immutable inode is owned.
        self.journal_write("claiming", candidate, None)
        cfg.incoming.rename(cfg.claimed)
        snapshot_id = snapshot_id_of(cfg.claimed)
        try:
            manifest = self._load_manifest(snapshot_id)
        except ApplyError as exc:
            self._quarantine(
                candidate=candidate,
                snapshot_id=snapshot_id,
                manifest_sha256=None,
                phase="manifest",
                kind="deterministic-gate",
                message=str(exc),
                retry_count=0,
                include_candidate=False,
            )
            raise ApplyError(
                f"claimed snapshot manifest failed validation ({exc}); "
                "snapshot quarantined, active release untouched"
            ) from exc
        manifest_sha256 = str(manifest["manifest_sha256"])
        self.journal_write(
            "rebuilding",
            candidate,
            snapshot_id,
            manifest_sha256=manifest_sha256,
            retry_count=0,
        )
        self._continue_release(
            candidate=candidate,
            snapshot_id=snapshot_id,
            manifest=manifest,
            retry_count=0,
            rebuild=True,
        )
        log(f"release complete: snapshot {snapshot_id} serving on {candidate}")
        return 0


def main() -> int:
    try:
        return Deploy(Config.from_env()).apply()
    except ApplyError as exc:
        print(f"[apply] ✗ {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
