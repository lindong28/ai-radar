"""Reconcile the EdgeOne rule engine against a pinned in-repo snapshot.

EdgeOne force-caches `/app.js` and `/style.css` for 7 days (ADR-039), and the rule
that does so lives in the Tencent Cloud console -- outside the repo. That makes it an
authority the test suite cannot see: if someone changes caching there, no in-repo test
notices, and a resource can go stale at the edge for a week. The 2026-08-17 incident
was that failure shape on an already-covered path.

Two design rules, both learned from review findings against an earlier version of this
module that reported "no drift" for six different real console changes:

1. **Do not model the rule engine.** The snapshot records every enabled rule verbatim,
   in order, including nested `SubRules` and every action type. An earlier version kept
   only `Name == "Cache"` actions, sorted them, and compared as a set -- so a `CacheKey`
   rule that drops the `v` query parameter (which would silently void every version
   string this repo generates), a reordered priority, and a cache action nested one
   level down all normalized to "unchanged". Semantic filtering turns every unmodeled
   shape into a false clean.

2. **A snapshot proves "same as last time", never "last time was safe".** So accepting
   a snapshot additionally requires that every force-cached path maps to an asset with
   a content-derived version string (`scripts/bump_frontend_assets.ASSETS`). Without
   that check, accepting a drifted `/vendor.js` would make it permanently invisible.

Not-verified is never reported as clean: unreadable payloads, short reads, and
unparseable conditions all refuse to record or pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import db
from ..runtime_env import read_value

ENV_SECRET_ID = "EDGEONE_SECRET_ID"
ENV_SECRET_KEY = "EDGEONE_SECRET_KEY"
ENV_ZONE_ID = "EDGEONE_ZONE_ID"
REQUIRED_ENV = (ENV_SECRET_ID, ENV_SECRET_KEY, ENV_ZONE_ID)

SNAPSHOT_PATH = db.PROJECT_ROOT / "web" / "edgeone-cache-rules.json"
PINS_PATH = db.PROJECT_ROOT / "web" / "asset-pins.json"

EXIT_CLEAN = 0
EXIT_DRIFT = 1
EXIT_NOT_VERIFIED = 2

# The API caps a page at 1000 (SDK: DescribeL7AccRulesRequest.Limit); we still loop on
# TotalCount rather than trusting one page, because a short read must not read as clean.
PAGE_LIMIT = 1000

# Caching-relevant for the human summary only. Coverage uses the narrower notion below:
# a CacheKey or MaxAge rule does not force node caching, so demanding a `?v=` for its
# path would reject legitimate console states.
_SUMMARY_ACTIONS = frozenset(
    {"Cache", "CacheKey", "CachePrefresh", "MaxAge", "StatusCodeCache", "OfflineCache"}
)

# The ONE condition shape this module claims to understand: an optional host equality
# clause ANDed with an exact path enumeration. Everything else -- `not in`, `contains`,
# prefix matches, ${http.request.full_uri}, file-extension matches, anything nested --
# is reported as unparseable, never as covered.
#
# The default is inverted on purpose. An earlier version extracted `/`-prefixed literals
# and treated whatever it found as the complete match set, so `${...uri.path} not in
# ['/app.js']` -- which matches every OTHER path -- read as "covered by /app.js". A parser
# that cannot interpret operators must not get a vote on whether something is safe; it may
# only recognise the exact form it was written for and abstain on everything else.
_UNDERSTOOD_CONDITION = re.compile(
    r"^\s*(?:\$\{http\.request\.host\}\s+in\s+\[[^\]]*\]\s+and\s+)?"
    r"\$\{http\.request\.uri\.path\}\s+in\s+\[(?P<paths>[^\]]*)\]\s*$"
)
_PATH_LITERAL = re.compile(r"'(/[^']*)'")


@dataclass(frozen=True)
class EdgeOneConfig:
    secret_id: str
    secret_key: str
    zone_id: str


class EdgeOneUnreadable(RuntimeError):
    """The zone's rules could not be read completely; the caller must not report clean."""


def load_config() -> EdgeOneConfig | None:
    values = {key: read_value(key).strip() for key in REQUIRED_ENV}
    if not all(values.values()):
        return None
    return EdgeOneConfig(
        secret_id=values[ENV_SECRET_ID],
        secret_key=values[ENV_SECRET_KEY],
        zone_id=values[ENV_ZONE_ID],
    )


