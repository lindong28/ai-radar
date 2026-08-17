"""Cover the EdgeOne console-vs-repo drift check.

Every test named `test_f<N>_*` pins a console change that an earlier version of this
module reported as "no drift" -- each was found by adversarial review, not by the author,
and each is the same failure class the module exists to prevent. They are kept as named
regressions so a future simplification of `normalize_rules` cannot quietly reopen one.

The load-bearing property throughout is negative: unreadable, short, or unparseable reads
must never come out as clean.
"""

from __future__ import annotations

import copy
import json

import pytest

from airadar import cli
from airadar.admin import edgeone

# Shaped after the SDK's DescribeL7AccRulesResponse model: Rules -> Branches -> Actions,
# with the match expressed as a condition *string* (the new rule engine does not return a
# structured condition tree).
BASE_RULES = [
    {
        "Status": "enable",
        "RuleId": "rule-aaa",
        "RuleName": "force cache static assets",
        "RulePriority": 1,
        "Branches": [
            {
                "Condition": "${http.request.host} in ['news.aiplanet.live'] and "
                "${http.request.uri.path} in ['/style.css', '/app.js']",
                "Actions": [
                    {"Name": "Cache", "CacheParameters": {"CustomTime": {"Switch": "on", "CacheTime": 604800}}}
                ],
                "SubRules": [],
            }
        ],
    },
    {
        "Status": "disable",
        "RuleId": "rule-bbb",
        "RuleName": "retired rule",
        "RulePriority": 2,
        "Branches": [
            {
                "Condition": "${http.request.uri.path} in ['/legacy.css']",
                "Actions": [{"Name": "Cache", "CacheParameters": {"CustomTime": {"Switch": "on", "CacheTime": 60}}}],
                "SubRules": [],
            }
        ],
    },
]
ASSETS = ("app.js", "style.css")


def _baseline() -> list[dict]:
    return edgeone.normalize_rules(copy.deepcopy(BASE_RULES))


def _drifted(mutate) -> edgeone.DriftReport:
    rules = copy.deepcopy(BASE_RULES)
    mutate(rules)
    return edgeone.compare_to_snapshot(edgeone.normalize_rules(rules), _baseline())


def test_normalize_keeps_enabled_rules_verbatim_and_in_order() -> None:
    rules = _baseline()
    assert [rule["RuleId"] for rule in rules] == ["rule-aaa"]
    assert "retired rule" not in json.dumps(rules)


def test_r7_a_description_only_edit_is_still_drift() -> None:
    """"Verbatim" has to mean verbatim, or the claim in the docstring is false."""

    def mutate(rules):
        rules[0]["Description"] = ["changed in the console"]

    assert _drifted(mutate).has_drift


def test_r3_a_negated_path_condition_is_never_treated_as_covered() -> None:
    """`not in ['/app.js']` matches every OTHER path; extracting '/app.js' inverts the verdict."""
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} not in ['/app.js']"
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
    assert not coverage.verified and coverage.unparseable


def test_r1_other_match_types_are_not_treated_as_covered() -> None:
    for condition in (
        "${http.request.full_uri} in ['https://news.aiplanet.live/vendor.js']",
        "${http.request.file_extension} in ['js']",
        "${http.request.uri.path} contains '/vendor'",
        "true",
    ):
        rules = copy.deepcopy(BASE_RULES)
        rules[0]["Branches"][0]["Condition"] = condition
        coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
        assert not coverage.verified, f"{condition!r} must not read as covered"


