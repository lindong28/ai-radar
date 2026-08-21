from __future__ import annotations

import gzip
import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MINIMUM_REQUEST_INTERVAL_SECONDS = 2.0
MAX_REQUESTS_PER_MINUTE = 30
AIHOT_API_SCHEMA_VERSION = 1
AIHOT_API_BY_VALUES = ("timeline", "published")
AIHOT_CAPTURE_QUERY_V1 = {
    "mode": "all",
    "category": None,
    "window": "7d",
    "q": None,
    "by": "timeline",
    "ordering": "timelineDesc",
}
AIHOT_OBSERVED_DEFAULT_QUERY_V1 = {
    "mode": "selected",
    "category": None,
    "window": "24h",
    "q": None,
    "by": "timeline",
    "ordering": "timelineDesc",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FORBIDDEN_PROVENANCE_KEYS = frozenset({"release", "deploy", "config", "database_snapshot_id"})
AIHOT_ITEM_V1_FIELDS = frozenset(
    {
        "id",
        "aihot_url",
        "original_url",
        "aihot_title",
        "original_title",
        "aihot_summary",
        "upstream_publisher_name",
        "published_at",
        "aihot_discovered_at",
        "aihot_category_slug",
        "tags",
        "aihot_score_0_to_100",
        "aihot_selected",
        "aihot_recommendation_reason",
        "api_record_projection_observation",
        "ssr_tags_observation",
    }
)
AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS = (
    "aihot_category_slug",
    "tags",
    "aihot_score_0_to_100",
    "aihot_summary",
    "aihot_selected",
    "aihot_recommendation_reason",
)
AIHOT_ITEM_V1_FIELD_COVERAGE_LEAF_KEYS = (
    "present_item_record_count",
    "non_null_item_record_count",
    "observed_item_record_count",
)
AIHOT_ITEM_V1_PAIRING_KEYS = (
    "item_record_count",
    "original_url_present_item_record_count",
    "original_title_key_present_item_record_count",
    "aihot_title_present_item_record_count",
)
AIHOT_ITEM_V1_PAIRING_STRATEGY_KEYS = ("primary", "assistance", "fallback")
AIHOT_ITEM_V1_PAIRING_STRATEGY = {
    "primary": "original_url",
    "assistance": "original_title",
    "fallback": "aihot_title",
}
AIHOT_RECONCILIATION_COUNT_KEYS = (
    "api_target_item_id_count",
    "normalized_item_record_count",
    "ssr_list_matched_target_item_id_count",
    "detail_fallback_target_item_id_count",
    "explicit_empty_tags_target_item_id_count",
    "missing_tag_observation_target_item_id_count",
    "non_equivalent_tag_observation_target_item_id_count",
    "api_ssr_identity_conflict_target_item_id_count",
)
AIHOT_VALIDATION_REPORT_V1_ARTIFACT_TYPE = "aihot_validation_report_v1"
AIHOT_VALIDATION_REPORT_V1_TOP_LEVEL_KEYS = (
    "artifact_type",
    "subject",
    "identity",
    "stability",
    "window_validation",
    "integrity",
    "result",
)
AIHOT_VALIDATION_REPORT_V1_STABILITY_KEYS = (
    "pass_count",
    "canonical_pass_index",
    "accepted_pass_index_pair",
    "formal_day_pair_equal",
    "formal_window_target_item_id_sha256_comparisons",
)
AIHOT_VALIDATION_REPORT_V1_SUBJECT_KEYS = ("artifact_type", "path", "sha256")
AIHOT_VALIDATION_REPORT_V1_IDENTITY_KEYS = (
    "source_base_url",
    "first_public_response_observed_at",
    "openapi_saved_public_response_body_sha256",
    "clean_tool_commit",
    "item_schema_path",
    "item_schema_sha256",
)
AIHOT_VALIDATION_REPORT_V1_WINDOW_VALIDATION_KEYS = (
    "window",
    "target_item_id_count",
    "target_item_id_sha256",
    "tag_reconciliation_counts",
    "pairing",
    "pairing_strategy",
    "field_coverage",
)
AIHOT_VALIDATION_REPORT_V1_INTEGRITY_KEYS = (
    "capture_manifest",
    "public_response_raw_replay",
    "api_pass_raw_replay",
    "item_schema",
    "window_manifest",
    "items_jsonl",
    "tag_reconciliation",
)
AIHOT_ITEM_V1_SCHEMA_SEMANTICS: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://schemas.ai-radar.invalid/aihot-item-v1.schema.json",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "aihot_url",
        "original_url",
        "aihot_title",
        "original_title",
        "aihot_summary",
        "upstream_publisher_name",
        "published_at",
        "aihot_discovered_at",
        "aihot_category_slug",
        "tags",
        "aihot_score_0_to_100",
        "aihot_selected",
        "aihot_recommendation_reason",
        "api_record_projection_observation",
        "ssr_tags_observation",
    ],
    "properties": {
        "id": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "aihot_url": {
            "type": "string",
            "format": "uri",
            "pattern": "^https?://[^/?#]+/items/[^?#]+$",
        },
        "original_url": {"type": "string", "format": "uri", "pattern": "^https?://"},
        "aihot_title": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "original_title": {
            "anyOf": [
                {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
                {"type": "null"},
            ]
        },
        "aihot_summary": {
            "anyOf": [
                {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
                {"type": "null"},
            ]
        },
        "upstream_publisher_name": {
            "type": "string",
            "minLength": 1,
            "pattern": ".*\\S.*",
        },
        "published_at": {
            "anyOf": [
                {"type": "string", "format": "date-time"},
                {"type": "null"},
            ]
        },
        "aihot_discovered_at": {"type": "string", "format": "date-time"},
        "aihot_category_slug": {
            "anyOf": [
                {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
                {"type": "null"},
            ]
        },
        "tags": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        },
        "aihot_score_0_to_100": {
            "anyOf": [
                {"type": "number", "minimum": 0, "maximum": 100},
                {"type": "null"},
            ]
        },
        "aihot_selected": {"type": "boolean"},
        "aihot_recommendation_reason": {
            "anyOf": [
                {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
                {"type": "null"},
            ]
        },
        "api_record_projection_observation": {"type": "string", "const": "complete"},
        "ssr_tags_observation": {"type": "string", "const": "observed"},
    },
}


def _closed_object_semantics(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _nullable_non_empty_string_semantics() -> dict[str, object]:
    return {
        "anyOf": [
            {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
            {"type": "null"},
        ]
    }


def _artifact_reference_semantics() -> dict[str, object]:
    return _closed_object_semantics(
        {
            "path": {"type": "string", "format": "relative-path"},
            "sha256": {"type": "string", "format": "sha256"},
        }
    )


_PUBLIC_RESPONSE_V1_SEMANTICS = _closed_object_semantics(
    {
        "surface": {"type": "string", "enum": ["rss", "openapi"]},
        "request_url": {"type": "string", "format": "http-uri"},
        "raw_path": {"type": "string", "format": "relative-path"},
        "compressed_raw_sha256": {"type": "string", "format": "sha256"},
        "response_body_sha256": {"type": "string", "format": "sha256"},
        "status": {"type": "integer", "minimum": 100, "maximum": 599},
        "content_type": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "date": {"type": "string", "format": "http-date"},
        "etag": _nullable_non_empty_string_semantics(),
        "cache_control": _nullable_non_empty_string_semantics(),
    }
)
_RAW_PAGE_V1_SEMANTICS = _closed_object_semantics(
    {
        "raw_path": {"type": "string", "format": "relative-path"},
        "compressed_raw_sha256": {"type": "string", "format": "sha256"},
        "response_body_sha256": {"type": "string", "format": "sha256"},
        "status": {"type": "integer", "minimum": 100, "maximum": 599},
        "content_type": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "date": {"type": "string", "format": "http-date"},
        "etag": _nullable_non_empty_string_semantics(),
        "cache_control": _nullable_non_empty_string_semantics(),
        "canonical_query": _closed_object_semantics(
            {
                "mode": {"type": "string", "const": "all"},
                "by": {"type": "string", "const": "timeline"},
                "window": {"type": "string", "const": "7d"},
                "limit": {"type": "integer", "const": 100},
                "cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            }
        ),
    }
)
_FORMAL_WINDOW_V1_SEMANTICS = _closed_object_semantics(
    {
        "start_inclusive": {"type": "string", "format": "date-time"},
        "end_exclusive": {"type": "string", "format": "date-time"},
        "target_item_id_count": {"type": "integer", "minimum": 0},
        "target_item_id_sha256": {"type": "string", "format": "sha256"},
    }
)
_CAPTURE_PASS_V1_SEMANTICS = _closed_object_semantics(
    {
        "reached_has_more_false": {"type": "boolean"},
        "formal_windows": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": _FORMAL_WINDOW_V1_SEMANTICS,
        },
        "raw_pages": {
            "type": "array",
            "minItems": 1,
            "items": _RAW_PAGE_V1_SEMANTICS,
        },
    }
)
AIHOT_CAPTURE_MANIFEST_V1_SEMANTICS: dict[str, object] = _closed_object_semantics(
    {
        "artifact_type": {"type": "string", "const": "aihot_capture_v1"},
        "capture_id": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "started_at": {"type": "string", "format": "date-time"},
        "finished_at": {"type": "string", "format": "date-time"},
        "source": _closed_object_semantics(
            {"base_url": {"type": "string", "format": "http-uri"}}
        ),
        "public_responses": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": _PUBLIC_RESPONSE_V1_SEMANTICS,
        },
        "user_agent": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "rate_policy": _closed_object_semantics(
            {
                "max_requests_per_minute": {"type": "integer", "const": 30},
                "minimum_interval_seconds": {"type": "number", "const": 2.0},
            }
        ),
        "tool": _closed_object_semantics(
            {
                "commit": {"type": "string", "format": "git-object"},
                "dirty": {"type": "boolean", "const": False},
            }
        ),
        "schema": _artifact_reference_semantics(),
        "canonical_pass_index": {"type": "integer", "minimum": 0, "maximum": 2},
        "passes": {
            "type": "array",
            "minItems": 2,
            "maxItems": 3,
            "items": _CAPTURE_PASS_V1_SEMANTICS,
        },
    }
)

_RECONCILIATION_COUNTS_V1_SEMANTICS = _closed_object_semantics(
    {key: {"type": "integer", "minimum": 0} for key in AIHOT_RECONCILIATION_COUNT_KEYS}
)
_SSR_TAG_OBSERVATION_RESPONSE_V1_SEMANTICS = _closed_object_semantics(
    {
        "request_url": {"type": "string", "format": "http-uri"},
        "response_raw_path": {"type": "string", "format": "relative-path"},
        "compressed_raw_sha256": {"type": "string", "format": "sha256"},
        "response_body_sha256": {"type": "string", "format": "sha256"},
        "status": {"type": "integer", "minimum": 100, "maximum": 599},
        "content_type": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
    }
)
_SSR_TAG_OBSERVATION_BINDING_V1_SEMANTICS = _closed_object_semantics(
    {
        "item_id": {"type": "string", "minLength": 1, "pattern": ".*\\S.*"},
        "response_raw_path": {"type": "string", "format": "relative-path"},
    }
)
AIHOT_WINDOW_MANIFEST_V1_SEMANTICS: dict[str, object] = _closed_object_semantics(
    {
        "artifact_type": {"type": "string", "const": "aihot_window_v1"},
        "window": _closed_object_semantics(
            {
                "start_inclusive": {"type": "string", "format": "date-time"},
                "end_exclusive": {"type": "string", "format": "date-time"},
                "time_basis": {"type": "string", "const": "aihot_timeline_v1"},
            }
        ),
        "capture": _artifact_reference_semantics(),
        "items": _artifact_reference_semantics(),
        "canonical_pass_target_item_id_sha256_projection": {
            "type": "string",
            "format": "sha256",
        },
        "tag_observation_responses": {
            "type": "array",
            "minItems": 1,
            "items": _SSR_TAG_OBSERVATION_RESPONSE_V1_SEMANTICS,
        },
        "tag_observation_bindings": {
            "type": "array",
            "minItems": 1,
            "items": _SSR_TAG_OBSERVATION_BINDING_V1_SEMANTICS,
        },
        "tag_reconciliation_counts": _RECONCILIATION_COUNTS_V1_SEMANTICS,
    }
)

_FIELD_COVERAGE_V1_SEMANTICS = _closed_object_semantics(
    {
        key: _closed_object_semantics(
            {
                leaf: {"type": "integer", "minimum": 0}
                for leaf in AIHOT_ITEM_V1_FIELD_COVERAGE_LEAF_KEYS
            }
        )
        for key in AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS
    }
)
_REPORT_WINDOW_DESCRIPTOR_V1_SEMANTICS = _closed_object_semantics(
    {
        "start_inclusive": {"type": "string", "format": "date-time"},
        "end_exclusive": {"type": "string", "format": "date-time"},
        "time_basis": {"type": "string", "const": "aihot_timeline_v1"},
    }
)
_REPORT_WINDOW_VALIDATION_V1_SEMANTICS = _closed_object_semantics(
    {
        "window": _REPORT_WINDOW_DESCRIPTOR_V1_SEMANTICS,
        "target_item_id_count": {"type": "integer", "minimum": 0},
        "target_item_id_sha256": {"type": "string", "format": "sha256"},
        "tag_reconciliation_counts": _RECONCILIATION_COUNTS_V1_SEMANTICS,
        "pairing": _closed_object_semantics(
            {
                key: {"type": "integer", "minimum": 0}
                for key in AIHOT_ITEM_V1_PAIRING_KEYS
            }
        ),
        "pairing_strategy": _closed_object_semantics(
            {
                key: {"type": "string", "const": value}
                for key, value in AIHOT_ITEM_V1_PAIRING_STRATEGY.items()
            }
        ),
        "field_coverage": _FIELD_COVERAGE_V1_SEMANTICS,
    }
)
_REPORT_INTEGRITY_STATE_V1_SEMANTICS = {
    "type": "string",
    "enum": ["pass", "not_applicable_for_capture_subject"],
}
AIHOT_VALIDATION_REPORT_V1_SEMANTICS: dict[str, object] = _closed_object_semantics(
    {
        "artifact_type": {
            "type": "string",
            "const": AIHOT_VALIDATION_REPORT_V1_ARTIFACT_TYPE,
        },
        "subject": _closed_object_semantics(
            {
                "artifact_type": {
                    "type": "string",
                    "enum": ["aihot_capture_v1", "aihot_window_v1"],
                },
                "path": {"type": "string", "format": "relative-path"},
                "sha256": {"type": "string", "format": "sha256"},
            }
        ),
        "identity": _closed_object_semantics(
            {
                "source_base_url": {"type": "string", "format": "http-uri"},
                "first_public_response_observed_at": {
                    "type": "string",
                    "format": "date-time",
                },
                "openapi_saved_public_response_body_sha256": {
                    "type": "string",
                    "format": "sha256",
                },
                "clean_tool_commit": {"type": "string", "format": "git-object"},
                "item_schema_path": {"type": "string", "format": "relative-path"},
                "item_schema_sha256": {"type": "string", "format": "sha256"},
            }
        ),
        "stability": _closed_object_semantics(
            {
                "pass_count": {"type": "integer", "minimum": 2, "maximum": 3},
                "canonical_pass_index": {"type": "integer", "minimum": 1, "maximum": 2},
                "accepted_pass_index_pair": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "integer", "minimum": 0, "maximum": 2},
                },
                "formal_day_pair_equal": {"type": "boolean", "const": True},
                "formal_window_target_item_id_sha256_comparisons": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": _closed_object_semantics(
                        {
                            "start_inclusive": {"type": "string", "format": "date-time"},
                            "end_exclusive": {"type": "string", "format": "date-time"},
                            "equal": {"type": "boolean", "const": True},
                        }
                    ),
                },
            }
        ),
        "window_validation": {
            "anyOf": [
                {"type": "null"},
                _REPORT_WINDOW_VALIDATION_V1_SEMANTICS,
            ]
        },
        "integrity": _closed_object_semantics(
            {
                key: _REPORT_INTEGRITY_STATE_V1_SEMANTICS
                for key in AIHOT_VALIDATION_REPORT_V1_INTEGRITY_KEYS
            }
        ),
        "result": {"type": "string", "const": "pass"},
    }
)