def missing_env(*, values: dict[str, str] | None = None) -> list[str]:
    resolved = values if values is not None else {key: read_value(key).strip() for key in REQUIRED_ENV}
    return [key for key in REQUIRED_ENV if not resolved.get(key)]


def normalize_rules(rules: list[Any], *, total_count: int | None = None) -> list[dict[str, Any]]:
    """Record enabled rules verbatim and in order.

    Order is preserved rather than sorted because the rule engine evaluates top to bottom
    (SDK: "规则按照从上到下的顺序执行"), so a pure reordering changes the effective TTL.
    No action filtering and no recursion flattening: nested SubRules ride along inside the
    branch payload, so a cache action moved into a sub-rule still changes the recorded value.

    Raises EdgeOneUnreadable when the payload contradicts itself, so an unreadable response
    can never be recorded as an empty-but-valid baseline and then match itself forever.
    """
    if rules is None or not isinstance(rules, list):
        raise EdgeOneUnreadable(f"expected a list of rules, got {type(rules).__name__}")
    if total_count is not None and len(rules) != total_count:
        raise EdgeOneUnreadable(f"read {len(rules)} rule(s) but the zone reports {total_count}")

    normalized: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise EdgeOneUnreadable(f"rule #{index} is {type(rule).__name__}, not an object")
        if rule.get("Status") != "enable":
            continue
        if rule.get("Branches") is None:
            # null means "could not read", per the SDK; collapsing it into [] with `or []`
            # would let an unreadable rule pass coverage as if it cached nothing.
            raise EdgeOneUnreadable(f"rule {rule.get('RuleId') or index} returned Branches=null")
        normalized.append(rule)
    return normalized