def test_r2_a_nested_cache_branch_is_never_judged_on_its_own_condition() -> None:
    """The parent's path restriction is lost if only the child condition is read."""
    rules = copy.deepcopy(BASE_RULES)
    rules.append(
        {
            "Status": "enable",
            "RuleId": "rule-nested",
            "RuleName": "nested",
            "Branches": [
                {
                    "Condition": "${http.request.uri.path} in ['/vendor.js']",
                    "Actions": [],
                    "SubRules": [
                        {
                            "Branches": [
                                {
                                    # Parseable *and* covered on its own -- so if the nested
                                    # guard were removed this would read as verified, which is
                                    # exactly the false clean the guard exists to prevent.
                                    "Condition": "${http.request.uri.path} in ['/app.js']",
                                    "Actions": [
                                        {"Name": "Cache", "CacheParameters": {"CustomTime": {"Switch": "on"}}}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
    assert not coverage.verified and any("nested" in item for item in coverage.unparseable)


def test_r5_a_null_branches_field_is_unreadable_not_empty() -> None:
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"] = None
    with pytest.raises(edgeone.EdgeOneUnreadable, match="Branches=null"):
        edgeone.normalize_rules(rules)


def test_r6_a_cachekey_only_rule_does_not_demand_a_version_string() -> None:
    """CacheKey shapes the key but pins no TTL; demanding ?v= there rejects a legal state."""
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0] = {
        "Condition": "${http.request.uri.path} in ['/api/foo']",
        "Actions": [{"Name": "CacheKey", "CacheKeyParameters": {"QueryString": {"Switch": "off"}}}],
        "SubRules": [],
    }
    assert edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS).verified


def test_no_drift_when_the_console_matches_the_snapshot() -> None:
    assert not edgeone.compare_to_snapshot(_baseline(), _baseline()).has_drift


def test_f3_a_rule_beyond_the_first_page_is_not_silently_dropped() -> None:
    """A short read is a read failure, not an empty zone."""
    with pytest.raises(edgeone.EdgeOneUnreadable, match="reports 21"):
        edgeone.normalize_rules(copy.deepcopy(BASE_RULES), total_count=21)


def test_f4_a_cachekey_rule_that_drops_the_v_parameter_is_drift() -> None:
    """The nastiest one: it would void every version string this repo generates."""

    def mutate(rules):
        rules[0]["Branches"][0]["Actions"].append(
            {"Name": "CacheKey", "CacheKeyParameters": {"QueryString": {"Switch": "off"}}}
        )

    assert _drifted(mutate).has_drift


def test_f5_reordering_two_rules_is_drift() -> None:
    """The engine evaluates top to bottom, so order decides which TTL wins."""

    def mutate(rules):
        rules[1]["Status"] = "enable"
        rules.reverse()

    assert _drifted(mutate).has_drift


def test_f6_a_cache_action_nested_in_subrules_is_drift() -> None:
    def mutate(rules):
        rules.append(
            {
                "Status": "enable",
                "RuleId": "rule-ccc",
                "RuleName": "nested",
                "RulePriority": 3,
                "Branches": [
                    {
                        "Condition": "${http.request.host} in ['news.aiplanet.live']",
                        "Actions": [],
                        "SubRules": [
                            {
                                "Branches": [
                                    {
                                        "Condition": "${http.request.uri.path} in ['/vendor.js']",
                                        "Actions": [
                                            {
                                                "Name": "Cache",
                                                "CacheParameters": {"CustomTime": {"Switch": "on", "CacheTime": 604800}},
                                            }
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        )

    assert _drifted(mutate).has_drift


def test_f6_nested_cache_branches_are_reachable_at_all() -> None:
    """Coverage must see through SubRules; judging them is a separate matter (see R2)."""
    rules = copy.deepcopy(BASE_RULES)
    rules.append(
        {
            "Status": "enable",
            "RuleId": "rule-ccc",
            "RuleName": "nested",
            "Branches": [
                {
                    "Condition": "${http.request.host} in ['news.aiplanet.live']",
                    "Actions": [],
                    "SubRules": [
                        {
                            "Branches": [
                                {
                                    "Condition": "${http.request.uri.path} in ['/vendor.js']",
                                    "Actions": [{"Name": "Cache", "CacheParameters": {}}],
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
    assert not coverage.verified


def test_f7_an_unreadable_payload_raises_instead_of_becoming_an_empty_baseline() -> None:
    for payload in (None, "nope", {"not": "a list"}):
        with pytest.raises(edgeone.EdgeOneUnreadable):
            edgeone.normalize_rules(payload)  # type: ignore[arg-type]
    with pytest.raises(edgeone.EdgeOneUnreadable):
        edgeone.normalize_rules(["not-a-dict"])


def test_f8_a_failed_purge_target_is_reported_even_with_a_job_id() -> None:
    """R8: the SDK's FailReason is {Reason, Targets[]} -- one entry can hide several URLs."""
    response = {
        "JobId": "job-1",
        "FailedList": [{"Reason": "quota", "Targets": ["https://x/a.js", "https://x/b.css"]}],
    }
    assert edgeone.purge_failures(response) == [
        ("https://x/a.js", "quota"),
        ("https://x/b.css", "quota"),
    ], "counting entries instead of targets would under-report the failures"
    assert edgeone.purge_failures({"JobId": "job-1"}) == []


def test_f2_an_uncovered_force_cached_path_is_reported() -> None:
    """A snapshot proves 'same as last time'; this proves 'last time was safe'."""
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} in ['/style.css', '/app.js', '/vendor.js']"
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
    assert coverage.uncovered == ["/vendor.js"]


def test_f2_an_unparseable_path_condition_is_never_treated_as_covered() -> None:
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} matches_some_syntax_we_cannot_read"
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
    assert coverage.unparseable and not coverage.verified


def test_coverage_passes_on_the_baseline() -> None:
    assert edgeone.check_asset_coverage(_baseline(), ASSETS).verified


def test_pinned_assets_mirror_the_bump_script(tmp_path) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from bump_frontend_assets import ASSETS as SCRIPT_ASSETS

    assert set(edgeone.pinned_assets()) == set(SCRIPT_ASSETS)
    missing = tmp_path / "asset-pins.json"
    with pytest.raises(edgeone.EdgeOneUnreadable):
        edgeone.pinned_assets(missing)


def test_snapshot_round_trips(tmp_path) -> None:
    path = tmp_path / "edgeone-cache-rules.json"
    assert edgeone.load_snapshot(path) is None, "a missing snapshot must differ from an empty one"
    edgeone.write_snapshot(_baseline(), path)
    assert edgeone.load_snapshot(path) == _baseline()


def _clear_env(monkeypatch) -> None:
    for key in edgeone.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(edgeone, "read_value", lambda key, **_: "")


def test_unconfigured_reports_not_verified_never_clean(monkeypatch, capsys) -> None:
    _clear_env(monkeypatch)
    assert edgeone.load_config() is None
    exit_code = cli._admin_edgeone(cli.build_parser().parse_args(["admin", "edgeone", "check"]))
    assert exit_code == edgeone.EXIT_NOT_VERIFIED != edgeone.EXIT_CLEAN
    out = capsys.readouterr().out
    assert "NOT VERIFIED" in out
    for key in edgeone.REQUIRED_ENV:
        assert key in out, "the operator must be told exactly which key to set"


def test_partial_credentials_still_count_as_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(edgeone, "read_value", lambda key, **_: "v" if key == edgeone.ENV_SECRET_ID else "")
    assert edgeone.load_config() is None
    assert edgeone.missing_env() == [edgeone.ENV_SECRET_KEY, edgeone.ENV_ZONE_ID]


def test_purge_requires_credentials(monkeypatch, capsys) -> None:
    _clear_env(monkeypatch)
    args = cli.build_parser().parse_args(["admin", "edgeone", "purge", "--url", "https://example.com/a.js"])
    assert cli._admin_edgeone(args) == edgeone.EXIT_NOT_VERIFIED
    assert "NOT VERIFIED" in capsys.readouterr().out


def test_cli_registers_both_subcommands() -> None:
    parser = cli.build_parser()
    check = parser.parse_args(["admin", "edgeone", "check", "--update-snapshot"])
    assert (check.admin_command, check.edgeone_command, check.update_snapshot) == ("edgeone", "check", True)
    purge = parser.parse_args(["admin", "edgeone", "purge", "--url", "https://a/x.js", "--url", "https://a/y.css"])
    assert purge.url == ["https://a/x.js", "https://a/y.css"]
    with pytest.raises(SystemExit):
        parser.parse_args(["admin", "edgeone", "purge"])


def test_f3_fetch_rules_pages_until_totalcount_is_satisfied(monkeypatch) -> None:
    """One default request returns at most 20 rules, so a bigger zone must be paged."""
    pages = [
        {"TotalCount": 3, "Rules": [{"RuleId": "r1"}, {"RuleId": "r2"}]},
        {"TotalCount": 3, "Rules": [{"RuleId": "r3"}]},
    ]
    calls: list[dict] = []

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def DescribeL7AccRules(self, request):  # noqa: N802 - mirrors the SDK method name
            calls.append(json.loads(request.to_json_string()))

            class _Resp:
                def to_json_string(_self) -> str:
                    return json.dumps(pages[len(calls) - 1])

            return _Resp()

    _install_fake_sdk(monkeypatch, _FakeClient)
    rules = edgeone.fetch_rules(edgeone.EdgeOneConfig("id", "key", "zone-x"))
    assert [rule["RuleId"] for rule in rules] == ["r1", "r2", "r3"]
    assert [call["Offset"] for call in calls] == [0, 2], "the second page must start where the first ended"


def test_r4_a_missing_totalcount_is_unreadable_not_a_complete_read(monkeypatch) -> None:
    """Without a declared total there is no way to tell a full read from a truncated one."""

    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def DescribeL7AccRules(self, request):  # noqa: N802
            class _Resp:
                def to_json_string(_self) -> str:
                    return json.dumps({"Rules": [{"RuleId": "r1"}]})

            return _Resp()

    _install_fake_sdk(monkeypatch, _FakeClient)
    with pytest.raises(edgeone.EdgeOneUnreadable, match="TotalCount"):
        edgeone.fetch_rules(edgeone.EdgeOneConfig("id", "key", "zone-x"))


def test_f3_a_short_read_raises_rather_than_looking_like_a_small_zone(monkeypatch) -> None:
    class _FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def DescribeL7AccRules(self, request):  # noqa: N802
            class _Resp:
                def to_json_string(_self) -> str:
                    return json.dumps({"TotalCount": 5, "Rules": []})

            return _Resp()

    _install_fake_sdk(monkeypatch, _FakeClient)
    with pytest.raises(edgeone.EdgeOneUnreadable):
        edgeone.fetch_rules(edgeone.EdgeOneConfig("id", "key", "zone-x"))


def _install_fake_sdk(monkeypatch, client_cls) -> None:
    """Stand in for the SDK modules that fetch_rules imports lazily."""
    import sys
    import types

    common = types.ModuleType("tencentcloud.common")
    common.credential = types.SimpleNamespace(Credential=lambda *a, **k: object())

    class _Request:
        def __init__(self) -> None:
            self._body = "{}"

        def from_json_string(self, body: str) -> None:
            self._body = body

        def to_json_string(self) -> str:
            return self._body

    teo = types.ModuleType("tencentcloud.teo.v20220901")
    teo.models = types.SimpleNamespace(DescribeL7AccRulesRequest=_Request, CreatePurgeTaskRequest=_Request)
    teo.teo_client = types.SimpleNamespace(TeoClient=client_cls)
    monkeypatch.setitem(sys.modules, "tencentcloud.common", common)
    monkeypatch.setitem(sys.modules, "tencentcloud.teo.v20220901", teo)


def _cache_action(params: dict) -> list[dict]:
    return [{"Name": "Cache", "CacheParameters": params}]


def test_s1_follow_origin_is_reported_rather_than_judged() -> None:
    """S1, twice corrected. Demanding a ?v= made the gate permanently red on the real zone
    (/wechat cannot carry one). Probing the origin looked like the fix but cannot work: a
    public GET may be served from the edge, so it reports the cached object's headers, not
    what the origin sends now. What is left is to report these paths on every run and let
    the human who pins the snapshot carry that judgement.
    """
    cond = "${http.request.host} in ['news.aiplanet.live'] and ${http.request.uri.path} in ['/vendor.js']"
    rules = edgeone.normalize_rules(
        _rule_with(cond, {"FollowOrigin": {"Switch": "on", "DefaultCache": "on"}, "NoCache": None, "CustomTime": None})
    )
    coverage = edgeone.check_asset_coverage(rules, ASSETS)
    assert coverage.uncovered == []
    assert coverage.origin_governed == ["/vendor.js"]


def test_s1_2_an_inactive_nocache_is_not_a_forced_cache() -> None:
    """A false positive here blocks a legitimate console state from ever being pinned."""
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} in ['/api/foo']"
    rules[0]["Branches"][0]["Actions"] = _cache_action({"NoCache": {"Switch": "off"}})
    assert edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS).verified


def test_an_active_nocache_never_demands_a_version_string() -> None:
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} in ['/api/foo']"
    rules[0]["Branches"][0]["Actions"] = _cache_action({"NoCache": {"Switch": "on"}})
    assert edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS).verified


def test_an_empty_cache_parameters_object_is_treated_as_caching() -> None:
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} in ['/vendor.js']"
    rules[0]["Branches"][0]["Actions"] = _cache_action({})
    assert edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS).uncovered == ["/vendor.js"]


def test_duplicate_cache_actions_cannot_hide_a_forced_cache() -> None:
    """Override semantics for repeated Cache actions are undocumented, so take the union.

    Whichever way the server resolves them, a branch that might pin a TTL must demand a
    version string rather than silently pass.
    """
    rules = copy.deepcopy(BASE_RULES)
    rules[0]["Branches"][0]["Condition"] = "${http.request.uri.path} in ['/vendor.js']"
    rules[0]["Branches"][0]["Actions"] = [
        {"Name": "Cache", "CacheParameters": {"NoCache": {"Switch": "on"}}},
        {"Name": "Cache", "CacheParameters": {"CustomTime": {"Switch": "on", "CacheTime": 604800}}},
    ]
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(rules), ASSETS)
    assert coverage.uncovered == ["/vendor.js"], "an ordering-dependent read would miss this"


REAL_FOLLOW_ORIGIN = {"FollowOrigin": {"Switch": "on", "DefaultCache": "on", "DefaultCacheStrategy": "on", "DefaultCacheTime": 0}, "NoCache": None, "CustomTime": None}
REAL_FORCE_CACHE = {"FollowOrigin": None, "NoCache": None, "CustomTime": {"Switch": "on", "IgnoreCacheControl": "on", "CacheTime": 604800}}


def _rule_with(condition: str, params: dict) -> list[dict]:
    return [
        {
            "Status": "enable",
            "RuleId": "r1",
            "RuleName": "probe",
            "Branches": [{"Condition": condition, "Actions": [{"Name": "Cache", "CacheParameters": params}], "SubRules": []}],
        }
    ]


def test_follow_origin_paths_are_not_demanded_to_carry_a_version_string() -> None:
    """Measured against the live zone: the origin governs /wechat, so no ?v= is possible.

    Treating this as uncovered made the gate permanently red -- the false-red failure that
    blocks a baseline from ever being pinned.
    """
    cond = "${http.request.host} in ['news.aiplanet.live'] and ${http.request.uri.path} in ['/wechat']"
    rules = edgeone.normalize_rules(_rule_with(cond, REAL_FOLLOW_ORIGIN))
    coverage = edgeone.check_asset_coverage(rules, ASSETS)
    assert coverage.uncovered == []
    assert coverage.origin_governed == ["/wechat"]


def test_ignore_cache_control_paths_still_demand_a_version_string() -> None:
    cond = "${http.request.host} in ['news.aiplanet.live'] and ${http.request.uri.path} in ['/vendor.js']"
    rules = edgeone.normalize_rules(_rule_with(cond, REAL_FORCE_CACHE))
    assert edgeone.check_asset_coverage(rules, ASSETS).uncovered == ["/vendor.js"]




def test_follow_origin_paths_do_not_block_pinning_a_baseline() -> None:
    """They are reported, not judged -- a legitimate zone must remain pinnable."""
    cond = "${http.request.host} in ['news.aiplanet.live'] and ${http.request.uri.path} in ['/wechat']"
    coverage = edgeone.check_asset_coverage(
        edgeone.normalize_rules(_rule_with(cond, REAL_FOLLOW_ORIGIN)), ASSETS
    )
    assert coverage.verified, "reporting must not turn into a permanent red"
    assert coverage.origin_governed == ["/wechat"]


def test_a_host_less_condition_still_yields_a_usable_report() -> None:
    """No host clause used to synthesise https:///path and block the baseline forever."""
    coverage = edgeone.check_asset_coverage(
        edgeone.normalize_rules(_rule_with("${http.request.uri.path} in ['/wechat']", REAL_FOLLOW_ORIGIN)),
        ASSETS,
    )
    assert coverage.verified and coverage.origin_governed == ["/wechat"]


def test_follow_origin_with_default_cache_off_is_also_just_reported() -> None:
    params = {"FollowOrigin": {"Switch": "on", "DefaultCache": "off"}, "NoCache": None, "CustomTime": None}
    cond = "${http.request.uri.path} in ['/api/foo']"
    coverage = edgeone.check_asset_coverage(edgeone.normalize_rules(_rule_with(cond, params)), ASSETS)
    assert coverage.verified and coverage.uncovered == []


def test_cli_surfaces_origin_governed_paths_before_reporting_clean(monkeypatch, capsys) -> None:
    """The one thing keeping an unchecked path visible is a line of CLI output.

    Asserting only CoverageReport.origin_governed would let a refactor delete that line
    while the suite stayed green -- and the unchecked paths would silently vanish from a
    run that still exits 0.
    """
    cond = "${http.request.host} in ['news.aiplanet.live'] and ${http.request.uri.path} in ['/wechat']"
    raw = _rule_with(cond, REAL_FOLLOW_ORIGIN)
    monkeypatch.setattr(edgeone, "load_config", lambda: edgeone.EdgeOneConfig("i", "k", "z"))
    monkeypatch.setattr(edgeone, "fetch_rules", lambda cfg: raw)
    monkeypatch.setattr(edgeone, "load_snapshot", lambda path=None: edgeone.normalize_rules(raw))
    code = cli._admin_edgeone(cli.build_parser().parse_args(["admin", "edgeone", "check"]))
    out = capsys.readouterr().out
    assert code == edgeone.EXIT_CLEAN
    assert "ORIGIN-GOVERNED: /wechat" in out
    assert "not checked here" in out, "a clean exit must still say what it did not check"