class DatasetContractError(ValueError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = dict(details or {})


def validate_field_coverage_keys(keys: Sequence[str]) -> None:
    if tuple(keys) != AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS:
        raise DatasetContractError(
            "report_contract_mismatch",
            "field_coverage keys do not match the frozen AIHOT item v1 report contract",
        )


def validate_pairing_keys(keys: Sequence[str]) -> None:
    if tuple(keys) != AIHOT_ITEM_V1_PAIRING_KEYS:
        raise DatasetContractError(
            "report_contract_mismatch",
            "pairing keys do not match the frozen AIHOT item v1 report contract",
        )


def _require_exact_report_keys(
    value: object,
    *,
    expected: Sequence[str],
    field_name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise DatasetContractError(
            "report_contract_mismatch",
            f"{field_name} keys do not match the frozen AIHOT validation report v1 contract",
        )
    return value


def validate_validation_report_v1_contract_shape(payload: Mapping[str, object]) -> None:
    try:
        _validate_json_schema_subset(
            payload,
            AIHOT_VALIDATION_REPORT_V1_SEMANTICS,
            field_path="report",
        )
    except ValueError as error:
        raise DatasetContractError(
            "report_contract_mismatch",
            "report machine semantics do not match the frozen AIHOT validation report v1 contract",
        ) from error
    report = _require_exact_report_keys(
        payload,
        expected=AIHOT_VALIDATION_REPORT_V1_TOP_LEVEL_KEYS,
        field_name="report",
    )
    subject = _require_exact_report_keys(
        report["subject"],
        expected=AIHOT_VALIDATION_REPORT_V1_SUBJECT_KEYS,
        field_name="report.subject",
    )
    _require_exact_report_keys(
        report["identity"],
        expected=AIHOT_VALIDATION_REPORT_V1_IDENTITY_KEYS,
        field_name="report.identity",
    )
    stability = _require_exact_report_keys(
        report["stability"],
        expected=AIHOT_VALIDATION_REPORT_V1_STABILITY_KEYS,
        field_name="report.stability",
    )
    comparisons = stability["formal_window_target_item_id_sha256_comparisons"]
    assert isinstance(comparisons, list)
    for index, comparison in enumerate(comparisons):
        _require_exact_report_keys(
            comparison,
            expected=("start_inclusive", "end_exclusive", "equal"),
            field_name=f"report.stability.comparisons[{index}]",
        )
    pair = stability["accepted_pass_index_pair"]
    canonical_pass_index = stability["canonical_pass_index"]
    pass_count = stability["pass_count"]
    if (
        not isinstance(pair, list)
        or not isinstance(canonical_pass_index, int)
        or not isinstance(pass_count, int)
        or pair != [canonical_pass_index - 1, canonical_pass_index]
        or canonical_pass_index >= pass_count
    ):
        raise DatasetContractError(
            "report_contract_mismatch",
            "report stability pair does not identify the canonical adjacent pass pair",
        )
    for comparison in comparisons:
        assert isinstance(comparison, Mapping)
        if _timestamp(str(comparison["end_exclusive"])) <= _timestamp(
            str(comparison["start_inclusive"])
        ):
            raise DatasetContractError(
                "report_contract_mismatch",
                "report stability comparison has invalid half-open bounds",
            )
    if comparisons[0]["start_inclusive"] == comparisons[1]["start_inclusive"]:
        raise DatasetContractError(
            "report_contract_mismatch",
            "report stability comparisons must name two distinct formal windows",
        )

    integrity = _require_exact_report_keys(
        report["integrity"],
        expected=AIHOT_VALIDATION_REPORT_V1_INTEGRITY_KEYS,
        field_name="report.integrity",
    )
    subject_artifact_type = subject["artifact_type"]
    if subject_artifact_type == "aihot_capture_v1":
        path = str(subject["path"])
        path_parts = PurePosixPath(path).parts
        expected_window_state = "not_applicable_for_capture_subject"
        if len(path_parts) != 3 or path_parts[0] != "captures" or path_parts[2] != "capture.json":
            raise DatasetContractError(
                "report_contract_mismatch",
                "capture report subject path is not captures/<capture-id>/capture.json",
            )
        if report["window_validation"] is not None:
            raise DatasetContractError(
                "report_contract_mismatch",
                "capture-subject report window_validation must be null",
            )
    else:
        path = str(subject["path"])
        path_parts = PurePosixPath(path).parts
        expected_window_state = "pass"
        if len(path_parts) != 3 or path_parts[0] != "windows" or path_parts[2] != "manifest.json":
            raise DatasetContractError(
                "report_contract_mismatch",
                "window report subject path is not windows/<window-id>/manifest.json",
            )
        window_validation = _require_exact_report_keys(
            report["window_validation"],
            expected=AIHOT_VALIDATION_REPORT_V1_WINDOW_VALIDATION_KEYS,
            field_name="report.window_validation",
        )
        _require_exact_report_keys(
            window_validation["tag_reconciliation_counts"],
            expected=AIHOT_RECONCILIATION_COUNT_KEYS,
            field_name="report.window_validation.tag_reconciliation_counts",
        )
        _require_exact_report_keys(
            window_validation["pairing"],
            expected=AIHOT_ITEM_V1_PAIRING_KEYS,
            field_name="report.window_validation.pairing",
        )
        _require_exact_report_keys(
            window_validation["pairing_strategy"],
            expected=AIHOT_ITEM_V1_PAIRING_STRATEGY_KEYS,
            field_name="report.window_validation.pairing_strategy",
        )
        field_coverage = _require_exact_report_keys(
            window_validation["field_coverage"],
            expected=AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS,
            field_name="report.window_validation.field_coverage",
        )
        for field_name, coverage in field_coverage.items():
            _require_exact_report_keys(
                coverage,
                expected=AIHOT_ITEM_V1_FIELD_COVERAGE_LEAF_KEYS,
                field_name=f"report.window_validation.field_coverage.{field_name}",
            )
        counts = window_validation["tag_reconciliation_counts"]
        pairing = window_validation["pairing"]
        target_count = window_validation["target_item_id_count"]
        assert isinstance(counts, Mapping)
        assert isinstance(pairing, Mapping)
        assert isinstance(target_count, int)
        gap_keys = AIHOT_RECONCILIATION_COUNT_KEYS[5:]
        if (
            counts["api_target_item_id_count"] != target_count
            or counts["normalized_item_record_count"] != target_count
            or counts["ssr_list_matched_target_item_id_count"]
            + counts["detail_fallback_target_item_id_count"]
            != target_count
            or counts["explicit_empty_tags_target_item_id_count"] > target_count
            or any(counts[key] != 0 for key in gap_keys)
            or any(pairing[key] != target_count for key in AIHOT_ITEM_V1_PAIRING_KEYS)
        ):
            raise DatasetContractError(
                "report_contract_mismatch",
                "report window counts do not reconcile to the target item total",
            )
        for field_name, coverage in field_coverage.items():
            assert isinstance(coverage, Mapping)
            present = coverage["present_item_record_count"]
            non_null = coverage["non_null_item_record_count"]
            observed = coverage["observed_item_record_count"]
            if (
                present != target_count
                or not isinstance(non_null, int)
                or not isinstance(observed, int)
                or non_null > present
                or observed > present
                or (field_name == "tags" and observed != target_count)
            ):
                raise DatasetContractError(
                    "report_contract_mismatch",
                    "report field coverage does not reconcile to the target item total",
                )
    if any(integrity[key] != "pass" for key in AIHOT_VALIDATION_REPORT_V1_INTEGRITY_KEYS[:4]) or any(
        integrity[key] != expected_window_state
        for key in AIHOT_VALIDATION_REPORT_V1_INTEGRITY_KEYS[4:]
    ):
        raise DatasetContractError(
            "report_contract_mismatch",
            "report integrity states do not match the subject artifact type",
        )


def load_validation_report_v1(value: bytes) -> dict[str, object]:
    report = load_canonical_json_object(
        value,
        artifact_name="AIHOT validation report v1",
    )
    validate_validation_report_v1_contract_shape(report)
    return report


_SCHEMA_ANNOTATION_KEYS = frozenset({"description", "title", "examples", "$comment"})


def _schema_semantics(value: object) -> object:
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, child in value.items():
            normalized_key = str(key)
            if normalized_key in _SCHEMA_ANNOTATION_KEYS:
                continue
            if normalized_key == "properties" and isinstance(child, Mapping):
                normalized[normalized_key] = {
                    str(property_name): _schema_semantics(property_schema)
                    for property_name, property_schema in child.items()
                }
            else:
                normalized[normalized_key] = _schema_semantics(child)
        required = normalized.get("required")
        if isinstance(required, list) and all(isinstance(field, str) for field in required):
            normalized["required"] = sorted(required)
        return normalized
    if isinstance(value, list):
        return [_schema_semantics(child) for child in value]
    return value


def validate_aihot_item_v1_schema(schema_bytes: bytes) -> None:
    try:
        schema = json.loads(schema_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatasetContractError(
            "item_schema_semantic_mismatch",
            "AIHOT item v1 schema is not valid UTF-8 JSON",
        ) from error
    if not isinstance(schema, Mapping):
        raise DatasetContractError(
            "item_schema_semantic_mismatch",
            "AIHOT item v1 schema root must be an object",
        )
    expected = _schema_semantics(AIHOT_ITEM_V1_SCHEMA_SEMANTICS)
    if _schema_semantics(schema) != expected:
        raise DatasetContractError(
            "item_schema_semantic_mismatch",
            "AIHOT item v1 machine semantics differ from the frozen authority; publish v2 instead",
        )


def _non_empty_string(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _nullable_non_empty_string(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, field_name=field_name)


def _http_url(value: str, *, field_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    return value


def _aihot_item_url(value: str, *, item_id: str, field_name: str = "aihot_url") -> str:
    _http_url(value, field_name=field_name)
    parsed = urlparse(value)
    if parsed.path != f"/items/{item_id}" or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must be the exact /items/{{id}} URL without query or fragment")
    return value


def _url_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or default_port


def _require_same_origin(value: str, base_url: str, *, field_name: str) -> None:
    if _url_origin(value) != _url_origin(base_url):
        raise DatasetContractError(
            "identity_anchor_mismatch",
            f"{field_name} must have the same scheme, host, and port as source.base_url",
        )


def _require_request_contract(
    value: str,
    base_url: str,
    *,
    field_name: str,
    expected_path: str,
    expected_query: Mapping[str, str],
) -> None:
    _require_same_origin(value, base_url, field_name=field_name)
    parsed = urlparse(value)
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise DatasetContractError(
            "request_contract_mismatch",
            f"{field_name} query is malformed",
        ) from error
    expected = {key: [expected_value] for key, expected_value in expected_query.items()}
    if parsed.path != expected_path or parsed.params or parsed.fragment or query != expected:
        raise DatasetContractError(
            "request_contract_mismatch",
            f"{field_name} path/query does not match its frozen public request contract",
        )


def _rfc3339(value: str, *, field_name: str) -> str:
    if "T" not in value:
        raise ValueError(f"{field_name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _timestamp(value: str) -> datetime:
    _rfc3339(value, field_name="timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _http_date(value: str, *, field_name: str = "HTTP Date") -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a valid HTTP date") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sha256(value: str, *, field_name: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AihotItemV1(_StrictModel):
    id: str
    aihot_url: str
    original_url: str
    aihot_title: str
    original_title: str | None
    aihot_summary: str | None
    upstream_publisher_name: str
    published_at: str | None
    aihot_discovered_at: str
    aihot_category_slug: str | None
    tags: list[str]
    aihot_score_0_to_100: float | None = Field(ge=0, le=100)
    aihot_selected: bool
    aihot_recommendation_reason: str | None
    api_record_projection_observation: Literal["complete"]
    ssr_tags_observation: Literal["observed"]

    @field_validator("id", "aihot_title", "upstream_publisher_name")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _non_empty_string(value, field_name=info.field_name)

    @field_validator(
        "original_title",
        "aihot_summary",
        "aihot_category_slug",
        "aihot_recommendation_reason",
    )
    @classmethod
    def validate_nullable_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _non_empty_string(value, field_name=info.field_name)

    @field_validator("aihot_url", "original_url")
    @classmethod
    def validate_urls(cls, value: str, info: Any) -> str:
        return _http_url(value, field_name=info.field_name)

    @field_validator("published_at", "aihot_discovered_at")
    @classmethod
    def validate_timestamps(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _rfc3339(value, field_name=info.field_name)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        if any(not tag.strip() for tag in value):
            raise ValueError("tags must contain only non-empty strings")
        if len(set(value)) != len(value):
            raise ValueError("tags must be unique")
        return value

    @model_validator(mode="after")
    def validate_aihot_identity_url(self) -> AihotItemV1:
        _aihot_item_url(self.aihot_url, item_id=self.id)
        return self


# Compatibility name retained for Phase 1 callers while v1's field authority is
# intentionally independent of whichever item model a later version introduces.
AihotItem = AihotItemV1


class UnreconciledApiItem(_StrictModel):
    id: str
    aihot_url: str
    original_url: str
    aihot_title: str
    original_title: str | None
    aihot_summary: str | None
    upstream_publisher_name: str
    published_at: str | None
    aihot_discovered_at: str
    aihot_category_slug: str | None
    aihot_score_0_to_100: float | None = Field(ge=0, le=100)
    aihot_selected: bool
    aihot_recommendation_reason: str | None

    @field_validator("id", "aihot_title", "upstream_publisher_name")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _non_empty_string(value, field_name=info.field_name)

    @field_validator(
        "original_title",
        "aihot_summary",
        "aihot_category_slug",
        "aihot_recommendation_reason",
    )
    @classmethod
    def validate_nullable_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _non_empty_string(value, field_name=info.field_name)

    @field_validator("aihot_url", "original_url")
    @classmethod
    def validate_urls(cls, value: str, info: Any) -> str:
        return _http_url(value, field_name=info.field_name)

    @field_validator("published_at", "aihot_discovered_at")
    @classmethod
    def validate_timestamps(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _rfc3339(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_aihot_identity_url(self) -> UnreconciledApiItem:
        _aihot_item_url(self.aihot_url, item_id=self.id)
        return self


class _ApiSource(_StrictModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _non_empty_string(value, field_name="source.name")


class _ApiLinks(_StrictModel):
    aihot: str
    original: str

    @field_validator("aihot", "original")
    @classmethod
    def validate_link(cls, value: str, info: Any) -> str:
        return _http_url(value, field_name=f"links.{info.field_name}")


class _ApiAttribution(_StrictModel):
    name: str
    url: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _non_empty_string(value, field_name="attribution.name")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _http_url(value, field_name="attribution.url")


class _ApiItem(_StrictModel):
    id: str
    title: str
    originalTitle: str
    summary: str
    source: _ApiSource
    links: _ApiLinks
    publishedAt: str
    discoveredAt: str
    category: str
    score: int = Field(ge=0, le=100)
    selected: bool
    reason: str | None
    attribution: _ApiAttribution

    @field_validator("id", "title", "originalTitle", "summary", "category")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _non_empty_string(value, field_name=info.field_name)

    @field_validator("reason")
    @classmethod
    def validate_nullable_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _non_empty_string(value, field_name=info.field_name)

    @field_validator("publishedAt", "discoveredAt")
    @classmethod
    def validate_timestamp(cls, value: str, info: Any) -> str:
        return _rfc3339(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_aihot_identity_url(self) -> _ApiItem:
        _aihot_item_url(self.links.aihot, item_id=self.id, field_name="links.aihot")
        return self


class _PageMetadata(_StrictModel):
    count: int
    hasMore: bool
    nextCursor: str | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> _PageMetadata:
        if self.hasMore and (self.nextCursor is None or not self.nextCursor):
            raise ValueError("a nonterminal page requires nextCursor")
        if not self.hasMore and self.nextCursor is not None:
            raise ValueError("a terminal page must have nextCursor=null")
        return self


class _ApiQuery(_StrictModel):
    mode: str
    category: str | None
    window: str
    q: str | None
    by: str
    ordering: str

    @field_validator("mode", "window", "ordering")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _non_empty_string(value, field_name=f"query.{info.field_name}")

    @field_validator("by")
    @classmethod
    def validate_by(cls, value: str) -> str:
        if value not in AIHOT_API_BY_VALUES:
            expected = "|".join(AIHOT_API_BY_VALUES)
            raise ValueError(f"query.by must be one of: {expected}")
        return value

    @field_validator("category", "q")
    @classmethod
    def validate_nullable_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _non_empty_string(value, field_name=f"query.{info.field_name}")


class _ApiPage(_StrictModel):
    schemaVersion: Literal[1]
    query: _ApiQuery
    items: list[_ApiItem]
    page: _PageMetadata


def parse_api_item(payload: Mapping[str, object]) -> UnreconciledApiItem:
    try:
        item = _ApiItem.model_validate(payload)
    except ValidationError as error:
        raise DatasetContractError("item_invalid", "AIHOT API item does not match the frozen v1 shape") from error
    return UnreconciledApiItem(
        id=item.id,
        aihot_url=item.links.aihot,
        original_url=item.links.original,
        aihot_title=item.title,
        original_title=item.originalTitle,
        aihot_summary=item.summary,
        upstream_publisher_name=item.source.name,
        published_at=item.publishedAt,
        aihot_discovered_at=item.discoveredAt,
        aihot_category_slug=item.category,
        aihot_score_0_to_100=item.score,
        aihot_selected=item.selected,
        aihot_recommendation_reason=item.reason,
    )


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_canonical_json_object(value: bytes, *, artifact_name: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
        canonical_value = canonical_json_bytes(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as error:
        raise DatasetContractError("manifest_invalid", f"{artifact_name} is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or canonical_value != value:
        raise DatasetContractError(
            "manifest_invalid",
            f"{artifact_name} must be one canonical JSON object",
        )
    return payload


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def deterministic_gzip(value: bytes) -> bytes:
    buffer = BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as compressed:
        compressed.write(value)
    return buffer.getvalue()


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class RequestStart:
    surface: str
    started_at: float


class GlobalRateLimiter:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_start: float | None = None
        self.request_starts: list[RequestStart] = []

    def acquire(self, surface: str, *, not_before: float = 0.0) -> float:
        target = not_before
        if self._last_start is not None:
            target = max(target, self._last_start + MINIMUM_REQUEST_INTERVAL_SECONDS)
        now = self._monotonic()
        if target > now:
            self._sleep(target - now)
        started_at = self._monotonic()
        self._last_start = started_at
        self.request_starts.append(RequestStart(surface=surface, started_at=started_at))
        return started_at


def validate_request_schedule(request_starts: Sequence[RequestStart]) -> None:
    for previous, current in zip(request_starts, request_starts[1:], strict=False):
        if current.started_at - previous.started_at < MINIMUM_REQUEST_INTERVAL_SECONDS - 1e-9:
            raise DatasetContractError(
                "rate_limit_violation",
                "adjacent request starts must be at least 2.0 seconds apart",
                details={"previous": previous.started_at, "current": current.started_at},
            )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _retry_delay(response: HttpResponse) -> float:
    value = _header(response.headers, "Retry-After")
    if value is None:
        raise DatasetContractError("retry_after_invalid", "429/503 response is missing Retry-After")
    if value.isdecimal():
        return float(value)
    response_date = _header(response.headers, "Date")
    if response_date is None:
        raise DatasetContractError("retry_after_invalid", "HTTP-date Retry-After requires a Date response header")
    try:
        return max(0.0, (_http_date(value, field_name="Retry-After") - _http_date(response_date)).total_seconds())
    except ValueError as error:
        raise DatasetContractError("retry_after_invalid", "Retry-After is neither seconds nor an HTTP date") from error


def _is_json_media_type(media_type: str) -> bool:
    if media_type == "application/json":
        return True
    top_level, separator, subtype = media_type.partition("/")
    structured_suffix = "+json"
    return bool(
        separator
        and top_level
        and subtype.endswith(structured_suffix)
        and len(subtype) > len(structured_suffix)
    )


def request_with_policy(
    send: Callable[[], HttpResponse],
    *,
    surface: str,
    limiter: GlobalRateLimiter,
    max_attempts: int = 3,
    accepted_media_types: Sequence[str] = ("application/json",),
) -> HttpResponse:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    not_before = 0.0
    for attempt in range(1, max_attempts + 1):
        started_at = limiter.acquire(surface, not_before=not_before)
        try:
            response = send()
        except Exception as error:
            raise DatasetContractError("transport_failed", "request transport failed") from error
        if 200 <= response.status < 300:
            content_type = _header(response.headers, "Content-Type")
            media_type = content_type.split(";", 1)[0].strip().lower() if content_type is not None else ""
            accepts_json_family = "application/*+json" in accepted_media_types and _is_json_media_type(media_type)
            if media_type not in accepted_media_types and not accepts_json_family:
                expected = ", ".join(accepted_media_types)
                raise DatasetContractError(
                    "content_type_invalid",
                    f"successful {surface} response must use one of: {expected}",
                )
            return response
        if response.status in {429, 503}:
            if attempt == max_attempts:
                raise DatasetContractError(
                    "retry_budget_exhausted",
                    f"HTTP {response.status} persisted through {max_attempts} attempts",
                    details={"status": response.status, "attempts": max_attempts},
                )
            not_before = started_at + _retry_delay(response)
            continue
        raise DatasetContractError(f"http_{response.status}", f"HTTP {response.status} is not a successful response")
    raise AssertionError("retry loop exhausted without returning or raising")


@dataclass(frozen=True)
class TraversedPage:
    request_cursor: str | None
    next_cursor: str | None
    has_more: bool
    response: HttpResponse


@dataclass(frozen=True)
class TraversalResult:
    items: tuple[UnreconciledApiItem, ...]
    pages: tuple[TraversedPage, ...]
    terminal: bool


def _parse_page_body(
    body: bytes,
    *,
    expected_query: Mapping[str, object] | None = None,
) -> _ApiPage:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatasetContractError("page_invalid", "API page body is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise DatasetContractError("page_invalid", "API page root must be an object")
    try:
        page = _ApiPage.model_validate(payload)
    except ValidationError as error:
        item_values = payload.get("items")
        if isinstance(item_values, list) and any(
            not isinstance(item, dict) or _item_is_invalid(item) for item in item_values
        ):
            raise DatasetContractError("item_invalid", "API page contains an invalid item") from error
        if isinstance(payload.get("page"), dict) and payload["page"].get("hasMore") is True and not payload["page"].get("nextCursor"):
            raise DatasetContractError("cursor_missing", "nonterminal API page is missing nextCursor") from error
        raise DatasetContractError("page_invalid", "API page does not match the frozen v1 envelope") from error
    frozen_query = AIHOT_CAPTURE_QUERY_V1 if expected_query is None else expected_query
    if page.query.model_dump(mode="json") != dict(frozen_query):
        raise DatasetContractError("page_invalid", "API page query does not match the requested frozen v1 query")
    return page


def _item_is_invalid(item: Mapping[str, object]) -> bool:
    try:
        _ApiItem.model_validate(item)
    except ValidationError:
        return True
    return False


def traverse_api_pages(
    fetch: Callable[[str | None], HttpResponse],
    *,
    limiter: GlobalRateLimiter,
    initial_seen_cursors: set[str] | None = None,
    max_attempts: int = 3,
) -> TraversalResult:
    cursor: str | None = None
    seen_cursors = set(initial_seen_cursors or ())
    items: list[UnreconciledApiItem] = []
    seen_ids: set[str] = set()
    pages: list[TraversedPage] = []
    while True:
        response = request_with_policy(
            lambda: fetch(cursor),
            surface="api",
            limiter=limiter,
            max_attempts=max_attempts,
        )
        page = _parse_page_body(response.body)
        for api_item in page.items:
            item = parse_api_item(api_item.model_dump())
            if item.id in seen_ids:
                raise DatasetContractError("item_duplicate", f"API traversal repeated item id {item.id!r}")
            seen_ids.add(item.id)
            items.append(item)
        next_cursor = page.page.nextCursor
        pages.append(
            TraversedPage(
                request_cursor=cursor,
                next_cursor=next_cursor,
                has_more=page.page.hasMore,
                response=response,
            )
        )
        if not page.page.hasMore:
            return TraversalResult(items=tuple(items), pages=tuple(pages), terminal=True)
        if next_cursor is None or not next_cursor:
            raise DatasetContractError("cursor_missing", "nonterminal API page is missing nextCursor")
        if next_cursor in seen_cursors:
            raise DatasetContractError("cursor_repeated", f"API traversal repeated cursor {next_cursor!r}")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def timeline_key(item: AihotItemV1 | UnreconciledApiItem) -> datetime:
    discovered = _timestamp(item.aihot_discovered_at)
    if item.published_at is not None:
        published = _timestamp(item.published_at)
        if discovered - published > timedelta(hours=72):
            return published
    return discovered


def filter_window[TimelineItemT: (AihotItemV1, UnreconciledApiItem)](
    items: Sequence[TimelineItemT], *, start: str, end: str
) -> list[TimelineItemT]:
    start_at = _timestamp(start)
    end_at = _timestamp(end)
    if end_at <= start_at:
        raise DatasetContractError("window_invalid", "window end must be later than its start")
    selected = [item for item in items if start_at <= timeline_key(item) < end_at]
    return sorted(selected, key=lambda item: (timeline_key(item), item.id))


def ensure_window_covered(
    *,
    start: str,
    end: str,
    first_response_date: str,
    last_response_date: str,
) -> None:
    start_at = _timestamp(start)
    end_at = _timestamp(end)
    first = _http_date(first_response_date)
    last = _http_date(last_response_date)
    coverage_start = last - timedelta(days=7)
    coverage_end = first
    if end_at <= start_at:
        raise DatasetContractError("window_invalid", "window end must be later than its start")
    if start_at < coverage_start or end_at > coverage_end:
        raise DatasetContractError(
            "window_out_of_coverage",
            "requested window is not fully contained in the pass common coverage",
            details={"coverage_start": coverage_start.isoformat(), "coverage_end": coverage_end.isoformat()},
        )


class TagObservation(_StrictModel):
    item_id: str
    channel: Literal["list", "detail"]
    tags: list[str]
    observed_aihot_url: str

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: str) -> str:
        return _non_empty_string(value, field_name="item_id")

    @field_validator("observed_aihot_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _http_url(value, field_name="observed_aihot_url")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        if any(not tag.strip() for tag in value) or len(set(value)) != len(value):
            raise ValueError("observed tags must be unique non-empty strings")
        return value


class _SsrTagHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, list[str]]] = []
        self._active_item: tuple[str, str, list[str]] | None = None
        self._tag_text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value for name, value in attrs}
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "timeline-item" in classes:
            if self._active_item is not None:
                raise DatasetContractError("ssr_parse_failed", "nested timeline items are invalid")
            item_id = attributes.get("data-aihot-id")
            aihot_url = attributes.get("data-aihot-url")
            if not item_id or not aihot_url:
                raise DatasetContractError("ssr_parse_failed", "timeline item is missing its public identity")
            self._active_item = (item_id, aihot_url, [])
        elif tag == "span" and "topic-tag" in classes and self._active_item is not None:
            if self._tag_text is not None:
                raise DatasetContractError("ssr_parse_failed", "nested topic tags are invalid")
            self._tag_text = []

    def handle_data(self, data: str) -> None:
        if self._tag_text is not None:
            self._tag_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._tag_text is not None:
            if self._active_item is None:
                raise DatasetContractError("ssr_parse_failed", "topic tag is outside a timeline item")
            tag_value = "".join(self._tag_text).strip()
            if not tag_value:
                raise DatasetContractError("ssr_parse_failed", "topic tag text must be non-empty")
            self._active_item[2].append(tag_value)
            self._tag_text = None
        elif tag == "article" and self._active_item is not None:
            if self._tag_text is not None:
                raise DatasetContractError("ssr_parse_failed", "timeline item ended inside a topic tag")
            self.items.append(self._active_item)
            self._active_item = None

    def close(self) -> None:
        super().close()
        if self._active_item is not None or self._tag_text is not None:
            raise DatasetContractError("ssr_parse_failed", "SSR HTML ended inside an observed item")


def _parse_ssr_tag_observations(body: bytes, *, channel: Literal["list", "detail"]) -> list[TagObservation]:
    try:
        markup = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DatasetContractError("ssr_parse_failed", "SSR body is not valid UTF-8") from error
    parser = _SsrTagHtmlParser()
    parser.feed(markup)
    parser.close()
    try:
        return [
            TagObservation(
                item_id=item_id,
                channel=channel,
                tags=tags,
                observed_aihot_url=aihot_url,
            )
            for item_id, aihot_url, tags in parser.items
        ]
    except ValidationError as error:
        raise DatasetContractError("ssr_parse_failed", "SSR tag observation is invalid") from error


class ReconciliationCounts(_StrictModel):
    api_target_item_id_count: int = Field(ge=0)
    normalized_item_record_count: int = Field(ge=0)
    ssr_list_matched_target_item_id_count: int = Field(ge=0)
    detail_fallback_target_item_id_count: int = Field(ge=0)
    explicit_empty_tags_target_item_id_count: int = Field(ge=0)
    missing_tag_observation_target_item_id_count: int = Field(ge=0)
    non_equivalent_tag_observation_target_item_id_count: int = Field(ge=0)
    api_ssr_identity_conflict_target_item_id_count: int = Field(ge=0)


@dataclass(frozen=True)
class TagReconciliation:
    items: tuple[AihotItemV1, ...]
    counts: ReconciliationCounts


def reconcile_tags(
    items: Sequence[UnreconciledApiItem],
    observations: Sequence[TagObservation],
) -> TagReconciliation:
    item_by_id = {item.id: item for item in items}
    if len(item_by_id) != len(items):
        raise DatasetContractError("item_duplicate", "API target ids must be unique")
    unknown_ids = sorted({observation.item_id for observation in observations} - set(item_by_id))
    if unknown_ids:
        raise DatasetContractError(
            "tag_observation_unknown",
            "SSR observation references an id outside the API target universe",
            details={"item_ids": unknown_ids},
        )
    by_id: defaultdict[str, list[TagObservation]] = defaultdict(list)
    for observation in observations:
        by_id[observation.item_id].append(observation)

    missing = 0
    duplicate = 0
    conflict = 0
    list_matches = 0
    detail_fallbacks = 0
    empty_tags = 0
    normalized: list[AihotItemV1] = []
    for item in items:
        candidates = by_id[item.id]
        if not candidates:
            missing += 1
            continue
        if any(candidate.observed_aihot_url != item.aihot_url for candidate in candidates):
            conflict += 1
            continue
        signatures = {(tuple(candidate.tags), candidate.observed_aihot_url) for candidate in candidates}
        if len(signatures) > 1:
            duplicate += 1
            continue
        selected = next((candidate for candidate in candidates if candidate.channel == "list"), candidates[0])
        if selected.channel == "list":
            list_matches += 1
        else:
            detail_fallbacks += 1
        if not selected.tags:
            empty_tags += 1
        normalized.append(
            AihotItemV1.model_validate(
                {
                    **item.model_dump(mode="json"),
                    "tags": list(selected.tags),
                    "api_record_projection_observation": "complete",
                    "ssr_tags_observation": "observed",
                }
            )
        )

    counts = ReconciliationCounts(
        api_target_item_id_count=len(items),
        normalized_item_record_count=len(normalized),
        ssr_list_matched_target_item_id_count=list_matches,
        detail_fallback_target_item_id_count=detail_fallbacks,
        explicit_empty_tags_target_item_id_count=empty_tags,
        missing_tag_observation_target_item_id_count=missing,
        non_equivalent_tag_observation_target_item_id_count=duplicate,
        api_ssr_identity_conflict_target_item_id_count=conflict,
    )
    if conflict:
        raise DatasetContractError("tag_identity_conflict", "API and SSR item identity conflict", details=counts.model_dump())
    if duplicate:
        raise DatasetContractError("tag_observation_duplicate", "item has non-equivalent SSR observations", details=counts.model_dump())
    if missing:
        raise DatasetContractError("tag_observation_missing", "item has no successful SSR tag observation", details=counts.model_dump())
    return TagReconciliation(items=tuple(normalized), counts=counts)


def _format_rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def formal_day_pair(first_response_date: str) -> tuple[tuple[str, str], tuple[str, str]]:
    observed = _http_date(first_response_date)
    today = observed.replace(hour=0, minute=0, second=0, microsecond=0)
    day_minus_two = today - timedelta(days=2)
    day_minus_one = today - timedelta(days=1)
    return (
        (_format_rfc3339(day_minus_two), _format_rfc3339(day_minus_one)),
        (_format_rfc3339(day_minus_one), _format_rfc3339(today)),
    )


@dataclass(frozen=True)
class PassObservation:
    index: int
    terminal: bool
    first_response_date: str
    last_response_date: str
    target_ids_by_window: tuple[frozenset[str], frozenset[str]]

    def __post_init__(self) -> None:
        _http_date(self.first_response_date)
        _http_date(self.last_response_date)
        if self.index < 0:
            raise ValueError("pass index must be non-negative")

    @property
    def formal_windows(self) -> tuple[tuple[str, str], tuple[str, str]]:
        return formal_day_pair(self.first_response_date)


@dataclass(frozen=True)
class StabilityDecision:
    canonical_pass_index: int
    accepted_pair: tuple[int, int]
    formal_day_pair_equal: bool = True
    target_hash_equal_by_window: tuple[bool, bool] = (True, True)


def select_canonical_pass(passes: Sequence[PassObservation]) -> StabilityDecision:
    if len(passes) not in {2, 3}:
        raise DatasetContractError("capture_unstable", "capture requires two or three complete passes")
    if [candidate.index for candidate in passes] != list(range(len(passes))):
        raise DatasetContractError("capture_unstable", "pass indices must be contiguous and zero-based")
    for previous, current in zip(passes, passes[1:], strict=False):
        formal_equal = previous.formal_windows == current.formal_windows
        target_equal = tuple(
            previous.target_ids_by_window[index] == current.target_ids_by_window[index]
            for index in range(2)
        )
        if previous.terminal and current.terminal and formal_equal and all(target_equal):
            return StabilityDecision(
                canonical_pass_index=current.index,
                accepted_pair=(previous.index, current.index),
                formal_day_pair_equal=True,
                target_hash_equal_by_window=(True, True),
            )
    raise DatasetContractError("capture_unstable", "no adjacent pass pair has stable formal windows and target ids")


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceIdentity(_ManifestModel):
    base_url: str

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        _http_url(value, field_name="source.base_url")
        parsed = urlparse(value)
        if parsed.path or parsed.params or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("source.base_url must be a canonical origin URL without path, credentials, query, or fragment")
        return value

class RatePolicy(_ManifestModel):
    max_requests_per_minute: int
    minimum_interval_seconds: float


class ToolReference(_ManifestModel):
    commit: str
    dirty: Literal[False]

    @field_validator("commit")
    @classmethod
    def validate_commit(cls, value: str) -> str:
        if not GIT_COMMIT_PATTERN.fullmatch(value):
            raise ValueError("tool.commit must be a 40- or 64-character lowercase Git object id")
        return value


class ArtifactReference(_ManifestModel):
    path: str
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_sha(cls, value: str) -> str:
        return _sha256(value, field_name="reference.sha256")


class CanonicalQuery(_ManifestModel):
    mode: Literal["all"]
    by: Literal["timeline"]
    window: Literal["7d"]
    limit: Literal[100]
    cursor: str | None


class RawPageReference(_ManifestModel):
    raw_path: str
    compressed_raw_sha256: str
    response_body_sha256: str
    status: int
    content_type: str
    date: str
    etag: str | None
    cache_control: str | None
    canonical_query: CanonicalQuery

    @field_validator("compressed_raw_sha256", "response_body_sha256")
    @classmethod
    def validate_hash(cls, value: str, info: Any) -> str:
        return _sha256(value, field_name=info.field_name)

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        _http_date(value)
        return value

    @field_validator("etag", "cache_control")
    @classmethod
    def validate_optional_header(cls, value: str | None, info: Any) -> str | None:
        return _nullable_non_empty_string(value, field_name=info.field_name)


class PublicResponseReference(_ManifestModel):
    surface: Literal["rss", "openapi"]
    request_url: str
    raw_path: str
    compressed_raw_sha256: str
    response_body_sha256: str
    status: int
    content_type: str
    date: str
    etag: str | None
    cache_control: str | None

    @field_validator("compressed_raw_sha256", "response_body_sha256")
    @classmethod
    def validate_hash(cls, value: str, info: Any) -> str:
        return _sha256(value, field_name=info.field_name)

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        _http_date(value)
        return value

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        return _non_empty_string(value, field_name="content_type")

    @field_validator("request_url")
    @classmethod
    def validate_request_url(cls, value: str) -> str:
        return _http_url(value, field_name="request_url")

    @field_validator("etag", "cache_control")
    @classmethod
    def validate_optional_header(cls, value: str | None, info: Any) -> str | None:
        return _nullable_non_empty_string(value, field_name=info.field_name)


class FormalWindowObservation(_ManifestModel):
    start_inclusive: str
    end_exclusive: str
    target_item_id_count: int = Field(ge=0)
    target_item_id_sha256: str

    @field_validator("start_inclusive", "end_exclusive")
    @classmethod
    def validate_timestamp(cls, value: str, info: Any) -> str:
        return _rfc3339(value, field_name=info.field_name)

    @field_validator("target_item_id_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _sha256(value, field_name="target_item_id_sha256")


class CapturePassManifest(_ManifestModel):
    reached_has_more_false: bool
    formal_windows: list[FormalWindowObservation]
    raw_pages: list[RawPageReference]

    @model_validator(mode="after")
    def validate_formal_window_count(self) -> CapturePassManifest:
        if len(self.formal_windows) != 2:
            raise ValueError("a pass must declare exactly two formal windows")
        return self


class CaptureManifest(_ManifestModel):
    artifact_type: Literal["aihot_capture_v1"]
    capture_id: str
    started_at: str
    finished_at: str
    source: SourceIdentity
    public_responses: list[PublicResponseReference]
    user_agent: str
    rate_policy: RatePolicy
    tool: ToolReference
    schema_ref: ArtifactReference = Field(alias="schema")
    canonical_pass_index: int = Field(ge=0, le=2)
    passes: list[CapturePassManifest]

    @field_validator("started_at", "finished_at")
    @classmethod
    def validate_timestamp(cls, value: str, info: Any) -> str:
        return _rfc3339(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_capture_shape(self) -> CaptureManifest:
        if len(self.passes) not in {2, 3}:
            raise ValueError("capture must contain exactly two or three ordered passes")
        if [response.surface for response in self.public_responses] != ["rss", "openapi"]:
            raise ValueError("public_responses must record RSS then OpenAPI exactly once")
        return self


class WindowDescriptor(_ManifestModel):
    start_inclusive: str
    end_exclusive: str
    time_basis: Literal["aihot_timeline_v1"]

    @field_validator("start_inclusive", "end_exclusive")
    @classmethod
    def validate_timestamp(cls, value: str, info: Any) -> str:
        return _rfc3339(value, field_name=info.field_name)


class SsrTagObservationResponse(_ManifestModel):
    request_url: str
    response_raw_path: str
    compressed_raw_sha256: str
    response_body_sha256: str
    status: int
    content_type: str

    @field_validator("request_url")
    @classmethod
    def validate_request_url(cls, value: str) -> str:
        return _http_url(value, field_name="request_url")

    @field_validator("compressed_raw_sha256", "response_body_sha256")
    @classmethod
    def validate_hash(cls, value: str, info: Any) -> str:
        return _sha256(value, field_name=info.field_name)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        return _non_empty_string(value, field_name="content_type")


class SsrTagObservationBinding(_ManifestModel):
    item_id: str
    response_raw_path: str

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: str) -> str:
        return _non_empty_string(value, field_name="item_id")


class WindowManifest(_ManifestModel):
    artifact_type: Literal["aihot_window_v1"]
    window: WindowDescriptor
    capture: ArtifactReference
    items: ArtifactReference
    canonical_pass_target_item_id_sha256_projection: str
    tag_observation_responses: list[SsrTagObservationResponse]
    tag_observation_bindings: list[SsrTagObservationBinding]
    tag_reconciliation_counts: ReconciliationCounts

    @field_validator("canonical_pass_target_item_id_sha256_projection")
    @classmethod
    def validate_target_hash(cls, value: str) -> str:
        return _sha256(
            value,
            field_name="canonical_pass_target_item_id_sha256_projection",
        )


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(child))
    elif isinstance(value, list | tuple):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _validate_relative_path(path: str, *, expected_prefix: str | None = None) -> None:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not path or ".." in candidate.parts or "." in candidate.parts:
        raise DatasetContractError("reference_invalid", f"artifact path is not a safe relative path: {path!r}")
    if expected_prefix is not None and not path.startswith(expected_prefix):
        raise DatasetContractError("reference_invalid", f"artifact path is outside {expected_prefix!r}: {path!r}")


def load_persisted_artifacts(root: str | Path, relative_paths: Sequence[str]) -> dict[str, bytes]:
    root_path = Path(root).resolve()
    loaded: dict[str, bytes] = {}
    resolved_targets: set[Path] = set()
    for relative_path in relative_paths:
        _validate_relative_path(relative_path)
        target = root_path.joinpath(*PurePosixPath(relative_path).parts).resolve()
        try:
            target.relative_to(root_path)
        except ValueError as error:
            raise DatasetContractError(
                "reference_invalid",
                f"artifact path resolves outside the persisted root: {relative_path!r}",
            ) from error
        if relative_path in loaded or target in resolved_targets:
            raise DatasetContractError(
                "artifact_path_collision",
                f"persisted artifact path is repeated or aliases another path: {relative_path!r}",
            )
        try:
            loaded[relative_path] = target.read_bytes()
        except OSError as error:
            raise DatasetContractError(
                "reference_missing",
                f"persisted artifact cannot be read: {relative_path}",
            ) from error
        resolved_targets.add(target)
    return loaded


def _window_root(start: str, end: str) -> str:
    def component(value: str) -> str:
        return _timestamp(value).astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")

    return f"windows/{component(start)}--{component(end)}"


def _target_digest(item_ids: Sequence[str]) -> str:
    return sha256_hex(canonical_json_bytes(sorted(item_ids)))


def _read_compressed_raw(
    *,
    path: str,
    expected_raw_sha256: str,
    expected_body_sha256: str,
    raw_files: Mapping[str, bytes],
) -> bytes:
    stored = raw_files.get(path)
    if stored is None:
        raise DatasetContractError("reference_missing", f"raw response is missing: {path}")
    if sha256_hex(stored) != expected_raw_sha256:
        raise DatasetContractError("raw_hash_mismatch", f"stored raw hash mismatch: {path}")
    if len(stored) < 10 or stored[4:8] != b"\x00\x00\x00\x00":
        raise DatasetContractError("raw_encoding_invalid", "gzip raw artifact must use mtime=0")
    try:
        body = gzip.decompress(stored)
    except (gzip.BadGzipFile, EOFError, OSError) as error:
        raise DatasetContractError("raw_encoding_invalid", f"invalid gzip raw response: {path}") from error
    if sha256_hex(body) != expected_body_sha256:
        raise DatasetContractError("raw_body_hash_mismatch", f"uncompressed body hash mismatch: {path}")
    return body


def _validate_public_surface_body(surface: Literal["rss", "openapi"], body: bytes) -> None:
    if surface == "rss":
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as error:
            raise DatasetContractError("public_surface_invalid", "RSS public response is not valid XML") from error
        root_name = root.tag.rsplit("}", 1)[-1] if isinstance(root.tag, str) else ""
        if root_name not in {"rss", "feed"}:
            raise DatasetContractError("public_surface_invalid", "RSS public response root must be rss or feed")
        return
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DatasetContractError("public_surface_invalid", "OpenAPI public response is not valid JSON") from error
    if not isinstance(document, Mapping) or not isinstance(document.get("openapi"), str) or not document["openapi"].strip():
        raise DatasetContractError(
            "public_surface_invalid",
            "OpenAPI public response must be an object with a non-empty openapi version",
        )


def _validate_public_surface_content_type(surface: Literal["rss", "openapi"], value: str) -> None:
    media_type = value.split(";", 1)[0].strip().lower()
    if surface == "rss":
        accepted = media_type in {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
    else:
        accepted = _is_json_media_type(media_type)
    if not accepted:
        raise DatasetContractError(
            "content_type_invalid",
            f"{surface} public response Content-Type does not match its surface",
        )


def _validate_manifest_prelude(payload: Mapping[str, object]) -> None:
    forbidden = sorted(FORBIDDEN_PROVENANCE_KEYS & _walk_keys(payload))
    if forbidden:
        raise DatasetContractError(
            "forbidden_provenance",
            "manifest contains internal provenance not observable on the public surface",
            details={"keys": forbidden},
        )
    source = payload.get("source")
    if not isinstance(source, Mapping) or "base_url" not in source:
        raise DatasetContractError("identity_anchor_missing", "capture source identity anchors are incomplete")


def validate_capture_manifest(
    payload: Mapping[str, object],
    *,
    manifest_path: str,
    raw_files: Mapping[str, bytes],
    schema_bytes: bytes,
) -> CaptureManifest:
    _validate_manifest_prelude(payload)
    try:
        _validate_json_schema_subset(
            payload,
            AIHOT_CAPTURE_MANIFEST_V1_SEMANTICS,
            field_path="capture",
        )
    except ValueError as error:
        raise DatasetContractError(
            "manifest_invalid",
            "capture manifest machine semantics differ from the frozen v1 authority",
        ) from error
    try:
        manifest = CaptureManifest.model_validate(payload)
    except ValidationError as error:
        raise DatasetContractError("manifest_invalid", "capture manifest does not match the v1 contract") from error
    if manifest_path != f"captures/{manifest.capture_id}/capture.json":
        raise DatasetContractError(
            "reference_invalid",
            "capture manifest path must be captures/<capture-id>/capture.json",
        )
    if manifest.rate_policy.max_requests_per_minute != MAX_REQUESTS_PER_MINUTE or manifest.rate_policy.minimum_interval_seconds != MINIMUM_REQUEST_INTERVAL_SECONDS:
        raise DatasetContractError("rate_policy_invalid", "capture rate policy must be 30 requests/minute with a 2.0 second interval")
    if _timestamp(manifest.finished_at) < _timestamp(manifest.started_at):
        raise DatasetContractError("manifest_invalid", "capture finished_at must not precede started_at")
    if manifest.schema_ref.path != "src/airadar/eval/schemas/aihot-item-v1.schema.json":
        raise DatasetContractError("reference_invalid", "capture schema path is not the frozen v1 authority")
    validate_aihot_item_v1_schema(schema_bytes)
    if manifest.schema_ref.sha256 != sha256_hex(schema_bytes):
        raise DatasetContractError("reference_hash_mismatch", "capture schema hash does not match the referenced bytes")
    if not manifest.user_agent.strip():
        raise DatasetContractError("identity_anchor_missing", "capture user_agent must be non-empty")

    public_bodies: dict[str, bytes] = {}
    previous_public_response_date: datetime | None = None
    for public_reference in manifest.public_responses:
        public_response_date = _http_date(public_reference.date)
        if (
            previous_public_response_date is not None
            and public_response_date < previous_public_response_date
        ):
            raise DatasetContractError(
                "manifest_invalid",
                "public response Date values must be nondecreasing in RSS-to-OpenAPI order",
            )
        previous_public_response_date = public_response_date
        expected_path = "/rss" if public_reference.surface == "rss" else "/openapi-v1.json"
        _require_request_contract(
            public_reference.request_url,
            manifest.source.base_url,
            field_name=f"public_responses[{public_reference.surface}].request_url",
            expected_path=expected_path,
            expected_query={},
        )
        _validate_relative_path(
            public_reference.raw_path,
            expected_prefix=f"captures/{manifest.capture_id}/raw/probes/",
        )
        if public_reference.status != 200:
            raise DatasetContractError(f"http_{public_reference.status}", "public identity response must be successful")
        _validate_public_surface_content_type(public_reference.surface, public_reference.content_type)
        public_bodies[public_reference.surface] = _read_compressed_raw(
            path=public_reference.raw_path,
            expected_raw_sha256=public_reference.compressed_raw_sha256,
            expected_body_sha256=public_reference.response_body_sha256,
            raw_files=raw_files,
        )
        _validate_public_surface_body(public_reference.surface, public_bodies[public_reference.surface])
    observations: list[PassObservation] = []
    for pass_index, capture_pass in enumerate(manifest.passes):
        if not capture_pass.raw_pages:
            raise DatasetContractError("terminal_missing", "each pass must reference at least one API page")
        expected_prefix = f"captures/{manifest.capture_id}/raw/api/pass-{pass_index:02d}/"
        items: list[UnreconciledApiItem] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        expected_cursor: str | None = None
        terminal = False
        previous_response_date: datetime | None = None
        for page_index, page_reference in enumerate(capture_pass.raw_pages):
            canonical_query = page_reference.canonical_query
            if canonical_query.cursor != expected_cursor:
                raise DatasetContractError("reference_invalid", "raw page cursor does not match traversal order")
            _validate_relative_path(page_reference.raw_path, expected_prefix=expected_prefix)
            body = _read_compressed_raw(
                path=page_reference.raw_path,
                expected_raw_sha256=page_reference.compressed_raw_sha256,
                expected_body_sha256=page_reference.response_body_sha256,
                raw_files=raw_files,
            )
            if page_reference.status != 200:
                raise DatasetContractError(f"http_{page_reference.status}", "successful capture manifest cannot reference a failed API page")
            if page_reference.content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise DatasetContractError("content_type_invalid", "raw API page must be application/json")
            response_date = _http_date(page_reference.date)
            if previous_response_date is not None and response_date < previous_response_date:
                raise DatasetContractError("manifest_invalid", "API response Date values must be nondecreasing within a pass")
            previous_response_date = response_date
            page = _parse_page_body(body)
            for api_item in page.items:
                item = parse_api_item(api_item.model_dump())
                _require_same_origin(item.aihot_url, manifest.source.base_url, field_name="canonical item aihot_url")
                if item.id in seen_ids:
                    raise DatasetContractError("item_duplicate", f"pass repeated item id {item.id!r}")
                seen_ids.add(item.id)
                items.append(item)
            terminal = not page.page.hasMore
            if terminal:
                if page_index != len(capture_pass.raw_pages) - 1:
                    raise DatasetContractError("terminal_invalid", "terminal API page must be the final referenced page")
                expected_cursor = None
            else:
                next_cursor = page.page.nextCursor
                if next_cursor is None:
                    raise DatasetContractError("cursor_missing", "nonterminal raw page is missing a cursor")
                if next_cursor in seen_cursors:
                    raise DatasetContractError("cursor_repeated", f"raw replay repeated cursor {next_cursor!r}")
                seen_cursors.add(next_cursor)
                expected_cursor = next_cursor
        if terminal != capture_pass.reached_has_more_false or not terminal:
            raise DatasetContractError("terminal_missing", "pass did not reach hasMore=false")
        first_response_date = capture_pass.raw_pages[0].date
        last_response_date = capture_pass.raw_pages[-1].date
        expected_windows = formal_day_pair(first_response_date)
        targets: list[frozenset[str]] = []
        for index, declared in enumerate(capture_pass.formal_windows):
            if (declared.start_inclusive, declared.end_exclusive) != expected_windows[index]:
                raise DatasetContractError("formal_window_mismatch", "stored formal windows do not match the pass Date anchor")
            ensure_window_covered(
                start=declared.start_inclusive,
                end=declared.end_exclusive,
                first_response_date=first_response_date,
                last_response_date=last_response_date,
            )
            target_ids = [item.id for item in filter_window(items, start=declared.start_inclusive, end=declared.end_exclusive)]
            if declared.target_item_id_count != len(target_ids) or declared.target_item_id_sha256 != _target_digest(target_ids):
                raise DatasetContractError("target_projection_mismatch", "stored target count/hash does not match canonical raw items")
            targets.append(frozenset(target_ids))
        observations.append(
            PassObservation(
                index=pass_index,
                terminal=True,
                first_response_date=first_response_date,
                last_response_date=last_response_date,
                target_ids_by_window=(targets[0], targets[1]),
            )
        )

    decision = select_canonical_pass(observations)
    if manifest.canonical_pass_index != decision.canonical_pass_index:
        raise DatasetContractError("capture_unstable", "declared canonical pass is not the first accepted stable pair")
    return manifest


def _validate_v1_field_set(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise DatasetContractError("item_schema_version_mismatch", "AIHOT item v1 record must be a JSON object")
    actual_fields = frozenset(str(key) for key in payload)
    if actual_fields != AIHOT_ITEM_V1_FIELDS:
        raise DatasetContractError(
            "item_schema_version_mismatch",
            "AIHOT item record fields do not match the frozen v1 authority",
            details={
                "missing": sorted(AIHOT_ITEM_V1_FIELDS - actual_fields),
                "extra": sorted(actual_fields - AIHOT_ITEM_V1_FIELDS),
            },
        )
    return payload


def serialize_items_jsonl(items: Sequence[AihotItemV1]) -> bytes:
    if any(not isinstance(item, AihotItemV1) for item in items):
        raise DatasetContractError(
            "item_unreconciled",
            "only SSR-reconciled AIHOT item v1 records may be serialized",
        )
    ordered = sorted(items, key=lambda item: (timeline_key(item), item.id))
    if not ordered:
        return b""
    records: list[bytes] = []
    for item in ordered:
        payload = item.model_dump(mode="json")
        _validate_v1_field_set(payload)
        try:
            validated = AihotItemV1.model_validate(payload)
        except ValidationError as error:
            raise DatasetContractError(
                "item_schema_version_mismatch",
                "AIHOT item record semantics do not match the frozen v1 authority",
            ) from error
        records.append(canonical_json_bytes(validated.model_dump(mode="json")) + b"\n")
    return b"".join(records)


def _json_schema_type_matches(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise ValueError(f"unsupported JSON Schema type: {expected_type}")


def _validate_json_schema_subset(value: object, schema: Mapping[str, object], *, field_path: str) -> None:
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        for branch in any_of:
            if not isinstance(branch, Mapping):
                continue
            try:
                _validate_json_schema_subset(value, branch, field_path=field_path)
            except ValueError:
                continue
            return
        raise ValueError(f"{field_path} does not satisfy any allowed schema branch")

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{field_path} does not equal its frozen constant")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ValueError(f"{field_path} is not in its frozen enum")
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _json_schema_type_matches(value, expected_type):
        raise ValueError(f"{field_path} does not have JSON Schema type {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not isinstance(required, list) or not isinstance(properties, Mapping):
            raise ValueError(f"{field_path} object schema is invalid")
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"{field_path} is missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{field_path} contains additional fields")
        for name, child in value.items():
            child_schema = properties.get(name)
            if isinstance(child_schema, Mapping):
                _validate_json_schema_subset(child, child_schema, field_path=f"{field_path}.{name}")
        return

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise ValueError(f"{field_path} contains fewer than minItems")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise ValueError(f"{field_path} contains more than maxItems")
        if schema.get("uniqueItems") is True:
            encoded_items = [canonical_json_bytes(item) for item in value]
            if len(encoded_items) != len(set(encoded_items)):
                raise ValueError(f"{field_path} contains duplicate values")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, child in enumerate(value):
                _validate_json_schema_subset(child, item_schema, field_path=f"{field_path}[{index}]")
        return

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise ValueError(f"{field_path} is shorter than minLength")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ValueError(f"{field_path} does not match its frozen pattern")
        value_format = schema.get("format")
        if value_format == "uri":
            parsed = urlparse(value)
            if not parsed.scheme:
                raise ValueError(f"{field_path} is not an absolute URI")
        elif value_format == "http-uri":
            _http_url(value, field_name=field_path)
        elif value_format == "date-time":
            _rfc3339(value, field_name=field_path)
        elif value_format == "http-date":
            _http_date(value, field_name=field_path)
        elif value_format == "sha256":
            _sha256(value, field_name=field_path)
        elif value_format == "git-object":
            if not GIT_COMMIT_PATTERN.fullmatch(value):
                raise ValueError(f"{field_path} is not a frozen Git object id")
        elif value_format == "relative-path":
            candidate = PurePosixPath(value)
            if candidate.is_absolute() or not value or ".." in candidate.parts or "." in candidate.parts:
                raise ValueError(f"{field_path} is not a safe relative path")
        return

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            raise ValueError(f"{field_path} is below its frozen minimum")
        if isinstance(maximum, int | float) and value > maximum:
            raise ValueError(f"{field_path} is above its frozen maximum")


def _validate_item_against_schema(payload: Mapping[str, object], schema_bytes: bytes) -> None:
    validate_aihot_item_v1_schema(schema_bytes)
    try:
        schema = json.loads(schema_bytes)
        if not isinstance(schema, Mapping):
            raise ValueError("item schema must be a JSON object")
        _validate_json_schema_subset(payload, schema, field_path="item")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise DatasetContractError(
            "window_integrity_failed",
            "items.jsonl row does not satisfy the referenced frozen JSON Schema",
        ) from error


def load_items_jsonl(value: bytes, *, schema_bytes: bytes | None = None) -> list[AihotItemV1]:
    if value and not value.endswith(b"\n"):
        raise DatasetContractError("window_integrity_failed", "items.jsonl must end in a single LF-delimited record")
    items: list[AihotItemV1] = []
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        if not raw_line:
            raise DatasetContractError("window_integrity_failed", f"items.jsonl line {line_number} is empty")
        try:
            payload = json.loads(raw_line)
            _validate_v1_field_set(payload)
            item = AihotItemV1.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
            raise DatasetContractError("window_integrity_failed", f"items.jsonl line {line_number} is invalid") from error
        if schema_bytes is not None:
            _validate_item_against_schema(payload, schema_bytes)
        items.append(item)
    if len({item.id for item in items}) != len(items):
        raise DatasetContractError("window_integrity_failed", "items.jsonl contains duplicate ids")
    if serialize_items_jsonl(items) != value:
        raise DatasetContractError("window_integrity_failed", "items.jsonl is not in deterministic timeline/id order")
    return items


_load_items_jsonl = load_items_jsonl


def _load_capture_pass_items(
    capture_pass: CapturePassManifest,
    *,
    raw_files: Mapping[str, bytes],
) -> list[UnreconciledApiItem]:
    items: list[UnreconciledApiItem] = []
    for page_reference in capture_pass.raw_pages:
        body = _read_compressed_raw(
            path=page_reference.raw_path,
            expected_raw_sha256=page_reference.compressed_raw_sha256,
            expected_body_sha256=page_reference.response_body_sha256,
            raw_files=raw_files,
        )
        page = _parse_page_body(body)
        items.extend(parse_api_item(api_item.model_dump()) for api_item in page.items)
    return items


def _load_ssr_tag_observations(
    window: WindowManifest,
    *,
    capture: CaptureManifest,
    raw_files: Mapping[str, bytes],
    target_item_ids: Sequence[str],
) -> list[TagObservation]:
    response_by_path: dict[str, SsrTagObservationResponse] = {}
    parsed_by_path: dict[str, tuple[Literal["list", "detail"], list[TagObservation]]] = {}
    for reference in window.tag_observation_responses:
        if reference.response_raw_path in response_by_path:
            raise DatasetContractError(
                "tag_observation_reference_invalid",
                "SSR response_raw_path must be unique",
            )
        response_by_path[reference.response_raw_path] = reference
        _require_same_origin(reference.request_url, capture.source.base_url, field_name="SSR request_url")
        _validate_relative_path(
            reference.response_raw_path,
            expected_prefix=f"captures/{capture.capture_id}/raw/ssr/",
        )
        if reference.status != 200:
            raise DatasetContractError(f"http_{reference.status}", "SSR observation response must be successful")
        if reference.content_type.split(";", 1)[0].strip().lower() != "text/html":
            raise DatasetContractError("content_type_invalid", "SSR observation response must be text/html")
        parsed_request_url = urlparse(reference.request_url)
        request_path = parsed_request_url.path
        if request_path == "/all":
            try:
                list_query = parse_qs(
                    parsed_request_url.query,
                    keep_blank_values=True,
                    strict_parsing=True,
                )
            except ValueError as error:
                raise DatasetContractError(
                    "reference_invalid",
                    "SSR list request URL query is malformed",
                ) from error
            page_values = list_query.get("page")
            page_value = page_values[0] if page_values and len(page_values) == 1 else ""
            if (
                parsed_request_url.params
                or parsed_request_url.fragment
                or set(list_query) != {"page"}
                or re.fullmatch(r"[1-9][0-9]*", page_value) is None
                or int(page_value) > 50
            ):
                raise DatasetContractError(
                    "reference_invalid",
                    "SSR list request URL must be exactly /all?page=<1..50>",
                )
            channel: Literal["list", "detail"] = "list"
        elif (
            parsed_request_url.path.startswith("/items/")
            and parsed_request_url.path.count("/") == 2
            and parsed_request_url.path.removeprefix("/items/")
            and not parsed_request_url.params
            and not parsed_request_url.query
            and not parsed_request_url.fragment
        ):
            channel = "detail"
        else:
            raise DatasetContractError(
                "reference_invalid",
                "SSR request URL must be /all or the referenced /items/<id> detail path",
            )
        body = _read_compressed_raw(
            path=reference.response_raw_path,
            expected_raw_sha256=reference.compressed_raw_sha256,
            expected_body_sha256=reference.response_body_sha256,
            raw_files=raw_files,
        )
        parsed = _parse_ssr_tag_observations(body, channel=channel)
        parsed_by_path[reference.response_raw_path] = (channel, parsed)

    target_id_set = set(target_item_ids)
    binding_item_ids = [binding.item_id for binding in window.tag_observation_bindings]
    if len(binding_item_ids) != len(set(binding_item_ids)) or set(binding_item_ids) != target_id_set:
        raise DatasetContractError(
            "tag_observation_reference_invalid",
            "SSR bindings must name every target item exactly once",
        )
    bound_paths = {binding.response_raw_path for binding in window.tag_observation_bindings}
    if bound_paths != set(response_by_path):
        raise DatasetContractError(
            "tag_observation_reference_invalid",
            "every SSR response must exist and be referenced by at least one binding",
        )

    observations: list[TagObservation] = []
    for binding in window.tag_observation_bindings:
        parsed_response = parsed_by_path.get(binding.response_raw_path)
        if parsed_response is None:
            raise DatasetContractError(
                "tag_observation_reference_invalid",
                "SSR binding references an unknown response_raw_path",
            )
        channel, parsed = parsed_response
        response = response_by_path[binding.response_raw_path]
        request_path = urlparse(response.request_url).path
        if channel == "detail" and request_path != f"/items/{binding.item_id}":
            raise DatasetContractError(
                "tag_observation_reference_invalid",
                "SSR detail response may bind only its request item id",
            )
        matching = [observation for observation in parsed if observation.item_id == binding.item_id]
        if not matching:
            raise DatasetContractError(
                "tag_observation_missing",
                "stored SSR raw does not contain the referenced API item id",
            )
        if len(matching) != 1:
            raise DatasetContractError(
                "tag_observation_duplicate",
                "stored SSR raw contains the referenced API item id more than once",
            )
        observations.append(matching[0])
    return observations


def validate_window_projection(
    payload: Mapping[str, object],
    *,
    manifest_path: str,
    capture_manifest: Mapping[str, object],
    files: Mapping[str, bytes],
    raw_files: Mapping[str, bytes],
) -> list[AihotItemV1]:
    """Validate a normalized window by replaying its immutable API and SSR raw bytes."""
    forbidden = sorted(FORBIDDEN_PROVENANCE_KEYS & _walk_keys(payload))
    if forbidden:
        raise DatasetContractError("forbidden_provenance", "window manifest contains forbidden internal provenance")
    try:
        _validate_json_schema_subset(
            payload,
            AIHOT_WINDOW_MANIFEST_V1_SEMANTICS,
            field_path="window_manifest",
        )
    except ValueError as error:
        raise DatasetContractError(
            "window_integrity_failed",
            "window manifest machine semantics differ from the frozen v1 authority",
        ) from error
    try:
        window = WindowManifest.model_validate(payload)
        capture = CaptureManifest.model_validate(capture_manifest)
    except ValidationError as error:
        raise DatasetContractError("window_integrity_failed", "window or capture manifest shape is invalid") from error
    expected_window_root = _window_root(window.window.start_inclusive, window.window.end_exclusive)
    if manifest_path != f"{expected_window_root}/manifest.json":
        raise DatasetContractError("reference_invalid", "window manifest path does not match its declared boundaries")
    _validate_relative_path(window.capture.path, expected_prefix=f"captures/{capture.capture_id}/")
    if window.capture.path != f"captures/{capture.capture_id}/capture.json":
        raise DatasetContractError("reference_invalid", "window capture reference must point to its capture.json")
    _validate_relative_path(capture.schema_ref.path)
    if capture.schema_ref.path != "src/airadar/eval/schemas/aihot-item-v1.schema.json":
        raise DatasetContractError("reference_invalid", "capture schema reference is not the frozen v1 authority")
    _validate_relative_path(window.items.path, expected_prefix="windows/")
    if window.items.path != f"{expected_window_root}/items.jsonl":
        raise DatasetContractError("reference_invalid", "window items path does not match its declared boundaries")
    for reference in (window.capture, capture.schema_ref, window.items):
        stored = files.get(reference.path)
        if stored is None or sha256_hex(stored) != reference.sha256:
            raise DatasetContractError("window_integrity_failed", f"referenced artifact is missing or changed: {reference.path}")
    if files[window.capture.path] != canonical_json_bytes(capture_manifest):
        raise DatasetContractError("window_integrity_failed", "capture reference bytes do not encode the supplied capture manifest")
    capture = validate_capture_manifest(
        capture_manifest,
        manifest_path=window.capture.path,
        raw_files=raw_files,
        schema_bytes=files[capture.schema_ref.path],
    )
    canonical_pass = capture.passes[capture.canonical_pass_index]
    canonical_items = _load_capture_pass_items(canonical_pass, raw_files=raw_files)
    api_targets = filter_window(
        canonical_items,
        start=window.window.start_inclusive,
        end=window.window.end_exclusive,
    )
    tag_observations = _load_ssr_tag_observations(
        window,
        capture=capture,
        raw_files=raw_files,
        target_item_ids=[item.id for item in api_targets],
    )
    reconciliation = reconcile_tags(api_targets, tag_observations)
    expected_items = list(reconciliation.items)
    items = load_items_jsonl(
        files[window.items.path],
        schema_bytes=files[capture.schema_ref.path],
    )
    selected = filter_window(items, start=window.window.start_inclusive, end=window.window.end_exclusive)
    if selected != items:
        raise DatasetContractError("window_integrity_failed", "items.jsonl includes an item outside the declared half-open window")
    if items != expected_items:
        raise DatasetContractError(
            "window_integrity_failed",
            "normalized records differ from canonical API fields or raw-derived SSR tags",
        )
    item_ids = [item.id for item in items]
    if (
        window.tag_reconciliation_counts != reconciliation.counts
        or window.canonical_pass_target_item_id_sha256_projection
        != _target_digest(item_ids)
    ):
        raise DatasetContractError(
            "window_integrity_failed",
            "window tag reconciliation counts or target hash do not match raw replay and reconciliation",
        )
    matching = [
        formal
        for formal in canonical_pass.formal_windows
        if formal.start_inclusive == window.window.start_inclusive and formal.end_exclusive == window.window.end_exclusive
    ]
    if (
        len(matching) != 1
        or matching[0].target_item_id_count != len(items)
        or matching[0].target_item_id_sha256
        != window.canonical_pass_target_item_id_sha256_projection
    ):
        raise DatasetContractError("window_integrity_failed", "window does not project the canonical pass target set")
    return items


def _validation_report_identity(
    capture: CaptureManifest,
) -> dict[str, object]:
    first_public_response = capture.public_responses[0]
    openapi_response = next(
        response for response in capture.public_responses if response.surface == "openapi"
    )
    return {
        "source_base_url": capture.source.base_url,
        "first_public_response_observed_at": _format_rfc3339(
            _http_date(first_public_response.date)
        ),
        "openapi_saved_public_response_body_sha256": openapi_response.response_body_sha256,
        "clean_tool_commit": capture.tool.commit,
        "item_schema_path": capture.schema_ref.path,
        "item_schema_sha256": capture.schema_ref.sha256,
    }


def _validation_report_stability(
    capture: CaptureManifest,
) -> dict[str, object]:
    canonical_index = capture.canonical_pass_index
    previous = capture.passes[canonical_index - 1]
    canonical = capture.passes[canonical_index]
    comparisons = [
        {
            "start_inclusive": current.start_inclusive,
            "end_exclusive": current.end_exclusive,
            "equal": (
                previous.formal_windows[index].start_inclusive
                == current.start_inclusive
                and previous.formal_windows[index].end_exclusive
                == current.end_exclusive
                and previous.formal_windows[index].target_item_id_sha256
                == current.target_item_id_sha256
            ),
        }
        for index, current in enumerate(canonical.formal_windows)
    ]
    return {
        "pass_count": len(capture.passes),
        "canonical_pass_index": canonical_index,
        "accepted_pass_index_pair": [canonical_index - 1, canonical_index],
        "formal_day_pair_equal": all(
            previous.formal_windows[index].start_inclusive == current.start_inclusive
            and previous.formal_windows[index].end_exclusive == current.end_exclusive
            for index, current in enumerate(canonical.formal_windows)
        ),
        "formal_window_target_item_id_sha256_comparisons": comparisons,
    }


def _validation_report_pairing(items: Sequence[AihotItemV1]) -> dict[str, int]:
    item_count = len(items)
    return {
        "item_record_count": item_count,
        "original_url_present_item_record_count": sum(
            bool(item.original_url) for item in items
        ),
        "original_title_key_present_item_record_count": item_count,
        "aihot_title_present_item_record_count": sum(
            bool(item.aihot_title) for item in items
        ),
    }


def _validation_report_field_coverage(
    items: Sequence[AihotItemV1],
) -> dict[str, dict[str, int]]:
    item_count = len(items)
    return {
        field_name: {
            "present_item_record_count": item_count,
            "non_null_item_record_count": sum(
                getattr(item, field_name) is not None for item in items
            ),
            "observed_item_record_count": item_count,
        }
        for field_name in AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS
    }


def _validation_report_integrity(*, window_subject: bool) -> dict[str, str]:
    window_state = "pass" if window_subject else "not_applicable_for_capture_subject"
    return {
        "capture_manifest": "pass",
        "public_response_raw_replay": "pass",
        "api_pass_raw_replay": "pass",
        "item_schema": "pass",
        "window_manifest": window_state,
        "items_jsonl": window_state,
        "tag_reconciliation": window_state,
    }


def _load_report_capture(
    capture_path: str,
    *,
    files: Mapping[str, bytes],
    raw_files: Mapping[str, bytes],
) -> tuple[dict[str, object], CaptureManifest]:
    capture_bytes = files.get(capture_path)
    if capture_bytes is None:
        raise DatasetContractError(
            "reference_missing",
            f"report capture subject is missing: {capture_path}",
        )
    capture_payload = load_canonical_json_object(
        capture_bytes,
        artifact_name="capture.json",
    )
    try:
        capture_shape = CaptureManifest.model_validate(capture_payload)
    except ValidationError as error:
        raise DatasetContractError(
            "manifest_invalid",
            "report capture subject does not match the v1 contract",
        ) from error
    schema_bytes = files.get(capture_shape.schema_ref.path)
    if schema_bytes is None:
        raise DatasetContractError(
            "reference_missing",
            "report capture schema is missing",
        )
    capture = validate_capture_manifest(
        capture_payload,
        manifest_path=capture_path,
        raw_files=raw_files,
        schema_bytes=schema_bytes,
    )
    return capture_payload, capture


def build_validation_report_v1(
    *,
    subject_path: str,
    files: Mapping[str, bytes],
    raw_files: Mapping[str, bytes],
) -> dict[str, object]:
    """Build a validation report from reopened persisted artifact bytes."""
    subject_bytes = files.get(subject_path)
    if subject_bytes is None:
        raise DatasetContractError(
            "reference_missing",
            f"validation report subject is missing: {subject_path}",
        )
    subject_payload = load_canonical_json_object(
        subject_bytes,
        artifact_name="validation report subject",
    )
    subject_artifact_type = subject_payload.get("artifact_type")
    window_validation: dict[str, object] | None
    if subject_artifact_type == "aihot_capture_v1":
        _capture_payload, capture = _load_report_capture(
            subject_path,
            files=files,
            raw_files=raw_files,
        )
        window_validation = None
        window_subject = False
    elif subject_artifact_type == "aihot_window_v1":
        try:
            window = WindowManifest.model_validate(subject_payload)
        except ValidationError as error:
            raise DatasetContractError(
                "window_integrity_failed",
                "report window subject does not match the v1 contract",
            ) from error
        capture_payload, capture = _load_report_capture(
            window.capture.path,
            files=files,
            raw_files=raw_files,
        )
        items = validate_window_projection(
            subject_payload,
            manifest_path=subject_path,
            capture_manifest=capture_payload,
            files=files,
            raw_files=raw_files,
        )
        item_ids = [item.id for item in items]
        window_validation = {
            "window": window.window.model_dump(mode="json"),
            "target_item_id_count": len(items),
            "target_item_id_sha256": _target_digest(item_ids),
            "tag_reconciliation_counts": window.tag_reconciliation_counts.model_dump(
                mode="json"
            ),
            "pairing": _validation_report_pairing(items),
            "pairing_strategy": dict(AIHOT_ITEM_V1_PAIRING_STRATEGY),
            "field_coverage": _validation_report_field_coverage(items),
        }
        window_subject = True
    else:
        raise DatasetContractError(
            "report_contract_mismatch",
            "validation report subject artifact_type is unsupported",
        )

    report: dict[str, object] = {
        "artifact_type": AIHOT_VALIDATION_REPORT_V1_ARTIFACT_TYPE,
        "subject": {
            "artifact_type": subject_artifact_type,
            "path": subject_path,
            "sha256": sha256_hex(subject_bytes),
        },
        "identity": _validation_report_identity(capture),
        "stability": _validation_report_stability(capture),
        "window_validation": window_validation,
        "integrity": _validation_report_integrity(window_subject=window_subject),
        "result": "pass",
    }
    validate_validation_report_v1_contract_shape(report)
    return report


def validate_grounded_validation_report_v1(
    payload: Mapping[str, object],
    *,
    subject_path: str,
    files: Mapping[str, bytes],
    raw_files: Mapping[str, bytes],
) -> dict[str, object]:
    validate_validation_report_v1_contract_shape(payload)
    expected = build_validation_report_v1(
        subject_path=subject_path,
        files=files,
        raw_files=raw_files,
    )
    if canonical_json_bytes(payload) != canonical_json_bytes(expected):
        raise DatasetContractError(
            "report_grounding_mismatch",
            "validation report differs from its replay-derived subject projection",
        )
    return dict(payload)


class NetworkTransport(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


def slice_offline(
    items: Sequence[AihotItemV1],
    *,
    start: str,
    end: str,
    network_transport: NetworkTransport | None = None,
) -> bytes:
    _ = network_transport
    return serialize_items_jsonl(filter_window(items, start=start, end=end))


@dataclass(frozen=True)
class GitCheckout:
    root: Path
    commit: str | None
    dirty: bool


class SubprocessGitProvider:
    def checkout(self, path: Path) -> GitCheckout:
        requested = path.resolve()
        root_result = subprocess.run(
            ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
        if root_result.returncode != 0 or not root_result.stdout.strip():
            raise DatasetContractError("git_checkout_invalid", "path is not inside a Git worktree")
        root = Path(root_result.stdout.strip()).resolve()
        head_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        commit = head_result.stdout.strip() if head_result.returncode == 0 else None
        status_result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
        )
        if status_result.returncode != 0:
            raise DatasetContractError("git_checkout_invalid", "Git worktree status could not be read")
        return GitCheckout(root=root, commit=commit, dirty=bool(status_result.stdout))


class CaptureHttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None,
        headers: Mapping[str, str],
    ) -> HttpResponse: ...


class HttpxTransport:
    def __init__(self, *, timeout_seconds: float, user_agent: str) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._user_agent = _non_empty_string(user_agent, field_name="user_agent")
        self._client = httpx.Client(
            trust_env=False,
            timeout=self._timeout_seconds,
            headers={"User-Agent": self._user_agent},
        )

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        response = self._client.get(url, params=params, headers=headers)
        return HttpResponse(
            status=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class CaptureResult:
    capture_path: str
    window_manifest_paths: tuple[str, str]
    request_starts: tuple[RequestStart, ...]


def _required_header(response: HttpResponse, name: str) -> str:
    value = _header(response.headers, name)
    if value is None or not value.strip():
        raise DatasetContractError("identity_anchor_missing", f"successful response is missing {name}")
    return value


def _optional_non_empty_header(response: HttpResponse, name: str) -> str | None:
    value = _header(response.headers, name)
    if value is None:
        return None
    if not value.strip():
        raise DatasetContractError("identity_anchor_missing", f"observed {name} header is blank")
    return value


def _write_staged(root: Path, relative_path: str, value: bytes) -> None:
    _validate_relative_path(relative_path)
    target = root.joinpath(*PurePosixPath(relative_path).parts)
    if target.exists() or target.is_symlink():
        raise DatasetContractError("target_exists", f"staged artifact already exists: {relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value)


def _remove_exact_tree(root: Path, *, allowed_parent: Path) -> None:
    if not root.exists() and not root.is_symlink():
        return
    resolved_parent = allowed_parent.resolve()
    try:
        root.resolve().relative_to(resolved_parent)
    except ValueError as error:
        raise DatasetContractError("cleanup_refused", "cleanup target is outside the allowed staging parent") from error
    if root.is_symlink() or root.is_file():
        root.unlink()
        return
    for child in root.iterdir():
        _remove_exact_tree(child, allowed_parent=allowed_parent)
    root.rmdir()


def _response_reference_payload(response: HttpResponse, *, raw_path: str) -> dict[str, object]:
    compressed = deterministic_gzip(response.body)
    return {
        "raw_path": raw_path,
        "compressed_raw_sha256": sha256_hex(compressed),
        "response_body_sha256": sha256_hex(response.body),
        "status": response.status,
        "content_type": _required_header(response, "Content-Type"),
        "date": _required_header(response, "Date"),
        "etag": _optional_non_empty_header(response, "ETag"),
        "cache_control": _optional_non_empty_header(response, "Cache-Control"),
    }


def _ssr_response_reference_payload(
    response: HttpResponse,
    *,
    request_url: str,
    raw_path: str,
) -> dict[str, object]:
    compressed = deterministic_gzip(response.body)
    return {
        "request_url": request_url,
        "response_raw_path": raw_path,
        "compressed_raw_sha256": sha256_hex(compressed),
        "response_body_sha256": sha256_hex(response.body),
        "status": response.status,
        "content_type": _required_header(response, "Content-Type"),
    }


class CaptureWriter:
    def __init__(
        self,
        *,
        tool_repo_root: str | Path,
        output_root: str | Path,
        base_url: str,
        user_agent: str,
        transport: CaptureHttpTransport,
        limiter: GlobalRateLimiter,
        now: Callable[[], datetime],
        capture_id: str,
        schema_bytes: bytes,
    ) -> None:
        self.tool_repo_root = Path(tool_repo_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.base_url = SourceIdentity(base_url=base_url).base_url.rstrip("/")
        self.user_agent = _non_empty_string(user_agent, field_name="user_agent")
        self.transport = transport
        self.limiter = limiter
        self.now = now
        self.capture_id = _non_empty_string(capture_id, field_name="capture_id")
        self.schema_bytes = schema_bytes

    def _send(
        self,
        *,
        url: str,
        params: Mapping[str, str | int | float | bool | None] | None,
        surface: str,
        accepted_media_types: Sequence[str],
    ) -> HttpResponse:
        return request_with_policy(
            lambda: self.transport.get(
                url,
                params=params,
                headers={"User-Agent": self.user_agent},
            ),
            surface=surface,
            limiter=self.limiter,
            accepted_media_types=accepted_media_types,
        )

    def _preflight(self) -> str:
        git_provider = SubprocessGitProvider()
        tool = git_provider.checkout(self.tool_repo_root)
        if tool.root.resolve() != self.tool_repo_root:
            raise DatasetContractError("tool_checkout_invalid", "tool_repo_root is not the Git worktree root")
        if tool.dirty:
            raise DatasetContractError("tool_checkout_dirty", "capture requires a clean tool checkout")
        if tool.commit is None or GIT_COMMIT_PATTERN.fullmatch(tool.commit) is None:
            raise DatasetContractError("tool_commit_invalid", "tool checkout HEAD is not a valid Git object id")
        output = git_provider.checkout(self.output_root)
        if output.root.resolve() != self.output_root:
            raise DatasetContractError("output_root_invalid", "capture output root must be the data-repo worktree root")
        capture_target = self.output_root / "captures" / self.capture_id
        staging_target = self.output_root / ".staging" / self.capture_id
        if capture_target.exists() or capture_target.is_symlink() or staging_target.exists() or staging_target.is_symlink():
            raise DatasetContractError("target_exists", "capture target or staging target already exists")
        return tool.commit

    def _store_raw(
        self,
        staging_root: Path,
        raw_files: dict[str, bytes],
        *,
        raw_path: str,
        body: bytes,
    ) -> bytes:
        compressed = deterministic_gzip(body)
        _write_staged(staging_root, raw_path, compressed)
        raw_files[raw_path] = compressed
        return compressed

    def _capture_public_responses(
        self,
        staging_root: Path,
        raw_files: dict[str, bytes],
    ) -> list[dict[str, object]]:
        definitions = (
            (
                "rss",
                f"{self.base_url}/rss",
                f"captures/{self.capture_id}/raw/probes/00-rss.xml.gz",
                ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml"),
            ),
            (
                "openapi",
                f"{self.base_url}/openapi-v1.json",
                f"captures/{self.capture_id}/raw/probes/01-openapi.json.gz",
                ("application/json", "application/*+json"),
            ),
        )
        references: list[dict[str, object]] = []
        for surface, request_url, raw_path, accepted_media_types in definitions:
            response = self._send(
                url=request_url,
                params=None,
                surface=surface,
                accepted_media_types=accepted_media_types,
            )
            compressed = self._store_raw(
                staging_root,
                raw_files,
                raw_path=raw_path,
                body=response.body,
            )
            references.append(
                {
                    "surface": surface,
                    "request_url": request_url,
                    "raw_path": raw_path,
                    "compressed_raw_sha256": sha256_hex(compressed),
                    "response_body_sha256": sha256_hex(response.body),
                    "status": response.status,
                    "content_type": _required_header(response, "Content-Type"),
                    "date": _required_header(response, "Date"),
                    "etag": _optional_non_empty_header(response, "ETag"),
                    "cache_control": _optional_non_empty_header(response, "Cache-Control"),
                }
            )
        return references

    def _capture_pass(
        self,
        pass_index: int,
        staging_root: Path,
        raw_files: dict[str, bytes],
    ) -> tuple[TraversalResult, dict[str, object], PassObservation]:
        api_url = f"{self.base_url}/api/v1/items"

        def fetch(cursor: str | None) -> HttpResponse:
            params: dict[str, str | int | float | bool | None] = {
                "mode": "all",
                "by": "timeline",
                "window": "7d",
                "limit": 100,
            }
            if cursor is not None:
                params["cursor"] = cursor
            return self.transport.get(
                api_url,
                params=params,
                headers={"User-Agent": self.user_agent},
            )

        traversal = traverse_api_pages(fetch, limiter=self.limiter)
        raw_pages: list[dict[str, object]] = []
        for page_index, page in enumerate(traversal.pages):
            raw_path = (
                f"captures/{self.capture_id}/raw/api/pass-{pass_index:02d}/"
                f"page-{page_index:03d}.json.gz"
            )
            compressed = self._store_raw(
                staging_root,
                raw_files,
                raw_path=raw_path,
                body=page.response.body,
            )
            raw_pages.append(
                {
                    "raw_path": raw_path,
                    "compressed_raw_sha256": sha256_hex(compressed),
                    "response_body_sha256": sha256_hex(page.response.body),
                    "status": page.response.status,
                    "content_type": _required_header(page.response, "Content-Type"),
                    "date": _required_header(page.response, "Date"),
                    "etag": _optional_non_empty_header(page.response, "ETag"),
                    "cache_control": _optional_non_empty_header(page.response, "Cache-Control"),
                    "canonical_query": {
                        "mode": "all",
                        "by": "timeline",
                        "window": "7d",
                        "limit": 100,
                        "cursor": page.request_cursor,
                    },
                }
            )
        first_date = str(raw_pages[0]["date"])
        last_date = str(raw_pages[-1]["date"])
        windows = formal_day_pair(first_date)
        target_sets: list[frozenset[str]] = []
        formal_windows: list[dict[str, object]] = []
        for start, end in windows:
            ensure_window_covered(
                start=start,
                end=end,
                first_response_date=first_date,
                last_response_date=last_date,
            )
            targets = filter_window(traversal.items, start=start, end=end)
            target_ids = [item.id for item in targets]
            target_sets.append(frozenset(target_ids))
            formal_windows.append(
                {
                    "start_inclusive": start,
                    "end_exclusive": end,
                    "target_item_id_count": len(target_ids),
                    "target_item_id_sha256": _target_digest(target_ids),
                }
            )
        pass_payload = {
            "reached_has_more_false": traversal.terminal,
            "formal_windows": formal_windows,
            "raw_pages": raw_pages,
        }
        observation = PassObservation(
            index=pass_index,
            terminal=traversal.terminal,
            first_response_date=first_date,
            last_response_date=last_date,
            target_ids_by_window=(target_sets[0], target_sets[1]),
        )
        return traversal, pass_payload, observation

    def _capture_ssr_observations(
        self,
        *,
        target_items: Sequence[UnreconciledApiItem],
        staging_root: Path,
        raw_files: dict[str, bytes],
    ) -> tuple[dict[str, TagObservation], dict[str, dict[str, object]]]:
        targets = {item.id: item for item in target_items}
        observations: dict[str, TagObservation] = {}
        reference_by_item: dict[str, dict[str, object]] = {}
        for page in range(1, 51):
            request_url = f"{self.base_url}/all?page={page}"
            response = self._send(
                url=f"{self.base_url}/all",
                params={"page": page},
                surface="ssr",
                accepted_media_types=("text/html",),
            )
            raw_path = f"captures/{self.capture_id}/raw/ssr/list-page-{page:03d}.html.gz"
            self._store_raw(staging_root, raw_files, raw_path=raw_path, body=response.body)
            reference = _ssr_response_reference_payload(
                response,
                request_url=request_url,
                raw_path=raw_path,
            )
            for observation in _parse_ssr_tag_observations(response.body, channel="list"):
                if observation.item_id not in targets:
                    continue
                if observation.item_id in observations:
                    raise DatasetContractError(
                        "tag_observation_duplicate",
                        f"SSR list pages repeated target item {observation.item_id!r}",
                    )
                observations[observation.item_id] = observation
                reference_by_item[observation.item_id] = reference

        for item in target_items:
            if item.id in observations:
                continue
            request_url = item.aihot_url
            response = self._send(
                url=request_url,
                params=None,
                surface="ssr",
                accepted_media_types=("text/html",),
            )
            raw_path = f"captures/{self.capture_id}/raw/ssr/detail-{sha256_hex(item.id.encode())[:16]}.html.gz"
            self._store_raw(staging_root, raw_files, raw_path=raw_path, body=response.body)
            parsed = [
                observation
                for observation in _parse_ssr_tag_observations(response.body, channel="detail")
                if observation.item_id == item.id
            ]
            if not parsed:
                raise DatasetContractError("tag_observation_missing", f"detail page did not contain target {item.id!r}")
            if len(parsed) != 1:
                raise DatasetContractError("tag_observation_duplicate", f"detail page repeated target {item.id!r}")
            observations[item.id] = parsed[0]
            reference_by_item[item.id] = _ssr_response_reference_payload(
                response,
                request_url=request_url,
                raw_path=raw_path,
            )
        return observations, reference_by_item

    def _window_payload(
        self,
        *,
        start: str,
        end: str,
        capture_path: str,
        capture_bytes: bytes,
        items: Sequence[UnreconciledApiItem],
        observations: Mapping[str, TagObservation],
        references: Mapping[str, dict[str, object]],
    ) -> tuple[dict[str, object], str, bytes]:
        ordered_observations = [observations[item.id] for item in items]
        reconciliation = reconcile_tags(items, ordered_observations)
        items_bytes = serialize_items_jsonl(reconciliation.items)
        window_root = _window_root(start, end)
        items_path = f"{window_root}/items.jsonl"
        seen_paths: set[str] = set()
        response_references: list[dict[str, object]] = []
        bindings: list[dict[str, object]] = []
        for item in items:
            reference = references[item.id]
            response_raw_path = str(reference["response_raw_path"])
            if response_raw_path not in seen_paths:
                response_references.append(reference)
                seen_paths.add(response_raw_path)
            bindings.append({"item_id": item.id, "response_raw_path": response_raw_path})
        payload: dict[str, object] = {
            "artifact_type": "aihot_window_v1",
            "window": {
                "start_inclusive": start,
                "end_exclusive": end,
                "time_basis": "aihot_timeline_v1",
            },
            "capture": {"path": capture_path, "sha256": sha256_hex(capture_bytes)},
            "items": {"path": items_path, "sha256": sha256_hex(items_bytes)},
            "canonical_pass_target_item_id_sha256_projection": _target_digest(
                [item.id for item in items]
            ),
            "tag_observation_responses": response_references,
            "tag_observation_bindings": bindings,
            "tag_reconciliation_counts": reconciliation.counts.model_dump(mode="json"),
        }
        return payload, items_path, items_bytes

    def capture(self, *, start: str, end: str) -> CaptureResult:
        tool_commit = self._preflight()
        _timestamp(start)
        _timestamp(end)
        staging_parent = self.output_root / ".staging"
        staging_root = staging_parent / self.capture_id
        staging_root.mkdir(parents=True)
        raw_files: dict[str, bytes] = {}
        try:
            started_at = _format_rfc3339(self.now().astimezone(UTC))
            public_responses = self._capture_public_responses(staging_root, raw_files)
            traversals: list[TraversalResult] = []
            pass_payloads: list[dict[str, object]] = []
            observations: list[PassObservation] = []
            decision: StabilityDecision | None = None
            for pass_index in range(3):
                traversal, pass_payload, observation = self._capture_pass(
                    pass_index,
                    staging_root,
                    raw_files,
                )
                traversals.append(traversal)
                pass_payloads.append(pass_payload)
                observations.append(observation)
                if len(observations) >= 2:
                    try:
                        decision = select_canonical_pass(observations)
                    except DatasetContractError as error:
                        if error.code != "capture_unstable" or len(observations) == 3:
                            raise
                    else:
                        break
            if decision is None:
                raise DatasetContractError("capture_unstable", "capture did not produce a stable adjacent pass pair")
            canonical = observations[decision.canonical_pass_index]
            formal_windows = canonical.formal_windows
            if (start, end) != (formal_windows[0][0], formal_windows[1][1]):
                raise DatasetContractError(
                    "window_invalid",
                    "capture start/end must span the canonical two complete UTC days",
                )
            canonical_items = list(traversals[decision.canonical_pass_index].items)
            target_items = [
                item
                for bounds in formal_windows
                for item in filter_window(canonical_items, start=bounds[0], end=bounds[1])
            ]
            ssr_observations, ssr_references = self._capture_ssr_observations(
                target_items=target_items,
                staging_root=staging_root,
                raw_files=raw_files,
            )
            capture_path = f"captures/{self.capture_id}/capture.json"
            capture_payload: dict[str, object] = {
                "artifact_type": "aihot_capture_v1",
                "capture_id": self.capture_id,
                "started_at": started_at,
                "finished_at": _format_rfc3339(self.now().astimezone(UTC)),
                "source": {"base_url": self.base_url},
                "public_responses": public_responses,
                "user_agent": self.user_agent,
                "rate_policy": {
                    "max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
                    "minimum_interval_seconds": MINIMUM_REQUEST_INTERVAL_SECONDS,
                },
                "tool": {"commit": tool_commit, "dirty": False},
                "schema": {
                    "path": "src/airadar/eval/schemas/aihot-item-v1.schema.json",
                    "sha256": sha256_hex(self.schema_bytes),
                },
                "canonical_pass_index": decision.canonical_pass_index,
                "passes": pass_payloads,
            }
            capture_bytes = canonical_json_bytes(capture_payload)
            _write_staged(staging_root, capture_path, capture_bytes)

            window_payloads: list[tuple[str, dict[str, object], str, bytes]] = []
            for window_start, window_end in formal_windows:
                window_items = filter_window(
                    canonical_items,
                    start=window_start,
                    end=window_end,
                )
                window_payload, items_path, items_bytes = self._window_payload(
                    start=window_start,
                    end=window_end,
                    capture_path=capture_path,
                    capture_bytes=capture_bytes,
                    items=window_items,
                    observations=ssr_observations,
                    references=ssr_references,
                )
                manifest_path = f"{_window_root(window_start, window_end)}/manifest.json"
                _write_staged(staging_root, items_path, items_bytes)
                _write_staged(staging_root, manifest_path, canonical_json_bytes(window_payload))
                window_payloads.append((manifest_path, window_payload, items_path, items_bytes))

            validate_capture_manifest(
                capture_payload,
                manifest_path=capture_path,
                raw_files=raw_files,
                schema_bytes=self.schema_bytes,
            )
            for manifest_path, window_payload, items_path, items_bytes in window_payloads:
                validate_window_projection(
                    window_payload,
                    manifest_path=manifest_path,
                    capture_manifest=capture_payload,
                    files={
                        capture_path: capture_bytes,
                        "src/airadar/eval/schemas/aihot-item-v1.schema.json": self.schema_bytes,
                        items_path: items_bytes,
                    },
                    raw_files=raw_files,
                )

            publish_roots = [
                f"captures/{self.capture_id}",
                *[str(PurePosixPath(path).parent) for path, *_rest in window_payloads],
            ]
            for relative_root in publish_roots:
                target = self.output_root.joinpath(*PurePosixPath(relative_root).parts)
                if target.exists() or target.is_symlink():
                    raise DatasetContractError("target_exists", f"refusing to overwrite {relative_root}")
            published_targets: list[Path] = []
            created_parents: list[Path] = []
            try:
                for relative_root in publish_roots:
                    source = staging_root.joinpath(*PurePosixPath(relative_root).parts)
                    target = self.output_root.joinpath(*PurePosixPath(relative_root).parts)
                    if not target.parent.exists():
                        target.parent.mkdir(parents=True)
                        created_parents.append(target.parent)
                    source.rename(target)
                    published_targets.append(target)
            except Exception:
                for target in reversed(published_targets):
                    _remove_exact_tree(target, allowed_parent=self.output_root)
                for parent in reversed(created_parents):
                    if parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
                raise
            _remove_exact_tree(staging_root, allowed_parent=staging_parent)
            return CaptureResult(
                capture_path=capture_path,
                window_manifest_paths=(window_payloads[0][0], window_payloads[1][0]),
                request_starts=tuple(self.limiter.request_starts),
            )
        except Exception:
            _remove_exact_tree(staging_root, allowed_parent=staging_parent)
            raise


def _persisted_artifact_maps(
    output_root: str | Path,
    *,
    schema_bytes: bytes | None = None,
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    root = Path(output_root).resolve()
    files: dict[str, bytes] = {}
    raw_files: dict[str, bytes] = {}
    for top_level in ("captures", "windows"):
        directory = root / top_level
        if not directory.is_dir():
            continue
        for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()):
            try:
                relative_path = path.resolve().relative_to(root).as_posix()
            except ValueError as error:
                raise DatasetContractError("reference_invalid", "persisted artifact escaped output root") from error
            _validate_relative_path(relative_path)
            value = path.read_bytes()
            if relative_path.endswith(".gz"):
                raw_files[relative_path] = value
            else:
                files[relative_path] = value
    schema = schema_bytes
    if schema is None:
        schema = (Path(__file__).resolve().parent / "schemas" / "aihot-item-v1.schema.json").read_bytes()
    files["src/airadar/eval/schemas/aihot-item-v1.schema.json"] = schema
    return files, raw_files


def validate_persisted_artifact(
    output_root: str | Path,
    subject_path: str,
    *,
    schema_bytes: bytes | None = None,
) -> dict[str, object]:
    _validate_relative_path(subject_path)
    files, raw_files = _persisted_artifact_maps(output_root, schema_bytes=schema_bytes)
    return build_validation_report_v1(
        subject_path=subject_path,
        files=files,
        raw_files=raw_files,
    )


def slice_persisted_capture(
    output_root: str | Path,
    *,
    capture_path: str,
    start: str,
    end: str,
    network_transport: NetworkTransport | None = None,
    schema_bytes: bytes | None = None,
) -> bytes:
    _ = network_transport
    root = Path(output_root).resolve()
    schema = schema_bytes
    if schema is None:
        schema = (Path(__file__).resolve().parent / "schemas" / "aihot-item-v1.schema.json").read_bytes()

    initial_capture_files = load_persisted_artifacts(root, [capture_path])
    initial_capture_payload = load_canonical_json_object(
        initial_capture_files[capture_path],
        artifact_name="capture.json",
    )
    try:
        initial_capture = CaptureManifest.model_validate(initial_capture_payload)
    except ValidationError as error:
        raise DatasetContractError(
            "manifest_invalid",
            "persisted capture does not match the v1 contract",
        ) from error
    capture_raw_paths = [
        *(reference.raw_path for reference in initial_capture.public_responses),
        *(page.raw_path for capture_pass in initial_capture.passes for page in capture_pass.raw_pages),
    ]
    initial_raw_files = load_persisted_artifacts(root, sorted(set(capture_raw_paths)))
    initial_files = {
        **initial_capture_files,
        "src/airadar/eval/schemas/aihot-item-v1.schema.json": schema,
    }
    capture_payload, capture = _load_report_capture(
        capture_path,
        files=initial_files,
        raw_files=initial_raw_files,
    )
    canonical_pass = capture.passes[capture.canonical_pass_index]
    ensure_window_covered(
        start=start,
        end=end,
        first_response_date=canonical_pass.raw_pages[0].date,
        last_response_date=canonical_pass.raw_pages[-1].date,
    )
    expected_manifest_paths = [
        f"{_window_root(window.start_inclusive, window.end_exclusive)}/manifest.json"
        for window in canonical_pass.formal_windows
    ]
    if len(expected_manifest_paths) != 2 or len(set(expected_manifest_paths)) != 2:
        raise DatasetContractError("window_missing", "capture must identify two distinct formal windows")
    manifest_files = load_persisted_artifacts(root, expected_manifest_paths)
    manifest_payloads: dict[str, dict[str, object]] = {}
    manifest_shapes: dict[str, WindowManifest] = {}
    referenced_paths: set[str] = set()
    for manifest_path in expected_manifest_paths:
        payload = load_canonical_json_object(
            manifest_files[manifest_path],
            artifact_name="window manifest",
        )
        try:
            window = WindowManifest.model_validate(payload)
        except ValidationError as error:
            raise DatasetContractError("window_integrity_failed", "persisted window manifest is invalid") from error
        manifest_payloads[manifest_path] = payload
        manifest_shapes[manifest_path] = window
        referenced_paths.add(window.items.path)
        referenced_paths.update(reference.response_raw_path for reference in window.tag_observation_responses)

    all_persisted_paths = sorted(
        {
            capture_path,
            *capture_raw_paths,
            *expected_manifest_paths,
            *referenced_paths,
        }
    )
    persisted = load_persisted_artifacts(root, all_persisted_paths)
    raw_path_set = set(capture_raw_paths) | {
        reference.response_raw_path
        for window in manifest_shapes.values()
        for reference in window.tag_observation_responses
    }
    raw_files = {path: persisted[path] for path in raw_path_set}
    files = {path: value for path, value in persisted.items() if path not in raw_path_set}
    files["src/airadar/eval/schemas/aihot-item-v1.schema.json"] = schema
    capture_payload, capture = _load_report_capture(
        capture_path,
        files=files,
        raw_files=raw_files,
    )
    validated_windows: list[tuple[WindowManifest, list[AihotItemV1]]] = []
    for manifest_path in expected_manifest_paths:
        payload = manifest_payloads[manifest_path]
        window = manifest_shapes[manifest_path]
        items = validate_window_projection(
            payload,
            manifest_path=manifest_path,
            capture_manifest=capture_payload,
            files=files,
            raw_files=raw_files,
        )
        validated_windows.append((window, items))
    ordered_windows = sorted(validated_windows, key=lambda candidate: candidate[0].window.start_inclusive)
    coverage_start = ordered_windows[0][0].window.start_inclusive
    coverage_end = ordered_windows[-1][0].window.end_exclusive
    if _timestamp(start) < _timestamp(coverage_start) or _timestamp(end) > _timestamp(coverage_end):
        raise DatasetContractError("window_out_of_coverage", "offline slice is outside persisted normalized coverage")
    records_by_id: dict[str, AihotItemV1] = {}
    for _window, items in ordered_windows:
        for item in items:
            if item.id in records_by_id:
                raise DatasetContractError("item_duplicate", "persisted formal windows repeat an item id")
            records_by_id[item.id] = item
    return slice_offline(list(records_by_id.values()), start=start, end=end)


def capture_dataset(
    *,
    start: str,
    end: str,
    output_root: str | Path = "benchmarks/aihot",
) -> CaptureResult:
    tool_root = Path(__file__).resolve().parents[3]
    observed_at = datetime.now(UTC)
    capture_id = f"aihot-{observed_at.strftime('%Y%m%dT%H%M%SZ')}"
    user_agent = "AI-Radar-AIHOT-Dataset-Capture/1.0"
    transport = HttpxTransport(timeout_seconds=30.0, user_agent=user_agent)
    writer = CaptureWriter(
        tool_repo_root=tool_root,
        output_root=output_root,
        base_url="https://aihot.virxact.com",
        user_agent=user_agent,
        transport=transport,
        limiter=GlobalRateLimiter(),
        now=lambda: datetime.now(UTC),
        capture_id=capture_id,
        schema_bytes=(Path(__file__).resolve().parent / "schemas" / "aihot-item-v1.schema.json").read_bytes(),
    )
    try:
        return writer.capture(start=start, end=end)
    finally:
        transport.close()