def iter_cache_branches(rule: dict[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    """(branch, nested) for every branch carrying a caching action, at any depth.

    `nested` marks a branch reached through SubRules. Its effective match is the parent
    condition ANDed with its own, and this module does not compose conditions -- so a
    nested cache branch is always treated as not-understood rather than judged on its own
    condition alone, which would drop the parent's path restriction.
    """
    found: list[tuple[dict[str, Any], bool]] = []

    def walk(branches: Any, nested: bool) -> None:
        if not isinstance(branches, list):
            return
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            actions = branch.get("Actions") or []
            if any(isinstance(a, dict) and a.get("Name") in _SUMMARY_ACTIONS for a in actions):
                found.append((branch, nested))
            for sub in branch.get("SubRules") or []:
                if isinstance(sub, dict):
                    walk(sub.get("Branches"), True)

    walk(rule.get("Branches"), False)
    return found


def _switch(config: Any) -> str | None:
    """'on' / 'off', or None when the switch is missing or carries a value we do not know.

    Collapsing unknown into 'off' is what turns an unreadable config into a false
    "does not cache" -- so unknown is kept distinct and always handled conservatively.
    """
    if not isinstance(config, dict):
        return None
    value = config.get("Switch")
    return value if value in ("on", "off") else None


def _action_overrides_origin(params: Any) -> bool:
    """Whether one Cache action lets the edge outlive what the origin says.

    This -- not "does it cache at all" -- is what makes a version string necessary.
    Under CustomTime the edge pins its own TTL and ignores the origin's Cache-Control,
    so only a new URL can invalidate it. Under FollowOrigin the origin's own headers
    still govern; whether the origin actually sends them is a fact this module cannot
    observe (see check_asset_coverage), so those paths are reported, not judged.

    The SDK allows at most one of CustomTime / FollowOrigin / NoCache to be on. Each is
    read as a tri-state: on decides, off falls through, unreadable answers True so an
    unfamiliar shape gets the stricter treatment.
    """
    if not isinstance(params, dict):
        return True
    if not any(params.get(key) is not None for key in ("CustomTime", "FollowOrigin", "NoCache")):
        return True  # malformed: exactly one of the three should have been configured

    custom = params.get("CustomTime")
    if custom is not None and _switch(custom) != "off":
        return True

    follow = params.get("FollowOrigin")
    if follow is not None and _switch(follow) is None:
        return True

    no_cache = params.get("NoCache")
    if no_cache is not None and _switch(no_cache) is None:
        return True
    return False


def _action_follows_origin(params: Any) -> bool:
    follow = isinstance(params, dict) and params.get("FollowOrigin")
    return isinstance(follow, dict) and _switch(follow) == "on"


def follows_origin(branch: dict[str, Any]) -> bool:
    """True when the branch defers freshness to the origin's own Cache-Control."""
    return any(
        _action_follows_origin(action.get("CacheParameters"))
        for action in branch.get("Actions") or []
        if isinstance(action, dict) and action.get("Name") == "Cache"
    )


def forces_node_cache(branch: dict[str, Any]) -> bool:
    """True when *any* Cache action on the branch lets the edge override the origin.

    Deliberately a union rather than "the last action wins": the public API defines
    Actions as an array without saying whether a name may repeat or how duplicates
    override each other, and that question cannot be settled without writing to a live
    zone. Taking the union means neither override semantics can produce a false clean --
    at worst a duplicate that the server would have cancelled shows up as not-verified,
    which is visible and correctable, unlike a silently missed cache rule.
    """
    return any(
        _action_overrides_origin(action.get("CacheParameters"))
        for action in branch.get("Actions") or []
        if isinstance(action, dict) and action.get("Name") == "Cache"
    )


def understood_paths(condition: str) -> list[str] | None:
    """Paths matched by `condition`, or None when the shape is outside what we can read."""
    match = _UNDERSTOOD_CONDITION.match(condition or "")
    if not match:
        return None
    paths = sorted(set(_PATH_LITERAL.findall(match.group("paths"))))
    return paths or None


def summarize_paths(condition: str) -> list[str]:
    """Human-readable only; never a verdict input (see understood_paths)."""
    return sorted(set(_PATH_LITERAL.findall(condition or "")))


@dataclass(frozen=True)
class CoverageReport:
    """Force-cached paths lacking a content-derived version, plus conditions we cannot read."""

    uncovered: list[str] = field(default_factory=list)
    unparseable: list[str] = field(default_factory=list)
    origin_governed: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return not self.uncovered and not self.unparseable


def check_asset_coverage(rules: list[dict[str, Any]], assets: tuple[str, ...]) -> CoverageReport:
    """Every force-cached path must resolve to an asset that gets a content-derived `?v=`.

    A snapshot alone cannot express this: it only says "same as last time". Accepting a
    newly force-cached path without this check would pin it as the expected state forever.
    """
    covered = {f"/{asset}" for asset in assets}
    uncovered: list[str] = []
    unparseable: list[str] = []
    origin_governed: list[str] = []
    for rule in rules:
        for branch, nested in iter_cache_branches(rule):
            overrides = forces_node_cache(branch)
            defers = follows_origin(branch)
            if not overrides and not defers:
                continue
            condition = branch.get("Condition") or ""
            paths = None if nested else understood_paths(condition)
            if paths is None:
                unparseable.append(f"{condition} (nested)" if nested else condition)
                continue
            if overrides:
                uncovered.extend(path for path in paths if path not in covered)
                continue
            # Deferred to the origin. Whether such a path can go stale depends on the
            # origin's own Cache-Control, which cannot be observed from here: a public
            # GET can be served from the edge (EO-Cache-Status: HIT), returning the
            # cached object's headers rather than what the origin sends now. Rather than
            # claim knowledge we do not have, these are surfaced on every run so the
            # human who pins the snapshot reviews them.
            origin_governed.extend(paths)
    return CoverageReport(
        uncovered=sorted(set(uncovered)),
        unparseable=sorted(set(unparseable)),
        origin_governed=sorted(set(origin_governed)),
    )


@dataclass(frozen=True)
class DriftReport:
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    reordered: bool = False

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.reordered)


def compare_to_snapshot(current: list[dict[str, Any]], recorded: list[dict[str, Any]]) -> DriftReport:
    """Sequence comparison: membership *and* order, since order decides which rule wins."""
    current_keys = [json.dumps(rule, sort_keys=True, ensure_ascii=False) for rule in current]
    recorded_keys = [json.dumps(rule, sort_keys=True, ensure_ascii=False) for rule in recorded]
    added = [key for key in current_keys if key not in recorded_keys]
    removed = [key for key in recorded_keys if key not in current_keys]
    reordered = not added and not removed and current_keys != recorded_keys
    return DriftReport(
        added=[json.loads(key) for key in added],
        removed=[json.loads(key) for key in removed],
        reordered=reordered,
    )


def pinned_assets(path: Path | None = None) -> tuple[str, ...]:
    """Assets that receive a content-derived `?v=`, read from the file that ships with the repo.

    `scripts/bump_frontend_assets.ASSETS` is the authoring-side authority but is not
    importable at runtime; `web/asset-pins.json` mirrors it and is asserted equal to it by
    tests/test_frontend_asset_versions.py, so reading the mirror cannot drift silently.
    """
    target = path or PINS_PATH
    if not target.exists():
        raise EdgeOneUnreadable(f"{target} is missing, so covered assets cannot be determined")
    return tuple(sorted(json.loads(target.read_text(encoding="utf-8"))))


def load_snapshot(path: Path | None = None) -> list[dict[str, Any]] | None:
    target = path or SNAPSHOT_PATH
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8")).get("rules", [])


def write_snapshot(rules: list[dict[str, Any]], path: Path | None = None) -> None:
    target = path or SNAPSHOT_PATH
    body = {
        "note": "EdgeOne rule-engine rules mirrored from the Tencent Cloud console (ADR-039). "
        "Every enabled rule is recorded verbatim and in order; refresh deliberately with "
        "./run.sh admin edgeone check --update-snapshot",
        "rules": rules,
    }
    target.write_text(json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch_rules(config: EdgeOneConfig) -> list[Any]:
    """Read every rule in the zone, paging until TotalCount is satisfied.

    A single default request returns at most 20 rules (SDK default Limit), so a zone with
    more would hide anything past the first page -- and hiding reads as clean.
    """
    from tencentcloud.common import credential
    from tencentcloud.teo.v20220901 import models, teo_client

    client = teo_client.TeoClient(credential.Credential(config.secret_id, config.secret_key), "")
    collected: list[Any] = []
    total: int | None = None
    while True:
        request = models.DescribeL7AccRulesRequest()
        request.from_json_string(
            json.dumps({"ZoneId": config.zone_id, "Offset": len(collected), "Limit": PAGE_LIMIT})
        )
        payload = json.loads(client.DescribeL7AccRules(request).to_json_string())
        if total is None:
            total = payload.get("TotalCount")
            if not isinstance(total, int):
                # Without a declared total there is no way to tell a complete read from a
                # truncated one, and a truncated read that happens to match the snapshot
                # would report clean.
                raise EdgeOneUnreadable("the zone did not report TotalCount; completeness is unknowable")
        page = payload.get("Rules")
        if page is None:
            raise EdgeOneUnreadable(f"the zone reports {total} rule(s) but returned none")
        collected.extend(page)
        if len(collected) >= total:
            break
        if not page:
            raise EdgeOneUnreadable(f"read {len(collected)} of {total} rule(s) before the pages ran dry")
    if len(collected) != total:
        raise EdgeOneUnreadable(f"read {len(collected)} rule(s) but the zone reports {total}")
    return collected


def purge_urls(config: EdgeOneConfig, urls: list[str]) -> dict[str, Any]:
    from tencentcloud.common import credential
    from tencentcloud.teo.v20220901 import models, teo_client

    client = teo_client.TeoClient(credential.Credential(config.secret_id, config.secret_key), "")
    request = models.CreatePurgeTaskRequest()
    request.from_json_string(json.dumps({"ZoneId": config.zone_id, "Type": "purge_url", "Targets": urls}))
    return json.loads(client.CreatePurgeTask(request).to_json_string())


def purge_failures(response: dict[str, Any]) -> list[tuple[str, str]]:
    """(target, reason) for every URL that was NOT purged, even though a JobId came back.

    The SDK's FailReason carries `Reason` (str) and `Targets` (list of str), so one entry
    can cover several URLs -- counting entries would under-report the failures.
    """
    failures: list[tuple[str, str]] = []
    for item in response.get("FailedList") or []:
        if not isinstance(item, dict):
            continue
        reason = item.get("Reason") or "no reason given"
        for target in item.get("Targets") or []:
            failures.append((str(target), str(reason)))
    return failures
