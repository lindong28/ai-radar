from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import pytest
from pydantic import ValidationError

try:
    from airadar.eval import aihot_dataset as dataset_module
except ImportError:
    dataset_module = None


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "src/airadar/eval/schemas/aihot-item-v1.schema.json"
SCHEMA_RELATIVE_PATH = "src/airadar/eval/schemas/aihot-item-v1.schema.json"
CAPTURE_ID = "synthetic-capture-20260820t120000z"
CAPTURE_PATH = f"captures/{CAPTURE_ID}/capture.json"
WINDOW_ONE = ("2026-08-18T00:00:00Z", "2026-08-19T00:00:00Z")
WINDOW_TWO = ("2026-08-19T00:00:00Z", "2026-08-20T00:00:00Z")


def synthetic_window_root(bounds: tuple[str, str]) -> str:
    start, end = bounds
    return f"windows/{start.replace(':', '')}--{end.replace(':', '')}"


WINDOW_ROOT = synthetic_window_root(WINDOW_ONE)
WINDOW_MANIFEST_PATH = f"{WINDOW_ROOT}/manifest.json"


@pytest.fixture
def ds() -> Any:
    assert dataset_module is not None, "AIHOT dataset contract module is not implemented"
    return dataset_module


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds


class FailIfNetworkTransport:
    def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline operation attempted network access")


def synthetic_api_item(
    item_id: str = "fiction-item-alpha",
    *,
    published_at: str = "2026-08-18T07:00:00Z",
    discovered_at: str = "2026-08-18T08:00:00Z",
) -> dict[str, object]:
    return {
        "id": item_id,
        "title": f"Synthetic Teapot Bulletin {item_id}",
        "originalTitle": f"Invented Kettle Dispatch {item_id}",
        "summary": "Clearly fictional summary for deterministic contract testing.",
        "source": {"name": "Fictional Gazette"},
        "links": {
            "aihot": f"https://aihot.invalid/items/{item_id}",
            "original": f"https://origin.invalid/articles/{item_id}",
        },
        "publishedAt": published_at,
        "discoveredAt": discovered_at,
        "category": "invented-research",
        "score": 73,
        "selected": True,
        "reason": "Synthetic recommendation rationale.",
        "attribution": {
            "name": "Fictional Gazette",
            "url": "https://origin.invalid/publishers/fictional-gazette",
        },
    }


def synthetic_item_record(
    ds: Any,
    item_id: str = "fiction-item-alpha",
    *,
    tags: list[str] | None = None,
    published_at: str = "2026-08-18T07:00:00Z",
    discovered_at: str = "2026-08-18T08:00:00Z",
) -> Any:
    api_item = ds.parse_api_item(synthetic_api_item(item_id, published_at=published_at, discovered_at=discovered_at))
    observed_tags = [] if tags is None else tags
    return ds.reconcile_tags(
        [api_item],
        [
            ds.TagObservation(
                item_id=item_id,
                channel="list",
                tags=observed_tags,
                observed_aihot_url=f"https://aihot.invalid/items/{item_id}",
            )
        ],
    ).items[0]


def structural_detail_html(
    *,
    article_attributes: str = 'class="fictional-card" data-fictional-binding="fiction-item-alpha"',
    identity_urls: tuple[str, ...] = (
        "https://aihot.invalid/items/fiction-item-alpha",
        "https://aihot.invalid/items/fiction-item-alpha",
    ),
    tag_groups: tuple[tuple[str, ...], ...] = (("fictional-one", "fictional-two"),),
) -> bytes:
    identity_markup = "".join(
        f'<meta content="{identity_url}">' for identity_url in identity_urls
    )
    group_markup = "".join(
        "<div>"
        + "".join(
            '<a class="fictional-tag-link" '
            f'href="/topics/{tag_slug}">Fictional tag {tag_slug}</a>'
            for tag_slug in tag_group
        )
        + "</div>"
        for tag_group in tag_groups
    )
    return (
        f"<html><head>{identity_markup}</head><body>"
        f"<article {article_attributes}>{group_markup}</article>"
        "</body></html>"
    ).encode()


@pytest.mark.parametrize(
    "canonical_item_url",
    [
        "https://aihot.invalid/items/fiction-item-alpha",
        "/items/fiction-item-alpha",
    ],
    ids=("absolute-same-origin", "relative-same-origin"),
)
def test_structural_detail_parser_recovers_identity_and_tags_without_legacy_selectors(
    ds: Any, canonical_item_url: str
) -> None:
    observations = ds._parse_ssr_tag_observations(
        structural_detail_html(identity_urls=(canonical_item_url, canonical_item_url)),
        channel="detail",
        request_url="https://aihot.invalid/items/fiction-item-alpha",
    )

    assert [observation.model_dump(mode="json") for observation in observations] == [
        {
            "item_id": "fiction-item-alpha",
            "channel": "detail",
            "tags": ["Fictional tag fictional-one", "Fictional tag fictional-two"],
            "observed_aihot_url": "https://aihot.invalid/items/fiction-item-alpha",
        }
    ]


@pytest.mark.parametrize(
    "body",
    [
        structural_detail_html(
            article_attributes=(
                'class="fictional-card" data-fictional-binding="fiction-item-alpha" '
                'data-fictional-extra="fiction-item-alpha"'
            )
        ),
        structural_detail_html(
            article_attributes='data-fictional-binding="fiction-item-alpha"'
        ),
        structural_detail_html(
            identity_urls=(
                "https://aihot.invalid/items/fiction-item-alpha",
                "https://other.invalid/items/fiction-item-alpha",
            )
        ),
        structural_detail_html(
            identity_urls=("/items/fiction-item-beta", "/items/fiction-item-beta")
        ),
        structural_detail_html(
            identity_urls=(
                "/items/fiction-item-alpha?fictional=1",
                "/items/fiction-item-alpha?fictional=1",
            )
        ),
        structural_detail_html(
            identity_urls=(
                "/items/fiction-item-alpha#fictional",
                "/items/fiction-item-alpha#fictional",
            )
        ),
        structural_detail_html(
            tag_groups=(("fictional-one",), ("fictional-two",)),
        ),
        structural_detail_html(
            tag_groups=(("fictional-one", "../fictional-two?fictional=1"),),
        ),
        structural_detail_html().replace(
            b'href="/topics/fictional-one"',
            b'href="/topics/fictional-one" href="/topics/fictional-one"',
        ),
    ],
    ids=(
        "ambiguous-article-identity",
        "missing-article-class-token",
        "ambiguous-canonical-url",
        "non-item-canonical-path",
        "canonical-url-query",
        "canonical-url-fragment",
        "ambiguous-tag-parent",
        "mixed-tag-url-family",
        "duplicate-anchor-attribute",
    ),
)
def test_structural_detail_parser_fails_closed_on_ambiguous_shape(ds: Any, body: bytes) -> None:
    assert (
        error_code(
            ds,
            lambda: ds._parse_ssr_tag_observations(
                body,
                channel="detail",
                request_url="https://aihot.invalid/items/fiction-item-alpha",
            ),
        )
        == "ssr_parse_failed"
    )


def test_structural_detail_shape_is_not_accepted_for_list_channel(ds: Any) -> None:
    assert ds._parse_ssr_tag_observations(structural_detail_html(), channel="list") == []


def page_payload(
    items: list[dict[str, object]],
    *,
    has_more: bool,
    next_cursor: str | None,
    mode: str = "all",
    category: str | None = None,
    window: str = "7d",
    q: str | None = None,
    by: str = "timeline",
    ordering: str = "timelineDesc",
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "query": {
            "mode": mode,
            "category": category,
            "window": window,
            "q": q,
            "by": by,
            "ordering": ordering,
        },
        "items": items,
        "page": {"count": len(items), "hasMore": has_more, "nextCursor": next_cursor},
    }


def response(
    ds: Any,
    payload: dict[str, object],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> Any:
    merged_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Date": "Thu, 20 Aug 2026 12:00:00 GMT",
        "ETag": '"synthetic-etag"',
        "Cache-Control": "public, max-age=60",
    }
    merged_headers.update(headers or {})
    return ds.HttpResponse(
        status=status,
        headers=merged_headers,
        body=ds.canonical_json_bytes(payload),
    )


def error_code(ds: Any, call: Callable[[], object]) -> str:
    with pytest.raises(ds.DatasetContractError) as captured:
        call()
    return captured.value.code


def id_digest(ds: Any, item_ids: list[str]) -> str:
    return ds.sha256_hex(ds.canonical_json_bytes(sorted(item_ids)))


def pass_manifest(
    ds: Any,
    *,
    window_one_ids: list[str],
    window_two_ids: list[str],
    raw_pages: list[dict[str, object]],
    terminal: bool = True,
) -> dict[str, object]:
    return {
        "reached_has_more_false": terminal,
        "formal_windows": [
            {
                "start_inclusive": WINDOW_ONE[0],
                "end_exclusive": WINDOW_ONE[1],
                "target_item_id_count": len(window_one_ids),
                "target_item_id_sha256": id_digest(ds, window_one_ids),
            },
            {
                "start_inclusive": WINDOW_TWO[0],
                "end_exclusive": WINDOW_TWO[1],
                "target_item_id_count": len(window_two_ids),
                "target_item_id_sha256": id_digest(ds, window_two_ids),
            },
        ],
        "raw_pages": raw_pages,
    }


def raw_page_file(
    ds: Any,
    *,
    pass_index: int,
    page_index: int,
    payload: dict[str, object],
    canonical_cursor: str | None = None,
    date: str = "Thu, 20 Aug 2026 12:00:00 GMT",
) -> tuple[dict[str, object], str, bytes]:
    body = ds.canonical_json_bytes(payload)
    compressed = ds.deterministic_gzip(body)
    path = f"captures/{CAPTURE_ID}/raw/api/pass-{pass_index:02d}/page-{page_index:03d}.json.gz"
    page = {
        "raw_path": path,
        "compressed_raw_sha256": ds.sha256_hex(compressed),
        "response_body_sha256": ds.sha256_hex(body),
        "status": 200,
        "content_type": "application/json; charset=utf-8",
        "date": date,
        "etag": '"synthetic-etag"',
        "cache_control": "public, max-age=60",
        "canonical_query": {
            "mode": "all",
            "by": "timeline",
            "window": "7d",
            "limit": 100,
            "cursor": canonical_cursor,
        },
    }
    return page, path, compressed


def public_response_file(
    ds: Any,
    *,
    surface: str,
    body: bytes,
    sequence: int,
    date: str,
    content_type: str,
) -> tuple[dict[str, object], str, bytes]:
    compressed = ds.deterministic_gzip(body)
    suffix = "xml" if surface == "rss" else "json"
    path = f"captures/{CAPTURE_ID}/raw/probes/{sequence:02d}-{surface}.{suffix}.gz"
    canonical_url_provenance: dict[str, object] | None = None
    if surface == "rss":
        canonical_url_provenance = {
            "discovered_via_redirect_from_url": "https://aihot.invalid/rss",
            "discovered_via_redirect_status": 301,
        }
    reference = {
        "surface": surface,
        "request_url": f"https://aihot.invalid/{'feed.xml' if surface == 'rss' else 'openapi-v1.json'}",
        "canonical_url_provenance": canonical_url_provenance,
        "raw_path": path,
        "compressed_raw_sha256": ds.sha256_hex(compressed),
        "response_body_sha256": ds.sha256_hex(body),
        "status": 200,
        "content_type": content_type,
        "date": date,
        "etag": None,
        "cache_control": "public, max-age=60",
    }
    return reference, path, compressed


def replace_public_response_body(
    ds: Any,
    manifest: dict[str, object],
    raw_files: dict[str, bytes],
    *,
    surface: str,
    body: bytes,
) -> None:
    references = manifest["public_responses"]
    assert isinstance(references, list)
    reference = next(candidate for candidate in references if candidate["surface"] == surface)
    path = reference["raw_path"]
    compressed = ds.deterministic_gzip(body)
    raw_files[path] = compressed
    reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
    reference["response_body_sha256"] = ds.sha256_hex(body)


def synthetic_capture_bundle(ds: Any) -> tuple[dict[str, object], dict[str, bytes]]:
    item_one = synthetic_api_item()
    item_two = synthetic_api_item(
        "fiction-item-beta",
        published_at="2026-08-19T07:00:00Z",
        discovered_at="2026-08-19T08:00:00Z",
    )
    raw_files: dict[str, bytes] = {}
    rss_reference, rss_path, rss_raw = public_response_file(
        ds,
        surface="rss",
        body=b"<rss><channel><title>Synthetic Feed Shape</title></channel></rss>",
        sequence=0,
        date="Thu, 20 Aug 2026 12:00:00 GMT",
        content_type="application/rss+xml; charset=utf-8",
    )
    openapi_body = ds.canonical_json_bytes({"openapi": "3.1.0", "info": {"title": "Synthetic API Shape"}})
    openapi_reference, openapi_path, openapi_raw = public_response_file(
        ds,
        surface="openapi",
        body=openapi_body,
        sequence=1,
        date="Thu, 20 Aug 2026 12:00:02 GMT",
        content_type="application/json; charset=utf-8",
    )
    raw_files.update({rss_path: rss_raw, openapi_path: openapi_raw})
    passes: list[dict[str, object]] = []
    for pass_index in (0, 1):
        page, path, compressed = raw_page_file(
            ds,
            pass_index=pass_index,
            page_index=0,
            payload=page_payload([item_one, item_two], has_more=False, next_cursor=None),
        )
        raw_files[path] = compressed
        passes.append(
            pass_manifest(
                ds,
                window_one_ids=["fiction-item-alpha"],
                window_two_ids=["fiction-item-beta"],
                raw_pages=[page],
            )
        )
    schema_bytes = SCHEMA_PATH.read_bytes()
    manifest: dict[str, object] = {
        "artifact_type": "aihot_capture_v1",
        "capture_id": CAPTURE_ID,
        "started_at": "2026-08-20T12:00:00Z",
        "finished_at": "2026-08-20T12:05:00Z",
        "source": {"base_url": "https://aihot.invalid"},
        "public_responses": [rss_reference, openapi_reference],
        "user_agent": "AI-Radar-Synthetic-Contract-Test/1.0",
        "rate_policy": {"max_requests_per_minute": 30, "minimum_interval_seconds": 2.0},
        "tool": {"commit": "b" * 40, "dirty": False},
        "schema": {"path": SCHEMA_RELATIVE_PATH, "sha256": ds.sha256_hex(schema_bytes)},
        "canonical_pass_index": 1,
        "passes": passes,
    }
    return manifest, raw_files


def install_multipage_capture(
    ds: Any,
    manifest: dict[str, object],
    raw_files: dict[str, bytes],
) -> list[str]:
    item_ids = ["fiction-item-alpha", "fiction-item-beta", "fiction-item-gamma"]
    items = [synthetic_api_item(item_id) for item_id in item_ids]
    for pass_index in (0, 1):
        first, first_path, first_raw = raw_page_file(
            ds,
            pass_index=pass_index,
            page_index=0,
            payload=page_payload(items[:2], has_more=True, next_cursor="fiction-page-two"),
        )
        second, second_path, second_raw = raw_page_file(
            ds,
            pass_index=pass_index,
            page_index=1,
            payload=page_payload(items[2:], has_more=False, next_cursor=None),
            canonical_cursor="fiction-page-two",
        )
        raw_files.update({first_path: first_raw, second_path: second_raw})
        manifest["passes"][pass_index] = pass_manifest(  # type: ignore[index]
            ds,
            window_one_ids=item_ids,
            window_two_ids=[],
            raw_pages=[first, second],
        )
    return item_ids


def synthetic_window_bundle(
    ds: Any,
    capture_manifest: dict[str, object],
    *,
    item_id: str = "fiction-item-alpha",
    item_ids: list[str] | None = None,
    tags: list[str] | None = None,
    channel: str = "list",
    item_updates: dict[str, object] | None = None,
    window_bounds: tuple[str, str] = WINDOW_ONE,
) -> tuple[dict[str, object], dict[str, bytes], dict[str, bytes]]:
    observed_tags = ["fictional-observed-tag"] if tags is None else tags
    default_item_id = "fiction-item-beta" if window_bounds == WINDOW_TWO else item_id
    target_ids = [default_item_id] if item_ids is None else item_ids
    window_day = window_bounds[0][:10]
    items = [
        synthetic_item_record(
            ds,
            target_id,
            tags=observed_tags,
            published_at=f"{window_day}T07:00:00Z",
            discovered_at=f"{window_day}T08:00:00Z",
        ).model_copy(update=item_updates or {})
        for target_id in target_ids
    ]
    items_bytes = ds.serialize_items_jsonl(items)
    capture_bytes = ds.canonical_json_bytes(capture_manifest)
    schema_bytes = SCHEMA_PATH.read_bytes()
    window_root = synthetic_window_root(window_bounds)
    items_path = f"{window_root}/items.jsonl"
    tag_observation_responses: list[dict[str, object]] = []
    tag_observation_bindings: list[dict[str, object]] = []
    ssr_raw_files: dict[str, bytes] = {}
    for target_id in target_ids:
        tag_markup = "".join(f'<span class="topic-tag">{tag}</span>' for tag in observed_tags)
        ssr_body = (
            f'<html><body><article class="timeline-item" data-aihot-id="{target_id}" '
            f'data-aihot-url="https://aihot.invalid/items/{target_id}">{tag_markup}</article></body></html>'
        ).encode()
        ssr_raw = ds.deterministic_gzip(ssr_body)
        ssr_path = f"captures/{CAPTURE_ID}/raw/ssr/{target_id}.html.gz"
        request_url = (
            "https://aihot.invalid/all?page=1"
            if channel == "list"
            else f"https://aihot.invalid/items/{target_id}"
        )
        tag_observation_responses.append(
            {
                "request_url": request_url,
                "response_raw_path": ssr_path,
                "compressed_raw_sha256": ds.sha256_hex(ssr_raw),
                "response_body_sha256": ds.sha256_hex(ssr_body),
                "status": 200,
                "content_type": "text/html; charset=utf-8",
            }
        )
        tag_observation_bindings.append(
            {"item_id": target_id, "response_raw_path": ssr_path}
        )
        ssr_raw_files[ssr_path] = ssr_raw
    manifest = {
        "artifact_type": "aihot_window_v1",
        "window": {
            "start_inclusive": window_bounds[0],
            "end_exclusive": window_bounds[1],
            "time_basis": "aihot_timeline_v1",
        },
        "capture": {"path": CAPTURE_PATH, "sha256": ds.sha256_hex(capture_bytes)},
        "items": {"path": items_path, "sha256": ds.sha256_hex(items_bytes)},
        "canonical_pass_target_item_id_sha256_projection": id_digest(ds, target_ids),
        "tag_observation_responses": tag_observation_responses,
        "tag_observation_bindings": tag_observation_bindings,
        "tag_reconciliation_counts": {
            "api_target_item_id_count": len(target_ids),
            "normalized_item_record_count": len(target_ids),
            "ssr_list_matched_target_item_id_count": len(target_ids) * int(channel == "list"),
            "detail_fallback_target_item_id_count": len(target_ids) * int(channel == "detail"),
            "explicit_empty_tags_target_item_id_count": len(target_ids) * int(not observed_tags),
            "missing_tag_observation_target_item_id_count": 0,
            "non_equivalent_tag_observation_target_item_id_count": 0,
            "api_ssr_identity_conflict_target_item_id_count": 0,
        },
    }
    files = {
        CAPTURE_PATH: capture_bytes,
        SCHEMA_RELATIVE_PATH: schema_bytes,
        items_path: items_bytes,
    }
    return manifest, files, ssr_raw_files


def merged_artifacts(*collections: dict[str, bytes]) -> dict[str, bytes]:
    merged: dict[str, bytes] = {}
    for collection in collections:
        for relative_path, value in collection.items():
            if relative_path in merged:
                assert merged[relative_path] == value
            merged[relative_path] = value
    return merged


def materialize_artifacts(root: Path, artifacts: dict[str, bytes]) -> None:
    for relative_path, value in artifacts.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)


def install_mixed_shared_ssr_observations(
    ds: Any,
    window: dict[str, object],
    *,
    item_ids: list[str],
) -> dict[str, bytes]:
    list_ids = item_ids[:2]
    detail_id = item_ids[2]
    list_articles = "".join(
        f'<article class="timeline-item" data-aihot-id="{item_id}" '
        f'data-aihot-url="https://aihot.invalid/items/{item_id}">'
        '<span class="topic-tag">fictional-observed-tag</span></article>'
        for item_id in list_ids
    )
    list_body = f"<html><body>{list_articles}</body></html>".encode()
    list_raw = ds.deterministic_gzip(list_body)
    list_path = f"captures/{CAPTURE_ID}/raw/ssr/shared-list.html.gz"
    detail_body = (
        f'<html><body><article class="timeline-item" data-aihot-id="{detail_id}" '
        f'data-aihot-url="https://aihot.invalid/items/{detail_id}">'
        '<span class="topic-tag">fictional-observed-tag</span></article></body></html>'
    ).encode()
    detail_raw = ds.deterministic_gzip(detail_body)
    detail_path = f"captures/{CAPTURE_ID}/raw/ssr/{detail_id}.html.gz"
    shared_response = {
        "request_url": "https://aihot.invalid/all?page=1",
        "response_raw_path": list_path,
        "compressed_raw_sha256": ds.sha256_hex(list_raw),
        "response_body_sha256": ds.sha256_hex(list_body),
        "status": 200,
        "content_type": "text/html; charset=utf-8",
    }
    window["tag_observation_responses"] = [
        shared_response,
        {
            "request_url": f"https://aihot.invalid/items/{detail_id}",
            "response_raw_path": detail_path,
            "compressed_raw_sha256": ds.sha256_hex(detail_raw),
            "response_body_sha256": ds.sha256_hex(detail_body),
            "status": 200,
            "content_type": "text/html; charset=utf-8",
        }
    ]
    window["tag_observation_bindings"] = [
        {"item_id": item_id, "response_raw_path": list_path} for item_id in list_ids
    ] + [{"item_id": detail_id, "response_raw_path": detail_path}]
    window["tag_reconciliation_counts"]["ssr_list_matched_target_item_id_count"] = 2  # type: ignore[index]
    window["tag_reconciliation_counts"]["detail_fallback_target_item_id_count"] = 1  # type: ignore[index]
    return {list_path: list_raw, detail_path: detail_raw}


def replace_first_api_item_field(
    ds: Any,
    capture: dict[str, object],
    raw_files: dict[str, bytes],
    field_path: tuple[str, ...],
    value: object,
) -> None:
    for capture_pass in capture["passes"]:  # type: ignore[index]
        page_reference = capture_pass["raw_pages"][0]
        path = page_reference["raw_path"]
        payload = json.loads(gzip.decompress(raw_files[path]))
        target = payload["items"][0]
        for part in field_path[:-1]:
            target = target[part]
        target[field_path[-1]] = value
        encoded = ds.canonical_json_bytes(payload)
        compressed = ds.deterministic_gzip(encoded)
        raw_files[path] = compressed
        page_reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
        page_reference["response_body_sha256"] = ds.sha256_hex(encoded)


def replace_first_raw_api_page_envelope(
    ds: Any,
    capture: dict[str, object],
    raw_files: dict[str, bytes],
    *,
    mutation: str,
) -> None:
    page_reference = capture["passes"][0]["raw_pages"][0]  # type: ignore[index]
    path = page_reference["raw_path"]
    payload = json.loads(gzip.decompress(raw_files[path]))
    if mutation == "legacy_items_page_only":
        payload = {"items": payload["items"], "page": payload["page"]}
    elif mutation == "wrong_schema_version":
        payload["schemaVersion"] = 2
    else:
        payload["query"]["mode"] = "selected"
    encoded = ds.canonical_json_bytes(payload)
    compressed = ds.deterministic_gzip(encoded)
    raw_files[path] = compressed
    page_reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
    page_reference["response_body_sha256"] = ds.sha256_hex(encoded)


def mutate_first_persisted_raw_api_page(
    ds: Any,
    capture: dict[str, object],
    raw_files: dict[str, bytes],
    *,
    mutation: str,
) -> None:
    page_reference = capture["passes"][0]["raw_pages"][0]  # type: ignore[index]
    path = page_reference["raw_path"]
    payload = json.loads(gzip.decompress(raw_files[path]))
    if mutation == "root_extra":
        payload["unknownRoot"] = "forbidden"
    elif mutation == "query_missing":
        del payload["query"]["q"]
    elif mutation == "item_wrong_type":
        payload["items"][0]["originalTitle"] = None
    elif mutation == "attribution_extra":
        payload["items"][0]["attribution"]["unknownNested"] = "forbidden"
    elif mutation == "page_wrong_type":
        payload["page"]["count"] = "1"
    else:
        payload["page"]["hasMore"] = True
        payload["page"].pop("nextCursor", None)
    encoded = ds.canonical_json_bytes(payload)
    compressed = ds.deterministic_gzip(encoded)
    raw_files[path] = compressed
    page_reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
    page_reference["response_body_sha256"] = ds.sha256_hex(encoded)


def move_all_capture_items_into_window_one(
    ds: Any,
    capture: dict[str, object],
    raw_files: dict[str, bytes],
) -> None:
    target_ids = ["fiction-item-alpha", "fiction-item-beta"]
    for capture_pass in capture["passes"]:  # type: ignore[index]
        page_reference = capture_pass["raw_pages"][0]
        path = page_reference["raw_path"]
        payload = json.loads(gzip.decompress(raw_files[path]))
        payload["items"][1]["publishedAt"] = "2026-08-18T07:00:00Z"
        payload["items"][1]["discoveredAt"] = "2026-08-18T08:00:00Z"
        encoded = ds.canonical_json_bytes(payload)
        compressed = ds.deterministic_gzip(encoded)
        raw_files[path] = compressed
        page_reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
        page_reference["response_body_sha256"] = ds.sha256_hex(encoded)
        capture_pass["formal_windows"][0]["target_item_id_count"] = 2
        capture_pass["formal_windows"][0]["target_item_id_sha256"] = id_digest(ds, target_ids)
        capture_pass["formal_windows"][1]["target_item_id_count"] = 0
        capture_pass["formal_windows"][1]["target_item_id_sha256"] = id_digest(ds, [])


def test_v1_schema_is_strict_and_self_describing(ds: Any) -> None:
    assert SCHEMA_PATH.is_file()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/aihot-item-v1.schema.json")
    assert schema["additionalProperties"] is False
    frozen_v1_fields = {
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
    assert ds.AIHOT_ITEM_V1_FIELDS == frozenset(frozen_v1_fields)
    assert set(schema["required"]) == frozen_v1_fields
    assert set(schema["properties"]) == frozen_v1_fields
    assert set(ds.AihotItemV1.model_fields) == frozen_v1_fields
    assert ds.AihotItem is ds.AihotItemV1
    assert schema["properties"]["aihot_score_0_to_100"]["anyOf"] == [
        {"type": "number", "minimum": 0, "maximum": 100},
        {"type": "null"},
    ]
    assert schema["properties"]["aihot_selected"]["description"] == "Public API selected flag."
    assert schema["properties"]["tags"]["uniqueItems"] is True
    assert schema["properties"]["api_record_projection_observation"]["type"] == "string"
    assert schema["properties"]["api_record_projection_observation"]["const"] == "complete"
    assert schema["properties"]["ssr_tags_observation"]["type"] == "string"
    assert schema["properties"]["ssr_tags_observation"]["const"] == "observed"


@pytest.mark.parametrize(
    "field_name",
    [
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
)
def test_v1_schema_rejects_same_key_semantic_drift(ds: Any, field_name: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    ds.validate_aihot_item_v1_schema(ds.canonical_json_bytes(schema))
    schema["properties"][field_name]["readOnly"] = True
    assert (
        error_code(
            ds,
            lambda: ds.validate_aihot_item_v1_schema(ds.canonical_json_bytes(schema)),
        )
        == "item_schema_semantic_mismatch"
    )


def test_v1_schema_freezes_top_level_semantics_but_not_editable_prose(ds: Any) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    prose_edit = copy.deepcopy(schema)
    prose_edit["description"] = "Edited explanatory prose with unchanged machine semantics."
    prose_edit["properties"]["aihot_selected"]["description"] = "Edited prose only."
    ds.validate_aihot_item_v1_schema(ds.canonical_json_bytes(prose_edit))

    required_drift = copy.deepcopy(schema)
    required_drift["required"].remove("aihot_selected")
    assert (
        error_code(
            ds,
            lambda: ds.validate_aihot_item_v1_schema(ds.canonical_json_bytes(required_drift)),
        )
        == "item_schema_semantic_mismatch"
    )
    object_drift = copy.deepcopy(schema)
    object_drift["additionalProperties"] = True
    assert (
        error_code(
            ds,
            lambda: ds.validate_aihot_item_v1_schema(ds.canonical_json_bytes(object_drift)),
        )
        == "item_schema_semantic_mismatch"
    )

    field_named_like_annotation = copy.deepcopy(schema)
    field_named_like_annotation["properties"]["title"] = {"type": "string"}
    assert (
        error_code(
            ds,
            lambda: ds.validate_aihot_item_v1_schema(
                ds.canonical_json_bytes(field_named_like_annotation)
            ),
        )
        == "item_schema_semantic_mismatch"
    )


def test_v1_serializer_rejects_future_model_field_mutation(ds: Any) -> None:
    class FutureAihotItem(ds.AihotItemV1):
        future_field: str

    future_item = FutureAihotItem(**synthetic_item_record(ds).model_dump(), future_field="not-v1")
    assert error_code(ds, lambda: ds.serialize_items_jsonl([future_item])) == "item_schema_version_mismatch"


def test_v1_loader_rejects_missing_frozen_field(ds: Any) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    del payload["aihot_recommendation_reason"]
    encoded = ds.canonical_json_bytes(payload) + b"\n"
    assert error_code(ds, lambda: ds.load_items_jsonl(encoded)) == "item_schema_version_mismatch"


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("api_record_projection_observation", "partial"),
        ("ssr_tags_observation", "missing"),
    ],
)
def test_final_observation_states_are_closed_v1_literals(
    ds: Any, field: str, wrong_value: str
) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    assert payload["api_record_projection_observation"] == "complete"
    assert payload["ssr_tags_observation"] == "observed"
    payload[field] = wrong_value
    with pytest.raises(ValidationError):
        ds.AihotItemV1.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["api_record_projection_observation", "ssr_tags_observation"])
def test_v1_loader_rejects_absent_observation_state(ds: Any, missing_field: str) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    del payload[missing_field]
    encoded = ds.canonical_json_bytes(payload) + b"\n"
    assert error_code(ds, lambda: ds.load_items_jsonl(encoded)) == "item_schema_version_mismatch"


def test_final_row_rejects_extra_future_observation_state_field(ds: Any) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    payload["api_record_projection_observation_v2"] = "pending"
    with pytest.raises(ValidationError):
        ds.AihotItemV1.model_validate(payload)


def test_persisted_contract_rejects_superseded_field_names(ds: Any) -> None:
    item_payload = synthetic_item_record(ds).model_dump(mode="json")
    ds.AihotItemV1.model_validate(item_payload)
    legacy_item = copy.deepcopy(item_payload)
    legacy_item["api_record_observation"] = legacy_item.pop("api_record_projection_observation")
    with pytest.raises(ValidationError):
        ds.AihotItemV1.model_validate(legacy_item)

    capture, _ = synthetic_capture_bundle(ds)
    page_reference = capture["passes"][0]["raw_pages"][0]  # type: ignore[index]
    ds.RawPageReference.model_validate(page_reference)
    legacy_page = copy.deepcopy(page_reference)
    legacy_page["canonical_request_url_projection"] = "https://aihot.invalid/api/v1/items"
    legacy_page["raw_sha256"] = legacy_page.pop("compressed_raw_sha256")
    legacy_page["body_sha256"] = legacy_page.pop("response_body_sha256")
    with pytest.raises(ValidationError):
        ds.RawPageReference.model_validate(legacy_page)

    public_reference = capture["public_responses"][0]  # type: ignore[index]
    ds.PublicResponseReference.model_validate(public_reference)
    legacy_public = copy.deepcopy(public_reference)
    legacy_public["raw_sha256"] = legacy_public.pop("compressed_raw_sha256")
    legacy_public["body_sha256"] = legacy_public.pop("response_body_sha256")
    with pytest.raises(ValidationError):
        ds.PublicResponseReference.model_validate(legacy_public)

    capture_pass = capture["passes"][0]  # type: ignore[index]
    ds.CapturePassManifest.model_validate(capture_pass)
    legacy_pass = copy.deepcopy(capture_pass)
    legacy_pass["index"] = 0
    legacy_pass["terminal"] = legacy_pass.pop("reached_has_more_false")
    with pytest.raises(ValidationError):
        ds.CapturePassManifest.model_validate(legacy_pass)

    source_identity = capture["source"]
    ds.SourceIdentity.model_validate(source_identity)
    legacy_source = copy.deepcopy(source_identity)
    legacy_source["first_public_response_observed_at"] = "2026-08-20T12:00:00Z"
    with pytest.raises(ValidationError):
        ds.SourceIdentity.model_validate(legacy_source)

    window, _, _ = synthetic_window_bundle(ds, capture)
    ds.WindowManifest.model_validate(window)
    ssr_reference = window["tag_observation_responses"][0]  # type: ignore[index]
    ds.SsrTagObservationResponse.model_validate(ssr_reference)
    legacy_ssr = copy.deepcopy(ssr_reference)
    legacy_ssr["raw_sha256"] = legacy_ssr.pop("compressed_raw_sha256")
    legacy_ssr["body_sha256"] = legacy_ssr.pop("response_body_sha256")
    with pytest.raises(ValidationError):
        ds.SsrTagObservationResponse.model_validate(legacy_ssr)

    legacy_window = copy.deepcopy(window)
    legacy_window["counts"] = legacy_window.pop("tag_reconciliation_counts")
    legacy_window["target_id_sha256"] = legacy_window.pop(
        "canonical_pass_target_item_id_sha256_projection"
    )
    legacy_window["schema"] = capture["schema"]
    legacy_window["tool"] = capture["tool"]
    with pytest.raises(ValidationError):
        ds.WindowManifest.model_validate(legacy_window)


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [
        ("api_target_count", "api_target_item_id_count"),
        ("normalized_count", "normalized_item_record_count"),
        ("ssr_list_match_count", "ssr_list_matched_target_item_id_count"),
        ("detail_fallback_count", "detail_fallback_target_item_id_count"),
        ("explicit_empty_tags_count", "explicit_empty_tags_target_item_id_count"),
        ("missing_count", "missing_tag_observation_target_item_id_count"),
        ("duplicate_count", "non_equivalent_tag_observation_target_item_id_count"),
        ("conflict_count", "api_ssr_identity_conflict_target_item_id_count"),
    ],
)
def test_reconciliation_counts_rejects_superseded_leaf_names(
    ds: Any, old_name: str, new_name: str
) -> None:
    capture, _ = synthetic_capture_bundle(ds)
    window, _, _ = synthetic_window_bundle(ds, capture)
    payload = window["tag_reconciliation_counts"]
    ds.ReconciliationCounts.model_validate(payload)
    legacy_payload = copy.deepcopy(payload)
    legacy_payload[old_name] = legacy_payload.pop(new_name)
    with pytest.raises(ValidationError):
        ds.ReconciliationCounts.model_validate(legacy_payload)


@pytest.mark.parametrize(
    ("new_name", "old_name"),
    [
        (
            "missing_tag_observation_target_item_id_count",
            "missing_tag_observation_target_id_count",
        ),
        (
            "non_equivalent_tag_observation_target_item_id_count",
            "non_equivalent_tag_observation_target_id_count",
        ),
        (
            "api_ssr_identity_conflict_target_item_id_count",
            "api_ssr_identity_conflict_target_id_count",
        ),
    ],
)
def test_gap_count_old_target_id_names_fail_closed_in_window_and_report(
    ds: Any, new_name: str, old_name: str
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    counts = window["tag_reconciliation_counts"]
    counts[old_name] = counts.pop(new_name)  # type: ignore[union-attr]
    with pytest.raises(ValidationError):
        ds.WindowManifest.model_validate(window)
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )

    report = synthetic_validation_report_shape(ds)
    report_counts = report["window_validation"]["tag_reconciliation_counts"]  # type: ignore[index]
    report_counts[old_name] = report_counts.pop(new_name)
    assert (
        error_code(ds, lambda: ds.validate_validation_report_v1_contract_shape(report))
        == "report_contract_mismatch"
    )


def test_serialized_nullable_reason_and_empty_tags_expose_completed_observation_states(ds: Any) -> None:
    api_payload = synthetic_api_item()
    api_payload["reason"] = None
    api_item = ds.parse_api_item(api_payload)
    final_item = ds.reconcile_tags(
        [api_item],
        [
            ds.TagObservation(
                item_id=api_item.id,
                channel="list",
                tags=[],
                observed_aihot_url=api_item.aihot_url,
            )
        ],
    ).items[0]
    serialized = json.loads(ds.serialize_items_jsonl([final_item]))
    assert serialized["tags"] == []
    assert serialized["aihot_summary"] == "Clearly fictional summary for deterministic contract testing."
    assert serialized["aihot_recommendation_reason"] is None
    assert serialized["api_record_projection_observation"] == "complete"
    assert serialized["ssr_tags_observation"] == "observed"


def test_downstream_frozen_item_nullable_fields_round_trip_independently_of_upstream_shape(ds: Any) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    nullable_fields = (
        "original_title",
        "aihot_summary",
        "published_at",
        "aihot_category_slug",
        "aihot_score_0_to_100",
    )
    for field in nullable_fields:
        payload[field] = None

    frozen_item = ds.AihotItemV1.model_validate(payload)
    encoded = ds.serialize_items_jsonl([frozen_item])
    serialized = json.loads(encoded)
    assert {field: serialized[field] for field in nullable_fields} == {
        field: None for field in nullable_fields
    }
    assert serialized["api_record_projection_observation"] == "complete"
    assert serialized["ssr_tags_observation"] == "observed"

    reopened = ds.load_items_jsonl(encoded, schema_bytes=SCHEMA_PATH.read_bytes())
    assert reopened == [frozen_item]
    assert reopened[0].api_record_projection_observation == "complete"
    assert reopened[0].ssr_tags_observation == "observed"


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "aihot_title",
        "original_title",
        "aihot_summary",
        "aihot_category_slug",
        "aihot_recommendation_reason",
        "upstream_publisher_name",
    ],
)
def test_item_schema_and_runtime_reject_whitespace_only_strings(ds: Any, field: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    field_schema = schema["properties"][field]
    string_schema = (
        next(branch for branch in field_schema["anyOf"] if branch.get("type") == "string")
        if "anyOf" in field_schema
        else field_schema
    )
    assert string_schema["pattern"] == ".*\\S.*"

    payload = synthetic_item_record(ds).model_dump(mode="json")
    payload[field] = " \t "
    with pytest.raises(ValidationError):
        ds.AihotItemV1.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aihot_url", "relative/item"),
        ("original_url", "ftp://origin.invalid/item"),
        ("aihot_discovered_at", "2026-08-18 08:00:00"),
        ("published_at", "not-a-date"),
        ("aihot_score_0_to_100", -0.01),
        ("aihot_score_0_to_100", 100.01),
        ("tags", [""]),
        ("tags", ["duplicate", "duplicate"]),
    ],
)
def test_item_model_rejects_invalid_values(ds: Any, field: str, value: object) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        ds.AihotItem.model_validate(payload)


def test_aihot_url_schema_does_not_narrow_the_frozen_nonempty_id_contract(ds: Any) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    pattern = schema["properties"]["aihot_url"]["pattern"]
    assert re.fullmatch(pattern, "https://aihot.invalid/items/fiction/group")
    payload = synthetic_item_record(ds).model_dump(mode="json")
    payload["id"] = "fiction/group"
    payload["aihot_url"] = "https://aihot.invalid/items/fiction/group"
    ds.AihotItemV1.model_validate(payload)


def test_item_model_rejects_extra_and_missing_keys(ds: Any) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    with pytest.raises(ValidationError):
        ds.AihotItem.model_validate({**payload, "unexpected": "forbidden"})
    payload.pop("original_title")
    with pytest.raises(ValidationError):
        ds.AihotItem.model_validate(payload)


def test_api_item_projection_preserves_upstream_strings(ds: Any) -> None:
    projected = ds.parse_api_item(synthetic_api_item())
    assert projected.model_dump(mode="json") == {
        "id": "fiction-item-alpha",
        "aihot_url": "https://aihot.invalid/items/fiction-item-alpha",
        "original_url": "https://origin.invalid/articles/fiction-item-alpha",
        "aihot_title": "Synthetic Teapot Bulletin fiction-item-alpha",
        "original_title": "Invented Kettle Dispatch fiction-item-alpha",
        "aihot_summary": "Clearly fictional summary for deterministic contract testing.",
        "upstream_publisher_name": "Fictional Gazette",
        "published_at": "2026-08-18T07:00:00Z",
        "aihot_discovered_at": "2026-08-18T08:00:00Z",
        "aihot_category_slug": "invented-research",
        "aihot_score_0_to_100": 73,
        "aihot_selected": True,
        "aihot_recommendation_reason": "Synthetic recommendation rationale.",
    }


def test_unreconciled_api_item_cannot_be_serialized_as_final_v1(ds: Any) -> None:
    api_item = ds.parse_api_item(synthetic_api_item())
    assert isinstance(api_item, ds.UnreconciledApiItem)
    assert not isinstance(api_item, ds.AihotItemV1)
    assert "tags" not in api_item.model_dump(mode="json")
    assert "api_record_projection_observation" not in api_item.model_dump(mode="json")
    assert "ssr_tags_observation" not in api_item.model_dump(mode="json")
    assert (
        error_code(
            ds,
            lambda: ds.serialize_items_jsonl([api_item]),  # type: ignore[list-item]
        )
        == "item_unreconciled"
    )

    reconciled = ds.reconcile_tags(
        [api_item],
        [
            ds.TagObservation(
                item_id=api_item.id,
                channel="list",
                tags=[],
                observed_aihot_url=api_item.aihot_url,
            )
        ],
    ).items[0]
    assert isinstance(reconciled, ds.AihotItemV1)
    assert reconciled.api_record_projection_observation == "complete"
    assert reconciled.ssr_tags_observation == "observed"
    assert ds.serialize_items_jsonl([reconciled])


def test_v1_serializer_and_report_keys_reject_old_ambiguous_names(ds: Any) -> None:
    item = synthetic_item_record(ds)
    serialized = json.loads(ds.serialize_items_jsonl([item]))
    assert serialized["aihot_score_0_to_100"] == 73
    assert serialized["aihot_selected"] is True
    assert serialized["aihot_title"] == "Synthetic Teapot Bulletin fiction-item-alpha"
    assert serialized["aihot_summary"] == "Clearly fictional summary for deterministic contract testing."
    assert serialized["aihot_discovered_at"] == "2026-08-18T08:00:00Z"
    assert serialized["aihot_category_slug"] == "invented-research"
    assert serialized["aihot_recommendation_reason"] == "Synthetic recommendation rationale."
    assert "score" not in serialized
    assert "selected" not in serialized
    assert "title" not in serialized
    assert "source_name" not in serialized
    assert "summary" not in serialized
    assert "discovered_at" not in serialized
    assert "category" not in serialized
    assert "recommendation_reason" not in serialized
    assert serialized["upstream_publisher_name"] == "Fictional Gazette"

    assert ds.AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS == (
        "aihot_category_slug",
        "tags",
        "aihot_score_0_to_100",
        "aihot_summary",
        "aihot_selected",
        "aihot_recommendation_reason",
    )
    ds.validate_field_coverage_keys(ds.AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS)
    for index, old_name in (
        (0, "category"),
        (2, "score"),
        (3, "summary"),
        (4, "selected"),
        (5, "recommendation_reason"),
    ):
        mutated_keys = list(ds.AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS)
        mutated_keys[index] = old_name
        assert (
            error_code(ds, lambda: ds.validate_field_coverage_keys(mutated_keys))
            == "report_contract_mismatch"
        )


def test_v1_rejects_old_source_name_field(ds: Any) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    assert payload["upstream_publisher_name"] == "Fictional Gazette"
    old_name_payload = dict(payload)
    old_name_payload["source_name"] = old_name_payload.pop("upstream_publisher_name")
    with pytest.raises(ValidationError):
        ds.AihotItemV1.model_validate(old_name_payload)
    encoded = ds.canonical_json_bytes(old_name_payload) + b"\n"
    assert error_code(ds, lambda: ds.load_items_jsonl(encoded)) == "item_schema_version_mismatch"


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [
        ("title", "aihot_title"),
        ("score", "aihot_score_0_to_100"),
        ("selected", "aihot_selected"),
        ("summary", "aihot_summary"),
        ("discovered_at", "aihot_discovered_at"),
        ("category", "aihot_category_slug"),
        ("recommendation_reason", "aihot_recommendation_reason"),
    ],
)
def test_v1_rejects_old_raw_row_field_names(ds: Any, old_name: str, new_name: str) -> None:
    payload = synthetic_item_record(ds).model_dump(mode="json")
    assert new_name in payload
    old_name_payload = dict(payload)
    old_name_payload[old_name] = old_name_payload.pop(new_name)
    with pytest.raises(ValidationError):
        ds.AihotItemV1.model_validate(old_name_payload)
    encoded = ds.canonical_json_bytes(old_name_payload) + b"\n"
    assert error_code(ds, lambda: ds.load_items_jsonl(encoded)) == "item_schema_version_mismatch"



def synthetic_validation_report_shape(
    ds: Any,
    *,
    subject_artifact_type: str = "aihot_window_v1",
) -> dict[str, object]:
    coverage_leaf = {
        "present_item_record_count": 1,
        "non_null_item_record_count": 1,
        "observed_item_record_count": 1,
    }
    is_window = subject_artifact_type == "aihot_window_v1"
    subject_path = WINDOW_MANIFEST_PATH if is_window else CAPTURE_PATH
    window_validation: dict[str, object] | None = None
    if is_window:
        window_validation = {
            "window": {
                "start_inclusive": WINDOW_ONE[0],
                "end_exclusive": WINDOW_ONE[1],
                "time_basis": "aihot_timeline_v1",
            },
            "target_item_id_count": 1,
            "target_item_id_sha256": id_digest(ds, ["fiction-item-alpha"]),
            "tag_reconciliation_counts": {
                "api_target_item_id_count": 1,
                "normalized_item_record_count": 1,
                "ssr_list_matched_target_item_id_count": 1,
                "detail_fallback_target_item_id_count": 0,
                "explicit_empty_tags_target_item_id_count": 0,
                "missing_tag_observation_target_item_id_count": 0,
                "non_equivalent_tag_observation_target_item_id_count": 0,
                "api_ssr_identity_conflict_target_item_id_count": 0,
            },
            "pairing": {key: 1 for key in ds.AIHOT_ITEM_V1_PAIRING_KEYS},
            "pairing_strategy": {
                "primary": "original_url",
                "assistance": "original_title",
                "fallback": "aihot_title",
            },
            "field_coverage": {
                key: dict(coverage_leaf) for key in ds.AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS
            },
        }
    integrity_status = "pass" if is_window else "not_applicable_for_capture_subject"
    return {
        "artifact_type": "aihot_validation_report_v1",
        "subject": {
            "artifact_type": subject_artifact_type,
            "path": subject_path,
            "sha256": "a" * 64,
        },
        "identity": {
            "source_base_url": "https://aihot.invalid",
            "first_public_response_observed_at": "2026-08-20T12:00:00Z",
            "openapi_saved_public_response_body_sha256": "b" * 64,
            "clean_tool_commit": "c" * 40,
            "item_schema_path": SCHEMA_RELATIVE_PATH,
            "item_schema_sha256": ds.sha256_hex(SCHEMA_PATH.read_bytes()),
        },
        "stability": {
            "pass_count": 2,
            "canonical_pass_index": 1,
            "accepted_pass_index_pair": [0, 1],
            "formal_day_pair_equal": True,
            "formal_window_target_item_id_sha256_comparisons": [
                {
                    "start_inclusive": WINDOW_ONE[0],
                    "end_exclusive": WINDOW_ONE[1],
                    "equal": True,
                },
                {
                    "start_inclusive": WINDOW_TWO[0],
                    "end_exclusive": WINDOW_TWO[1],
                    "equal": True,
                },
            ],
        },
        "window_validation": window_validation,
        "integrity": {
            "capture_manifest": "pass",
            "public_response_raw_replay": "pass",
            "api_pass_raw_replay": "pass",
            "item_schema": "pass",
            "window_manifest": integrity_status,
            "items_jsonl": integrity_status,
            "tag_reconciliation": integrity_status,
        },
        "result": "pass",
    }


def test_validation_report_v1_names_and_nested_key_shapes_are_frozen(ds: Any) -> None:
    assert ds.AIHOT_VALIDATION_REPORT_V1_ARTIFACT_TYPE == "aihot_validation_report_v1"
    assert ds.AIHOT_ITEM_V1_PAIRING_KEYS == (
        "item_record_count",
        "original_url_present_item_record_count",
        "original_title_key_present_item_record_count",
        "aihot_title_present_item_record_count",
    )
    assert ds.AIHOT_ITEM_V1_FIELD_COVERAGE_LEAF_KEYS == (
        "present_item_record_count",
        "non_null_item_record_count",
        "observed_item_record_count",
    )
    assert ds.AIHOT_RECONCILIATION_COUNT_KEYS == (
        "api_target_item_id_count",
        "normalized_item_record_count",
        "ssr_list_matched_target_item_id_count",
        "detail_fallback_target_item_id_count",
        "explicit_empty_tags_target_item_id_count",
        "missing_tag_observation_target_item_id_count",
        "non_equivalent_tag_observation_target_item_id_count",
        "api_ssr_identity_conflict_target_item_id_count",
    )
    assert ds.AIHOT_VALIDATION_REPORT_V1_WINDOW_VALIDATION_KEYS == (
        "window",
        "target_item_id_count",
        "target_item_id_sha256",
        "tag_reconciliation_counts",
        "pairing",
        "pairing_strategy",
        "field_coverage",
    )
    assert ds.AIHOT_VALIDATION_REPORT_V1_TOP_LEVEL_KEYS == (
        "artifact_type",
        "subject",
        "identity",
        "stability",
        "window_validation",
        "integrity",
        "result",
    )
    assert ds.AIHOT_VALIDATION_REPORT_V1_STABILITY_KEYS == (
        "pass_count",
        "canonical_pass_index",
        "accepted_pass_index_pair",
        "formal_day_pair_equal",
        "formal_window_target_item_id_sha256_comparisons",
    )
    for subject_artifact_type in ("aihot_capture_v1", "aihot_window_v1"):
        report = synthetic_validation_report_shape(
            ds, subject_artifact_type=subject_artifact_type
        )
        ds.validate_validation_report_v1_contract_shape(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "artifact_type",
        "top_level_extra",
        "subject_extra",
        "stability_old_comparison",
        "capture_with_window_validation",
        "capture_with_window_integrity_pass",
        "window_with_capture_integrity_state",
        "window_count_old_name",
        "window_pairing_old_name",
        "window_coverage_old_leaf",
        "result",
    ],
)
def test_validation_report_v1_contract_rejects_semantic_shape_drift(
    ds: Any, mutation: str
) -> None:
    report = synthetic_validation_report_shape(ds)
    if mutation == "artifact_type":
        report["artifact_type"] = "aihot_validation_report_v2"
    elif mutation == "top_level_extra":
        report["future"] = {}
    elif mutation == "subject_extra":
        subject = report["subject"]
        assert isinstance(subject, dict)
        subject["future"] = "forbidden"
    elif mutation == "stability_old_comparison":
        stability = report["stability"]
        assert isinstance(stability, dict)
        stability["target_hash_equal_by_window"] = stability.pop(
            "formal_window_target_item_id_sha256_comparisons"
        )
    elif mutation == "capture_with_window_validation":
        report = synthetic_validation_report_shape(ds, subject_artifact_type="aihot_capture_v1")
        report["window_validation"] = synthetic_validation_report_shape(ds)["window_validation"]
    elif mutation == "capture_with_window_integrity_pass":
        report = synthetic_validation_report_shape(ds, subject_artifact_type="aihot_capture_v1")
        report["integrity"]["window_manifest"] = "pass"  # type: ignore[index]
    elif mutation == "window_with_capture_integrity_state":
        report["integrity"]["items_jsonl"] = "not_applicable_for_capture_subject"  # type: ignore[index]
    elif mutation == "window_count_old_name":
        window_validation = report["window_validation"]
        assert isinstance(window_validation, dict)
        counts = window_validation["tag_reconciliation_counts"]
        assert isinstance(counts, dict)
        counts["api_target_count"] = counts.pop("api_target_item_id_count")
    elif mutation == "window_pairing_old_name":
        window_validation = report["window_validation"]
        assert isinstance(window_validation, dict)
        pairing = window_validation["pairing"]
        assert isinstance(pairing, dict)
        pairing["total"] = pairing.pop("item_record_count")
    elif mutation == "window_coverage_old_leaf":
        window_validation = report["window_validation"]
        assert isinstance(window_validation, dict)
        coverage = window_validation["field_coverage"]
        assert isinstance(coverage, dict)
        first = coverage[ds.AIHOT_ITEM_V1_FIELD_COVERAGE_KEYS[0]]
        assert isinstance(first, dict)
        first["present"] = first.pop("present_item_record_count")
    else:
        report["result"] = "fail"
    assert (
        error_code(ds, lambda: ds.validate_validation_report_v1_contract_shape(report))
        == "report_contract_mismatch"
    )


def test_adr061_report_accepts_key_reordering_and_reopens_canonical_bytes(ds: Any) -> None:
    for subject_artifact_type in ("aihot_capture_v1", "aihot_window_v1"):
        report = synthetic_validation_report_shape(
            ds, subject_artifact_type=subject_artifact_type
        )
        reordered = dict(reversed(list(report.items())))
        ds.validate_validation_report_v1_contract_shape(reordered)
        encoded = ds.canonical_json_bytes(reordered)
        reopened = ds.load_validation_report_v1(encoded)
        assert reopened == report
        assert ds.canonical_json_bytes(reopened) == encoded

    noncanonical = json.dumps(
        synthetic_validation_report_shape(ds),
        ensure_ascii=False,
        sort_keys=False,
    ).encode()
    assert (
        error_code(ds, lambda: ds.load_validation_report_v1(noncanonical))
        == "manifest_invalid"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "subject_path",
        "subject_sha",
        "pass_count",
        "canonical_pair",
        "comparison_count",
        "comparison_equal",
        "window_target_count",
        "identity_extra",
        "integrity_missing",
    ],
)
def test_adr061_report_rejects_types_ranges_and_cross_field_drift(
    ds: Any, mutation: str
) -> None:
    report = synthetic_validation_report_shape(ds)
    if mutation == "subject_path":
        report["subject"]["path"] = CAPTURE_PATH  # type: ignore[index]
    elif mutation == "subject_sha":
        report["subject"]["sha256"] = "not-a-digest"  # type: ignore[index]
    elif mutation == "pass_count":
        report["stability"]["pass_count"] = 4  # type: ignore[index]
    elif mutation == "canonical_pair":
        report["stability"]["accepted_pass_index_pair"] = [0, 0]  # type: ignore[index]
    elif mutation == "comparison_count":
        report["stability"]["formal_window_target_item_id_sha256_comparisons"].pop()  # type: ignore[index]
    elif mutation == "comparison_equal":
        report["stability"]["formal_window_target_item_id_sha256_comparisons"][0]["equal"] = False  # type: ignore[index]
    elif mutation == "window_target_count":
        report["window_validation"]["target_item_id_count"] = -1  # type: ignore[index]
    elif mutation == "identity_extra":
        report["identity"]["future"] = "forbidden"  # type: ignore[index]
    else:
        del report["integrity"]["tag_reconciliation"]  # type: ignore[index]
    assert (
        error_code(ds, lambda: ds.validate_validation_report_v1_contract_shape(report))
        == "report_contract_mismatch"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_role",
        "extra_role",
        "role_swap",
        "wrong_literal",
        "positional",
        "old_shape",
    ],
)
def test_adr062_pairing_strategy_is_closed_and_role_named(ds: Any, mutation: str) -> None:
    report = synthetic_validation_report_shape(ds)
    window_validation = report["window_validation"]
    assert isinstance(window_validation, dict)
    strategy = window_validation["pairing_strategy"]
    assert isinstance(strategy, dict)
    if mutation == "missing_role":
        del strategy["assistance"]
    elif mutation == "extra_role":
        strategy["secondary"] = "original_title"
    elif mutation == "role_swap":
        strategy["primary"], strategy["fallback"] = (
            strategy["fallback"],
            strategy["primary"],
        )
    elif mutation == "wrong_literal":
        strategy["assistance"] = "aihot_title"
    elif mutation == "positional":
        window_validation["pairing_strategy"] = [
            "original_url",
            "original_title",
            "aihot_title",
        ]
    else:
        del window_validation["pairing_strategy"]
    assert (
        error_code(ds, lambda: ds.validate_validation_report_v1_contract_shape(report))
        == "report_contract_mismatch"
    )


def test_adr062_pairing_strategy_positive_shape_is_self_describing(ds: Any) -> None:
    report = synthetic_validation_report_shape(ds)
    ds.validate_validation_report_v1_contract_shape(report)
    assert report["window_validation"]["pairing_strategy"] == {  # type: ignore[index]
        "primary": "original_url",
        "assistance": "original_title",
        "fallback": "aihot_title",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "api_target_total",
        "normalized_total",
        "channel_total",
        "pairing_total",
        "pairing_presence",
        "coverage_present",
        "non_null_above_present",
        "observed_above_present",
        "tags_not_observed",
        "gap_nonzero",
        "empty_above_total",
    ],
)
def test_validation_report_v1_rejects_internal_count_semantic_drift(
    ds: Any, mutation: str
) -> None:
    report = synthetic_validation_report_shape(ds)
    window_validation = report["window_validation"]
    assert isinstance(window_validation, dict)
    counts = window_validation["tag_reconciliation_counts"]
    pairing = window_validation["pairing"]
    coverage = window_validation["field_coverage"]
    assert isinstance(counts, dict)
    assert isinstance(pairing, dict)
    assert isinstance(coverage, dict)
    if mutation == "api_target_total":
        counts["api_target_item_id_count"] = 2
    elif mutation == "normalized_total":
        counts["normalized_item_record_count"] = 2
    elif mutation == "channel_total":
        counts["ssr_list_matched_target_item_id_count"] = 0
    elif mutation == "pairing_total":
        pairing["item_record_count"] = 0
    elif mutation == "pairing_presence":
        pairing["original_url_present_item_record_count"] = 0
    elif mutation == "coverage_present":
        coverage["aihot_summary"]["present_item_record_count"] = 0
    elif mutation == "non_null_above_present":
        coverage["aihot_summary"]["non_null_item_record_count"] = 2
    elif mutation == "observed_above_present":
        coverage["aihot_summary"]["observed_item_record_count"] = 2
    elif mutation == "tags_not_observed":
        coverage["tags"]["observed_item_record_count"] = 0
    elif mutation == "gap_nonzero":
        counts["missing_tag_observation_target_item_id_count"] = 1
    else:
        counts["explicit_empty_tags_target_item_id_count"] = 2
    assert (
        error_code(ds, lambda: ds.validate_validation_report_v1_contract_shape(report))
        == "report_contract_mismatch"
    )


def test_cursor_traversal_reaches_terminal_page(ds: Any) -> None:
    pages: Iterator[Any] = iter(
        [
            response(ds, page_payload([synthetic_api_item()], has_more=True, next_cursor="synthetic-cursor-2")),
            response(
                ds,
                page_payload(
                    [synthetic_api_item("fiction-item-beta")],
                    has_more=False,
                    next_cursor=None,
                ),
            ),
        ]
    )
    clock = FakeClock()
    limiter = ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    result = ds.traverse_api_pages(lambda _cursor: next(pages), limiter=limiter)
    assert result.terminal is True
    assert [item.id for item in result.items] == ["fiction-item-alpha", "fiction-item-beta"]
    assert [record.started_at for record in limiter.request_starts] == [0.0, 2.0]


def test_api_envelope_constants_freeze_capture_and_observed_default_queries(ds: Any) -> None:
    assert ds.AIHOT_API_SCHEMA_VERSION == 1
    assert ds.AIHOT_API_BY_VALUES == ("timeline", "published")
    assert ds.AIHOT_CAPTURE_QUERY_V1 == {
        "mode": "all",
        "category": None,
        "window": "7d",
        "q": None,
        "by": "timeline",
        "ordering": "timelineDesc",
    }
    assert ds.AIHOT_OBSERVED_DEFAULT_QUERY_V1 == {
        "mode": "selected",
        "category": None,
        "window": "24h",
        "q": None,
        "by": "timeline",
        "ordering": "timelineDesc",
    }


def test_observed_default_api_envelope_is_accepted_only_with_its_expected_query(ds: Any) -> None:
    payload = page_payload(
        [synthetic_api_item()],
        has_more=False,
        next_cursor=None,
        mode="selected",
        window="24h",
    )
    page = ds._parse_page_body(
        ds.canonical_json_bytes(payload),
        expected_query=ds.AIHOT_OBSERVED_DEFAULT_QUERY_V1,
    )
    assert page.schemaVersion == 1
    assert page.query.model_dump(mode="json") == ds.AIHOT_OBSERVED_DEFAULT_QUERY_V1


def test_api_envelope_by_domain_accepts_only_timeline_or_published(ds: Any) -> None:
    for by in ds.AIHOT_API_BY_VALUES:
        payload = page_payload(
            [synthetic_api_item()],
            has_more=False,
            next_cursor=None,
            by=by,
        )
        expected_query = {**ds.AIHOT_CAPTURE_QUERY_V1, "by": by}
        page = ds._parse_page_body(
            ds.canonical_json_bytes(payload),
            expected_query=expected_query,
        )
        assert page.query.by == by

    unsupported_by = "fictionalLegacyBy"
    payload = page_payload([], has_more=False, next_cursor=None, by=unsupported_by)
    expected_query = {**ds.AIHOT_CAPTURE_QUERY_V1, "by": unsupported_by}
    assert (
        error_code(
            ds,
            lambda: ds._parse_page_body(
                ds.canonical_json_bytes(payload),
                expected_query=expected_query,
            ),
        )
        == "page_invalid"
    )


def test_authoritative_api_model_key_sets_are_exact(ds: Any) -> None:
    assert tuple(ds._ApiPage.model_fields) == ("schemaVersion", "query", "items", "page")
    assert tuple(ds._ApiQuery.model_fields) == ("mode", "category", "window", "q", "by", "ordering")
    assert tuple(ds._ApiItem.model_fields) == (
        "id",
        "title",
        "originalTitle",
        "summary",
        "source",
        "links",
        "publishedAt",
        "discoveredAt",
        "category",
        "score",
        "selected",
        "reason",
        "attribution",
    )
    assert tuple(ds._ApiSource.model_fields) == ("name",)
    assert tuple(ds._ApiLinks.model_fields) == ("aihot", "original")
    assert tuple(ds._ApiAttribution.model_fields) == ("name", "url")
    assert tuple(ds._PageMetadata.model_fields) == ("count", "hasMore", "nextCursor")


@pytest.mark.parametrize("field", ["schemaVersion", "query", "items", "page"])
def test_api_root_rejects_each_missing_key(ds: Any, field: str) -> None:
    payload = page_payload([synthetic_api_item()], has_more=False, next_cursor=None)
    del payload[field]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("schemaVersion", "1"),
        ("query", []),
        ("items", {}),
        ("page", []),
    ],
)
def test_api_root_rejects_each_wrong_type(ds: Any, field: str, wrong_value: object) -> None:
    payload = page_payload([synthetic_api_item()], has_more=False, next_cursor=None)
    payload[field] = wrong_value
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


def test_api_root_rejects_unknown_extra_key(ds: Any) -> None:
    payload = page_payload([synthetic_api_item()], has_more=False, next_cursor=None)
    payload["unknownRoot"] = "forbidden"
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


@pytest.mark.parametrize("field", ["mode", "category", "window", "q", "by", "ordering"])
def test_api_query_rejects_each_missing_key(ds: Any, field: str) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    del payload["query"][field]  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


@pytest.mark.parametrize("field", ["mode", "category", "window", "q", "by", "ordering"])
def test_api_query_rejects_each_wrong_type(ds: Any, field: str) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    payload["query"][field] = 7  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


def test_api_query_rejects_unknown_extra_key_and_accepts_nullable_fields(ds: Any) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    ds._parse_page_body(ds.canonical_json_bytes(payload))
    payload["query"]["unknownQuery"] = "forbidden"  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "title",
        "originalTitle",
        "summary",
        "source",
        "links",
        "publishedAt",
        "discoveredAt",
        "category",
        "score",
        "selected",
        "reason",
        "attribution",
    ],
)
def test_api_item_rejects_each_missing_key(ds: Any, field: str) -> None:
    item = synthetic_api_item()
    del item[field]
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("id", 7),
        ("title", 7),
        ("originalTitle", None),
        ("summary", None),
        ("source", []),
        ("links", []),
        ("publishedAt", None),
        ("discoveredAt", None),
        ("category", None),
        ("score", "73"),
        ("selected", 1),
        ("reason", 7),
        ("attribution", []),
    ],
)
def test_api_item_rejects_each_wrong_type(ds: Any, field: str, wrong_value: object) -> None:
    item = synthetic_api_item()
    item[field] = wrong_value
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


@pytest.mark.parametrize("wrong_score", [73.5, True])
def test_api_item_score_is_strict_int_not_float_or_bool(ds: Any, wrong_score: object) -> None:
    item = synthetic_api_item()
    item["score"] = wrong_score
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


def test_api_item_accepts_nullable_reason_and_rejects_unknown_extra_key(ds: Any) -> None:
    item = synthetic_api_item()
    item["reason"] = None
    assert ds.parse_api_item(item).aihot_recommendation_reason is None
    item["unknownItem"] = "forbidden"
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


@pytest.mark.parametrize(
    ("container", "field"),
    [
        ("source", "name"),
        ("links", "aihot"),
        ("links", "original"),
        ("attribution", "name"),
        ("attribution", "url"),
    ],
)
def test_api_item_nested_shapes_reject_each_missing_key(ds: Any, container: str, field: str) -> None:
    item = synthetic_api_item()
    del item[container][field]  # type: ignore[index]
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


@pytest.mark.parametrize("container", ["source", "links", "attribution"])
def test_api_item_nested_shapes_reject_unknown_extra_key(ds: Any, container: str) -> None:
    item = synthetic_api_item()
    item[container]["unknownNested"] = "forbidden"  # type: ignore[index]
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


@pytest.mark.parametrize(
    ("container", "field"),
    [
        ("source", "name"),
        ("links", "aihot"),
        ("links", "original"),
        ("attribution", "name"),
        ("attribution", "url"),
    ],
)
def test_api_item_nested_shapes_reject_each_wrong_type(ds: Any, container: str, field: str) -> None:
    item = synthetic_api_item()
    item[container][field] = 7  # type: ignore[index]
    assert error_code(ds, lambda: ds.parse_api_item(item)) == "item_invalid"


@pytest.mark.parametrize("field", ["count", "hasMore"])
def test_api_page_metadata_rejects_each_required_missing_key(ds: Any, field: str) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    del payload["page"][field]  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [("count", 0.0), ("count", False), ("hasMore", 0), ("nextCursor", 7)],
)
def test_api_page_metadata_rejects_each_wrong_type(ds: Any, field: str, wrong_value: object) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    payload["page"][field] = wrong_value  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


def test_api_page_metadata_rejects_unknown_extra_key_without_inventing_count_semantics(ds: Any) -> None:
    payload = page_payload([synthetic_api_item()], has_more=False, next_cursor=None)
    payload["page"]["count"] = 999  # type: ignore[index]
    ds._parse_page_body(ds.canonical_json_bytes(payload))
    payload["page"]["unknownPage"] = "forbidden"  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "page_invalid"


@pytest.mark.parametrize("next_cursor", [None, "missing"])
def test_terminal_page_accepts_null_or_missing_next_cursor(ds: Any, next_cursor: str | None) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    if next_cursor == "missing":
        del payload["page"]["nextCursor"]  # type: ignore[index]
    ds._parse_page_body(ds.canonical_json_bytes(payload))


@pytest.mark.parametrize("next_cursor", [None, "missing"])
def test_nonterminal_page_rejects_null_or_missing_next_cursor(ds: Any, next_cursor: str | None) -> None:
    payload = page_payload([], has_more=True, next_cursor=None)
    if next_cursor == "missing":
        del payload["page"]["nextCursor"]  # type: ignore[index]
    assert error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload))) == "cursor_missing"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_schema_version", "page_invalid"),
        ("wrong_schema_version", "page_invalid"),
        ("missing_query", "page_invalid"),
        ("missing_items", "page_invalid"),
        ("missing_page", "page_invalid"),
        ("extra_envelope_key", "page_invalid"),
        ("missing_query_key", "page_invalid"),
        ("extra_query_key", "page_invalid"),
        ("legacy_items_page_only", "page_invalid"),
        ("wrong_mode", "page_invalid"),
        ("wrong_window", "page_invalid"),
        ("wrong_by", "page_invalid"),
        ("wrong_ordering", "page_invalid"),
    ],
)
def test_api_envelope_rejects_missing_wrong_legacy_and_query_semantic_mutations(
    ds: Any,
    mutation: str,
    expected_code: str,
) -> None:
    payload = page_payload([], has_more=False, next_cursor=None)
    if mutation == "missing_schema_version":
        del payload["schemaVersion"]
    elif mutation == "wrong_schema_version":
        payload["schemaVersion"] = 2
    elif mutation == "missing_query":
        del payload["query"]
    elif mutation == "missing_items":
        del payload["items"]
    elif mutation == "missing_page":
        del payload["page"]
    elif mutation == "extra_envelope_key":
        payload["fictionalLegacyEnvelope"] = True
    elif mutation == "missing_query_key":
        del payload["query"]["ordering"]  # type: ignore[index]
    elif mutation == "extra_query_key":
        payload["query"]["fictionalLegacySort"] = "timeline"  # type: ignore[index]
    elif mutation == "legacy_items_page_only":
        payload = {"items": payload["items"], "page": payload["page"]}
    elif mutation == "wrong_mode":
        payload["query"]["mode"] = "selected"  # type: ignore[index]
    elif mutation == "wrong_window":
        payload["query"]["window"] = "24h"  # type: ignore[index]
    elif mutation == "wrong_by":
        payload["query"]["by"] = "published"  # type: ignore[index]
    else:
        payload["query"]["ordering"] = "publishedDesc"  # type: ignore[index]
    assert (
        error_code(ds, lambda: ds._parse_page_body(ds.canonical_json_bytes(payload)))
        == expected_code
    )


@pytest.mark.parametrize(
    ("page", "expected_code"),
    [
        (page_payload([], has_more=True, next_cursor=None), "cursor_missing"),
        (page_payload([], has_more=True, next_cursor="synthetic-repeat"), "cursor_repeated"),
        ({"items": [{"invalid": "item"}], "page": {"hasMore": False, "nextCursor": None}}, "item_invalid"),
    ],
)
def test_cursor_and_item_failures_are_named(ds: Any, page: dict[str, object], expected_code: str) -> None:
    limiter = ds.GlobalRateLimiter()
    assert (
        error_code(
            ds,
            lambda: ds.traverse_api_pages(
                lambda _cursor: response(ds, page),
                limiter=limiter,
                initial_seen_cursors={"synthetic-repeat"},
            ),
        )
        == expected_code
    )


@pytest.mark.parametrize("status", [400, 403])
def test_non_retryable_http_status_never_returns_success(ds: Any, status: int) -> None:
    limiter = ds.GlobalRateLimiter()
    assert (
        error_code(
            ds,
            lambda: ds.request_with_policy(
                lambda: response(ds, {}, status=status),
                surface="api",
                limiter=limiter,
            ),
        )
        == f"http_{status}"
    )


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_and_global_rate_limit_share_one_deadline(ds: Any, status: int) -> None:
    replies = iter(
        [
            response(ds, {}, status=status, headers={"Retry-After": "5"}),
            response(ds, page_payload([], has_more=False, next_cursor=None)),
        ]
    )
    clock = FakeClock()
    limiter = ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    final = ds.request_with_policy(lambda: next(replies), surface="ssr", limiter=limiter, max_attempts=2)
    assert final.status == 200
    assert [record.started_at for record in limiter.request_starts] == [0.0, 5.0]


def test_retry_budget_exhaustion_is_named(ds: Any) -> None:
    clock = FakeClock()
    limiter = ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    assert (
        error_code(
            ds,
            lambda: ds.request_with_policy(
                lambda: response(ds, {}, status=429, headers={"Retry-After": "2"}),
                surface="rss",
                limiter=limiter,
                max_attempts=2,
            ),
        )
        == "retry_budget_exhausted"
    )
    assert [record.started_at for record in limiter.request_starts] == [0.0, 2.0]


@pytest.mark.parametrize(
    "retry_after",
    ["1", "Thu, 20 Aug 2026 12:00:01 GMT"],
)
def test_retry_after_never_weakens_the_global_two_second_interval(ds: Any, retry_after: str) -> None:
    replies = iter(
        [
            response(ds, {}, status=503, headers={"Retry-After": retry_after}),
            response(ds, {}),
        ]
    )
    clock = FakeClock()
    limiter = ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    ds.request_with_policy(lambda: next(replies), surface="api", limiter=limiter, max_attempts=2)
    assert [record.started_at for record in limiter.request_starts] == [0.0, 2.0]


def test_wrong_content_type_is_not_a_success_artifact(ds: Any) -> None:
    limiter = ds.GlobalRateLimiter()
    assert (
        error_code(
            ds,
            lambda: ds.request_with_policy(
                lambda: response(ds, {}, headers={"Content-Type": "text/html"}),
                surface="api",
                limiter=limiter,
            ),
        )
        == "content_type_invalid"
    )


def test_api_ssr_and_rss_share_the_same_30rpm_limiter(ds: Any) -> None:
    clock = FakeClock()
    limiter = ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    for surface in ("rss", "api", "ssr"):
        ds.request_with_policy(lambda: response(ds, {}), surface=surface, limiter=limiter)
    assert [(record.surface, record.started_at) for record in limiter.request_starts] == [
        ("rss", 0.0),
        ("api", 2.0),
        ("ssr", 4.0),
    ]
    ds.validate_request_schedule(limiter.request_starts)


def test_rate_interval_mutation_below_two_seconds_is_rejected(ds: Any) -> None:
    mutated = [
        ds.RequestStart(surface="api", started_at=0.0),
        ds.RequestStart(surface="ssr", started_at=1.999),
    ]
    assert error_code(ds, lambda: ds.validate_request_schedule(mutated)) == "rate_limit_violation"


def test_timeline_rule_at_and_beyond_72_hours(ds: Any) -> None:
    exactly = synthetic_item_record(
        ds,
        published_at="2026-08-15T08:00:00Z",
        discovered_at="2026-08-18T08:00:00Z",
    )
    beyond = synthetic_item_record(
        ds,
        item_id="fiction-item-beyond",
        published_at="2026-08-15T07:59:59Z",
        discovered_at="2026-08-18T08:00:00Z",
    )
    assert ds.timeline_key(exactly).isoformat() == "2026-08-18T08:00:00+00:00"
    assert ds.timeline_key(beyond).isoformat() == "2026-08-15T07:59:59+00:00"


def test_window_is_half_open_and_coverage_is_mandatory(ds: Any) -> None:
    before = synthetic_item_record(ds, item_id="before", discovered_at="2026-08-18T23:59:59Z")
    start = synthetic_item_record(ds, item_id="start", discovered_at="2026-08-19T00:00:00Z")
    end = synthetic_item_record(ds, item_id="end", discovered_at="2026-08-20T00:00:00Z")
    selected = ds.filter_window([end, before, start], start=WINDOW_TWO[0], end=WINDOW_TWO[1])
    assert [item.id for item in selected] == ["start"]
    ds.ensure_window_covered(
        start=WINDOW_TWO[0],
        end=WINDOW_TWO[1],
        first_response_date="Thu, 20 Aug 2026 12:00:00 GMT",
        last_response_date="Thu, 20 Aug 2026 12:05:00 GMT",
    )
    assert (
        error_code(
            ds,
            lambda: ds.ensure_window_covered(
                start="2026-08-13T00:00:00Z",
                end="2026-08-14T00:00:00Z",
                first_response_date="Thu, 20 Aug 2026 12:00:00 GMT",
                last_response_date="Thu, 20 Aug 2026 12:05:00 GMT",
            ),
        )
        == "window_out_of_coverage"
    )


def test_ssr_join_distinguishes_observed_empty_and_detail_fallback(ds: Any) -> None:
    first = ds.parse_api_item(synthetic_api_item())
    second = ds.parse_api_item(synthetic_api_item("fiction-item-beta"))
    result = ds.reconcile_tags(
        [first, second],
        [
            ds.TagObservation(
                item_id=first.id,
                channel="list",
                tags=[],
                observed_aihot_url=first.aihot_url,
            ),
            ds.TagObservation(
                item_id=second.id,
                channel="detail",
                tags=["fictional-detail-tag"],
                observed_aihot_url=second.aihot_url,
            ),
        ],
    )
    assert result.counts.model_dump() == {
        "api_target_item_id_count": 2,
        "normalized_item_record_count": 2,
        "ssr_list_matched_target_item_id_count": 1,
        "detail_fallback_target_item_id_count": 1,
        "explicit_empty_tags_target_item_id_count": 1,
        "missing_tag_observation_target_item_id_count": 0,
        "non_equivalent_tag_observation_target_item_id_count": 0,
        "api_ssr_identity_conflict_target_item_id_count": 0,
    }
    assert {item.id: item.tags for item in result.items} == {
        "fiction-item-alpha": [],
        "fiction-item-beta": ["fictional-detail-tag"],
    }


@pytest.mark.parametrize(
    ("observations", "expected_code"),
    [
        ([], "tag_observation_missing"),
        (
            [
                ("list", ["fictional-a"], "https://aihot.invalid/items/fiction-item-alpha"),
                ("detail", ["fictional-b"], "https://aihot.invalid/items/fiction-item-alpha"),
            ],
            "tag_observation_duplicate",
        ),
        (
            [("list", ["fictional-a"], "https://aihot.invalid/items/wrong-id")],
            "tag_identity_conflict",
        ),
    ],
)
def test_ssr_join_gap_states_fail_closed(ds: Any, observations: list[object], expected_code: str) -> None:
    item = ds.parse_api_item(synthetic_api_item())
    shaped = [
        ds.TagObservation(item_id=item.id, channel=channel, tags=tags, observed_aihot_url=url)
        for channel, tags, url in observations
    ]
    assert error_code(ds, lambda: ds.reconcile_tags([item], shaped)) == expected_code


def stability_pass(
    ds: Any,
    index: int,
    observed_at: str,
    first_window_ids: set[str],
    second_window_ids: set[str],
    *,
    terminal: bool = True,
) -> Any:
    return ds.PassObservation(
        index=index,
        terminal=terminal,
        first_response_date=observed_at,
        last_response_date=observed_at,
        target_ids_by_window=(frozenset(first_window_ids), frozenset(second_window_ids)),
    )


def test_stability_accepts_first_equal_pair(ds: Any) -> None:
    passes = [
        stability_pass(ds, 0, "Thu, 20 Aug 2026 12:00:00 GMT", {"a"}, {"b"}),
        stability_pass(ds, 1, "Thu, 20 Aug 2026 12:03:00 GMT", {"a"}, {"b"}),
    ]
    decision = ds.select_canonical_pass(passes)
    assert decision.accepted_pair == (0, 1)
    assert decision.canonical_pass_index == 1
    assert decision.target_hash_equal_by_window == (True, True)


def test_stability_accepts_second_pair_after_first_drift(ds: Any) -> None:
    passes = [
        stability_pass(ds, 0, "Thu, 20 Aug 2026 12:00:00 GMT", {"a"}, {"b"}),
        stability_pass(ds, 1, "Thu, 20 Aug 2026 12:03:00 GMT", {"a", "added"}, {"b"}),
        stability_pass(ds, 2, "Thu, 20 Aug 2026 12:06:00 GMT", {"a", "added"}, {"b"}),
    ]
    decision = ds.select_canonical_pass(passes)
    assert decision.accepted_pair == (1, 2)
    assert decision.canonical_pass_index == 2


def test_three_unstable_passes_fail_without_canonical(ds: Any) -> None:
    passes = [
        stability_pass(ds, 0, "Thu, 20 Aug 2026 12:00:00 GMT", {"a"}, {"b"}),
        stability_pass(ds, 1, "Thu, 20 Aug 2026 12:03:00 GMT", {"a", "x"}, {"b"}),
        stability_pass(ds, 2, "Thu, 20 Aug 2026 12:06:00 GMT", {"a", "x", "y"}, {"b"}),
    ]
    assert error_code(ds, lambda: ds.select_canonical_pass(passes)) == "capture_unstable"


def test_cross_midnight_pair_continues_to_stable_third_pass(ds: Any) -> None:
    passes = [
        stability_pass(ds, 0, "Thu, 20 Aug 2026 23:59:59 GMT", {"a"}, {"b"}),
        stability_pass(ds, 1, "Fri, 21 Aug 2026 00:00:01 GMT", {"a"}, {"b"}),
        stability_pass(ds, 2, "Fri, 21 Aug 2026 00:03:00 GMT", {"a"}, {"b"}),
    ]
    decision = ds.select_canonical_pass(passes)
    assert decision.accepted_pair == (1, 2)
    assert decision.canonical_pass_index == 2


def test_nonterminal_pass_can_never_be_accepted(ds: Any) -> None:
    passes = [
        stability_pass(ds, 0, "Thu, 20 Aug 2026 12:00:00 GMT", {"a"}, {"b"}),
        stability_pass(ds, 1, "Thu, 20 Aug 2026 12:03:00 GMT", {"a"}, {"b"}, terminal=False),
    ]
    assert error_code(ds, lambda: ds.select_canonical_pass(passes)) == "capture_unstable"


def test_capture_manifest_validates_identity_raw_hashes_and_canonical_chain(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    validated = ds.validate_capture_manifest(
        manifest,
        manifest_path=CAPTURE_PATH,
        raw_files=raw_files,
        schema_bytes=SCHEMA_PATH.read_bytes(),
    )
    assert validated.canonical_pass_index == 1
    assert len(validated.passes) == 2


@pytest.mark.parametrize(
    "mutation",
    ["legacy_items_page_only", "wrong_schema_version", "wrong_query"],
)
def test_persisted_capture_replay_rejects_api_envelope_mutations(
    ds: Any,
    mutation: str,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    replace_first_raw_api_page_envelope(
        ds,
        manifest,
        raw_files,
        mutation=mutation,
    )

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "page_invalid"
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("root_extra", "page_invalid"),
        ("query_missing", "page_invalid"),
        ("item_wrong_type", "item_invalid"),
        ("attribution_extra", "item_invalid"),
        ("page_wrong_type", "page_invalid"),
        ("nonterminal_cursor_missing", "cursor_missing"),
    ],
)
def test_persisted_capture_raw_replay_enforces_authoritative_api_shape(
    ds: Any,
    mutation: str,
    expected_code: str,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    mutate_first_persisted_raw_api_page(
        ds,
        manifest,
        raw_files,
        mutation=mutation,
    )
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == expected_code
    )


@pytest.mark.parametrize(
    ("rss_date", "openapi_date"),
    [
        ("Thu, 20 Aug 2026 12:00:00 GMT", "Thu, 20 Aug 2026 12:00:02 GMT"),
        ("Thu, 20 Aug 2026 12:00:00 GMT", "Thu, 20 Aug 2026 12:00:00 GMT"),
    ],
)
def test_capture_accepts_nondecreasing_public_response_dates(
    ds: Any,
    rss_date: str,
    openapi_date: str,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["public_responses"][0]["date"] = rss_date  # type: ignore[index]
    manifest["public_responses"][1]["date"] = openapi_date  # type: ignore[index]

    ds.validate_capture_manifest(
        manifest,
        manifest_path=CAPTURE_PATH,
        raw_files=raw_files,
        schema_bytes=SCHEMA_PATH.read_bytes(),
    )


def test_capture_rejects_decreasing_public_response_dates(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["public_responses"][0]["date"] = "Thu, 20 Aug 2026 12:00:02 GMT"  # type: ignore[index]
    manifest["public_responses"][1]["date"] = "Thu, 20 Aug 2026 12:00:00 GMT"  # type: ignore[index]

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_accepts_closed_canonical_url_discovery_provenance(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)

    validated = ds.validate_capture_manifest(
        manifest,
        manifest_path=CAPTURE_PATH,
        raw_files=raw_files,
        schema_bytes=SCHEMA_PATH.read_bytes(),
    )

    rss, openapi = validated.public_responses
    assert rss.request_url == "https://aihot.invalid/feed.xml"
    assert rss.canonical_url_provenance.model_dump(mode="json") == {
        "discovered_via_redirect_from_url": "https://aihot.invalid/rss",
        "discovered_via_redirect_status": 301,
    }
    assert openapi.request_url == "https://aihot.invalid/openapi-v1.json"
    assert openapi.canonical_url_provenance is None
    assert rss.date == "Thu, 20 Aug 2026 12:00:00 GMT"
    assert openapi.date == "Thu, 20 Aug 2026 12:00:02 GMT"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("rss_missing", "manifest_invalid"),
        ("rss_extra", "manifest_invalid"),
        ("rss_wrong_type", "manifest_invalid"),
        ("rss_wrong_redirect_path", "request_contract_mismatch"),
        ("rss_wrong_redirect_status", "manifest_invalid"),
        ("rss_null", "canonical_url_provenance_invalid"),
        ("openapi_missing", "manifest_invalid"),
        ("openapi_object", "canonical_url_provenance_invalid"),
    ],
)
def test_capture_rejects_canonical_url_discovery_provenance_mutations(
    ds: Any,
    mutation: str,
    expected_code: str,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    ds.validate_capture_manifest(
        manifest,
        manifest_path=CAPTURE_PATH,
        raw_files=raw_files,
        schema_bytes=SCHEMA_PATH.read_bytes(),
    )
    rss, openapi = manifest["public_responses"]  # type: ignore[misc]
    if mutation == "rss_missing":
        del rss["canonical_url_provenance"]
    elif mutation == "rss_extra":
        rss["canonical_url_provenance"]["future"] = "fictional"  # type: ignore[index]
    elif mutation == "rss_wrong_type":
        rss["canonical_url_provenance"] = "fictional"
    elif mutation == "rss_wrong_redirect_path":
        rss["canonical_url_provenance"][  # type: ignore[index]
            "discovered_via_redirect_from_url"
        ] = "https://aihot.invalid/not-rss"
    elif mutation == "rss_wrong_redirect_status":
        rss["canonical_url_provenance"][  # type: ignore[index]
            "discovered_via_redirect_status"
        ] = 302
    elif mutation == "rss_null":
        rss["canonical_url_provenance"] = None
    elif mutation == "openapi_missing":
        del openapi["canonical_url_provenance"]
    elif mutation == "openapi_object":
        openapi["canonical_url_provenance"] = copy.deepcopy(
            rss["canonical_url_provenance"]
        )
    else:
        raise AssertionError(f"unknown mutation: {mutation}")

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == expected_code
    )


def test_adr061_capture_shape_removes_same_payload_projections_and_binds_path(
    ds: Any,
) -> None:
    assert tuple(ds.SourceIdentity.model_fields) == ("base_url",)
    assert "canonical_request_url_projection" not in ds.RawPageReference.model_fields
    assert "first_response_date" not in ds.CapturePassManifest.model_fields
    assert "last_response_date" not in ds.CapturePassManifest.model_fields
    manifest, raw_files = synthetic_capture_bundle(ds)
    ds.validate_capture_manifest(
        manifest,
        manifest_path=CAPTURE_PATH,
        raw_files=raw_files,
        schema_bytes=SCHEMA_PATH.read_bytes(),
    )
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=f"captures/{CAPTURE_ID}/alias.json",
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "reference_invalid"
    )


@pytest.mark.parametrize(
    ("container", "old_name", "value"),
    [
        ("source", "first_public_response_observed_at", "2026-08-20T12:00:00Z"),
        ("source", "openapi_saved_public_response_body_sha256_projection", "a" * 64),
        ("raw_page", "canonical_request_url_projection", "https://aihot.invalid/api/v1/items"),
        ("capture_pass", "first_response_date", "Thu, 20 Aug 2026 12:00:00 GMT"),
        ("capture_pass", "last_response_date", "Thu, 20 Aug 2026 12:00:00 GMT"),
    ],
)
def test_adr061_capture_rejects_removed_projection_keys(
    ds: Any, container: str, old_name: str, value: object
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    if container == "source":
        manifest["source"][old_name] = value  # type: ignore[index]
    elif container == "raw_page":
        manifest["passes"][0]["raw_pages"][0][old_name] = value  # type: ignore[index]
    else:
        manifest["passes"][0][old_name] = value  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_and_window_v1_machine_semantics_are_literal_frozen(ds: Any) -> None:
    capture_semantics = ds.AIHOT_CAPTURE_MANIFEST_V1_SEMANTICS
    assert capture_semantics["type"] == "object"
    assert capture_semantics["additionalProperties"] is False
    assert tuple(capture_semantics["required"]) == (
        "artifact_type",
        "capture_id",
        "started_at",
        "finished_at",
        "source",
        "public_responses",
        "user_agent",
        "rate_policy",
        "tool",
        "schema",
        "canonical_pass_index",
        "passes",
    )
    capture_properties = capture_semantics["properties"]
    assert capture_properties["artifact_type"] == {
        "type": "string",
        "const": "aihot_capture_v1",
    }
    assert capture_properties["tool"]["properties"]["dirty"] == {
        "type": "boolean",
        "const": False,
    }
    assert capture_properties["passes"]["minItems"] == 2
    assert capture_properties["passes"]["maxItems"] == 3
    public_response_semantics = capture_properties["public_responses"]["items"]
    assert tuple(public_response_semantics["required"]) == (
        "surface",
        "request_url",
        "canonical_url_provenance",
        "raw_path",
        "compressed_raw_sha256",
        "response_body_sha256",
        "status",
        "content_type",
        "date",
        "etag",
        "cache_control",
    )
    assert public_response_semantics["properties"]["canonical_url_provenance"] == {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "discovered_via_redirect_from_url",
                    "discovered_via_redirect_status",
                ],
                "properties": {
                    "discovered_via_redirect_from_url": {
                        "type": "string",
                        "format": "http-uri",
                    },
                    "discovered_via_redirect_status": {
                        "type": "integer",
                        "const": 301,
                    },
                },
            },
            {"type": "null"},
        ]
    }
    pass_properties = capture_properties["passes"]["items"]["properties"]
    assert "index" not in pass_properties
    assert "first_response_date" not in pass_properties
    assert "last_response_date" not in pass_properties
    assert "canonical_request_url_projection" not in pass_properties["raw_pages"]["items"]["properties"]
    assert set(capture_properties["source"]["properties"]) == {"base_url"}
    assert tuple(pass_properties["formal_windows"]["items"]["required"]) == (
        "start_inclusive",
        "end_exclusive",
        "target_item_id_count",
        "target_item_id_sha256",
    )

    window_semantics = ds.AIHOT_WINDOW_MANIFEST_V1_SEMANTICS
    assert window_semantics["type"] == "object"
    assert window_semantics["additionalProperties"] is False
    assert tuple(window_semantics["required"]) == (
        "artifact_type",
        "window",
        "capture",
        "items",
        "canonical_pass_target_item_id_sha256_projection",
        "tag_observation_responses",
        "tag_observation_bindings",
        "tag_reconciliation_counts",
    )
    window_properties = window_semantics["properties"]
    assert "schema" not in window_properties
    assert "tool" not in window_properties
    assert tuple(window_properties["tag_observation_responses"]["items"]["required"]) == (
        "request_url",
        "response_raw_path",
        "compressed_raw_sha256",
        "response_body_sha256",
        "status",
        "content_type",
    )
    assert tuple(window_properties["tag_observation_bindings"]["items"]["required"]) == (
        "item_id",
        "response_raw_path",
    )
    assert tuple(window_properties["tag_reconciliation_counts"]["required"]) == (
        "api_target_item_id_count",
        "normalized_item_record_count",
        "ssr_list_matched_target_item_id_count",
        "detail_fallback_target_item_id_count",
        "explicit_empty_tags_target_item_id_count",
        "missing_tag_observation_target_item_id_count",
        "non_equivalent_tag_observation_target_item_id_count",
        "api_ssr_identity_conflict_target_item_id_count",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "required_missing",
        "extra_nested",
        "wrong_type",
        "range",
        "literal",
        "old_projection_name",
    ],
)
def test_capture_v1_semantic_drift_fails_without_changing_artifact_type(
    ds: Any, mutation: str
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    assert manifest["artifact_type"] == "aihot_capture_v1"
    if mutation == "required_missing":
        del manifest["user_agent"]
    elif mutation == "extra_nested":
        manifest["source"]["future"] = "forbidden"  # type: ignore[index]
    elif mutation == "wrong_type":
        manifest["canonical_pass_index"] = "1"
    elif mutation == "range":
        manifest["canonical_pass_index"] = -1
    elif mutation == "literal":
        manifest["tool"]["dirty"] = True  # type: ignore[index]
    else:
        reference = manifest["passes"][0]["raw_pages"][0]  # type: ignore[index]
        reference["canonical_request_url_projection"] = "https://aihot.invalid/api/v1/items"
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


@pytest.mark.parametrize(
    "mutation",
    ["required_missing", "extra_nested", "wrong_type", "range", "literal", "old_projection_name"],
)
def test_window_v1_semantic_drift_fails_without_changing_artifact_type(
    ds: Any, mutation: str
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    assert window["artifact_type"] == "aihot_window_v1"
    if mutation == "required_missing":
        del window["items"]
    elif mutation == "extra_nested":
        window["window"]["future"] = "forbidden"  # type: ignore[index]
    elif mutation == "wrong_type":
        window["tag_reconciliation_counts"]["normalized_item_record_count"] = 1.0  # type: ignore[index]
    elif mutation == "range":
        window["tag_reconciliation_counts"]["api_target_item_id_count"] = -1  # type: ignore[index]
    elif mutation == "literal":
        window["window"]["time_basis"] = "aihot_timeline_v2"  # type: ignore[index]
    else:
        window["target_id_sha256"] = window.pop(
            "canonical_pass_target_item_id_sha256_projection"
        )
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_capture_rejects_legacy_request_cursor_extra_key(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    legacy_reference = manifest["passes"][0]["raw_pages"][0]  # type: ignore[index]
    legacy_reference["request_cursor"] = None
    with pytest.raises(ValidationError):
        ds.RawPageReference.model_validate(legacy_reference)
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_rejects_canonical_query_cursor_drift_as_invalid_reference(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["passes"][0]["raw_pages"][0]["canonical_query"]["cursor"] = "fictional-drift"  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "reference_invalid"
    )


def test_capture_rejects_request_url_cursor_drift_from_canonical_authority(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    reference = manifest["passes"][0]["raw_pages"][0]  # type: ignore[index]
    reference["canonical_request_url_projection"] = "https://aihot.invalid/api/v1/items?cursor=fictional-drift"
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_replay_rejects_repeated_canonical_cursor(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    item_ids = [
        "fiction-repeat-alpha",
        "fiction-repeat-beta",
        "fiction-repeat-gamma",
        "fiction-repeat-delta",
    ]
    items = [
        synthetic_api_item(item_ids[0], discovered_at="2026-08-18T06:00:00Z"),
        synthetic_api_item(item_ids[1], discovered_at="2026-08-18T07:00:00Z"),
        synthetic_api_item(item_ids[2], discovered_at="2026-08-19T06:00:00Z"),
        synthetic_api_item(item_ids[3], discovered_at="2026-08-19T07:00:00Z"),
    ]
    cursors = [None, "fiction-cursor-a", "fiction-cursor-b", "fiction-cursor-a"]
    next_cursors = ["fiction-cursor-a", "fiction-cursor-b", "fiction-cursor-a", None]
    for pass_index in (0, 1):
        raw_pages: list[dict[str, object]] = []
        for page_index, (item, cursor, next_cursor) in enumerate(zip(items, cursors, next_cursors, strict=True)):
            page, path, compressed = raw_page_file(
                ds,
                pass_index=pass_index,
                page_index=page_index,
                payload=page_payload([item], has_more=next_cursor is not None, next_cursor=next_cursor),
                canonical_cursor=cursor,
            )
            raw_files[path] = compressed
            raw_pages.append(page)
        manifest["passes"][pass_index] = pass_manifest(  # type: ignore[index]
            ds,
            window_one_ids=item_ids[:2],
            window_two_ids=item_ids[2:],
            raw_pages=raw_pages,
        )

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "cursor_repeated"
    )


def test_capture_replay_rejects_formal_window_outside_common_coverage(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    install_multipage_capture(ds, manifest, raw_files)
    late_date = "Wed, 26 Aug 2026 12:00:00 GMT"
    for capture_pass in manifest["passes"]:  # type: ignore[index]
        capture_pass["raw_pages"][-1]["date"] = late_date

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "window_out_of_coverage"
    )


def test_capture_rejects_decreasing_api_response_dates(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    install_multipage_capture(ds, manifest, raw_files)
    first_date = "Thu, 20 Aug 2026 12:00:02 GMT"
    last_date = "Thu, 20 Aug 2026 12:00:00 GMT"
    for capture_pass in manifest["passes"]:  # type: ignore[index]
        capture_pass["raw_pages"][0]["date"] = first_date
        capture_pass["raw_pages"][1]["date"] = last_date

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_rejects_finished_at_before_started_at(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["finished_at"] = "2026-08-20T11:59:59Z"

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_rejects_formal_window_target_count_drift(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["passes"][0]["formal_windows"][0]["target_item_id_count"] = 2  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "target_projection_mismatch"
    )


def test_capture_rejects_formal_window_target_hash_drift(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["passes"][0]["formal_windows"][0]["target_item_id_sha256"] = "f" * 64  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "target_projection_mismatch"
    )


def test_capture_rejects_source_base_that_disagrees_with_public_evidence(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["source"]["base_url"] = "https://other.invalid"  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "identity_anchor_mismatch"
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://aihot.invalid/forged",
        "https://aihot.invalid/?forged=1",
        "https://aihot.invalid/#forged",
    ],
)
def test_capture_rejects_source_base_that_is_not_canonical_origin(ds: Any, base_url: str) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["source"]["base_url"] = base_url  # type: ignore[index]

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_rejects_request_host_outside_source_base(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["public_responses"][0]["request_url"] = "https://other.invalid/feed.xml"  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "identity_anchor_mismatch"
    )


@pytest.mark.parametrize(
    ("reference_kind", "wrong_url"),
    [
        ("rss", "https://aihot.invalid/rss"),
        ("openapi", "https://aihot.invalid/not-openapi.json"),
    ],
)
def test_capture_rejects_same_origin_wrong_request_path(
    ds: Any, reference_kind: str, wrong_url: str
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    if reference_kind == "rss":
        manifest["public_responses"][0]["request_url"] = wrong_url  # type: ignore[index]
    elif reference_kind == "openapi":
        manifest["public_responses"][1]["request_url"] = wrong_url  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "request_contract_mismatch"
    )


def test_capture_rejects_canonical_item_host_outside_source_base(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    replace_first_api_item_field(ds, manifest, raw_files, ("links", "aihot"), "https://other.invalid/items/fiction-item-alpha")
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "identity_anchor_mismatch"
    )


@pytest.mark.parametrize(
    "wrong_url",
    [
        "https://aihot.invalid/not-items/fiction-item-alpha",
        "https://aihot.invalid/items/fiction-item-alpha?forged=1",
        "https://aihot.invalid/items/fiction-item-alpha#forged",
    ],
)
def test_capture_rejects_canonical_item_wrong_path_query_or_fragment(
    ds: Any, wrong_url: str
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    replace_first_api_item_field(ds, manifest, raw_files, ("links", "aihot"), wrong_url)
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "item_invalid"
    )


def test_window_replay_rejects_coforged_same_origin_item_path(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    item_id = "fiction-item-alpha"
    canonical_url = f"https://aihot.invalid/items/{item_id}"
    forged_url = f"https://aihot.invalid/not-items/{item_id}"
    replace_first_api_item_field(ds, capture, raw_files, ("links", "aihot"), forged_url)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)

    items_path = window["items"]["path"]  # type: ignore[index]
    normalized = json.loads(files[items_path])
    normalized["aihot_url"] = forged_url
    normalized_bytes = ds.canonical_json_bytes(normalized) + b"\n"
    files[items_path] = normalized_bytes
    window["items"]["sha256"] = ds.sha256_hex(normalized_bytes)  # type: ignore[index]

    ssr_reference = window["tag_observation_responses"][0]  # type: ignore[index]
    ssr_path = ssr_reference["response_raw_path"]
    ssr_body = gzip.decompress(raw_files[ssr_path]).replace(
        canonical_url.encode(),
        forged_url.encode(),
    )
    ssr_raw = ds.deterministic_gzip(ssr_body)
    raw_files[ssr_path] = ssr_raw
    ssr_reference["compressed_raw_sha256"] = ds.sha256_hex(ssr_raw)
    ssr_reference["response_body_sha256"] = ds.sha256_hex(ssr_body)

    files[CAPTURE_PATH] = ds.canonical_json_bytes(capture)
    window["capture"]["sha256"] = ds.sha256_hex(files[CAPTURE_PATH])  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "item_invalid"
    )


def test_openapi_identity_digest_is_grounded_in_public_response_bytes(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["public_responses"][1]["response_body_sha256"] = "f" * 64  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "raw_body_hash_mismatch"
    )


@pytest.mark.parametrize(
    ("surface", "body"),
    [
        ("rss", b"<html><body>Synthetic login page</body></html>"),
        ("openapi", b"{}"),
    ],
)
def test_capture_rejects_public_probe_body_without_surface_semantics(
    ds: Any,
    surface: str,
    body: bytes,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    replace_public_response_body(ds, manifest, raw_files, surface=surface, body=body)

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "public_surface_invalid"
    )


@pytest.mark.parametrize(
    ("surface", "wrong_content_type"),
    [("rss", "application/json"), ("openapi", "text/html; charset=utf-8")],
)
def test_capture_rejects_public_probe_content_type_for_wrong_surface(
    ds: Any,
    surface: str,
    wrong_content_type: str,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    reference = next(
        candidate for candidate in manifest["public_responses"] if candidate["surface"] == surface  # type: ignore[union-attr]
    )
    reference["content_type"] = wrong_content_type

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "content_type_invalid"
    )


@pytest.mark.parametrize("field", ["etag", "cache_control"])
@pytest.mark.parametrize("blank", ["", " \t "])
@pytest.mark.parametrize("reference_kind", ["api", "public"])
def test_capture_rejects_blank_present_response_header(
    ds: Any,
    field: str,
    blank: str,
    reference_kind: str,
) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    if reference_kind == "api":
        manifest["passes"][0]["raw_pages"][0][field] = blank  # type: ignore[index]
    else:
        manifest["public_responses"][0][field] = blank  # type: ignore[index]

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_rejects_source_observed_at_drift_from_first_public_date(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["source"]["first_public_response_observed_at"] = "2026-08-20T12:00:01Z"  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


@pytest.mark.parametrize(
    "missing_path",
    [("source", "base_url")],
)
def test_capture_identity_anchor_is_required(ds: Any, missing_path: tuple[str, str]) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    del manifest[missing_path[0]][missing_path[1]]  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "identity_anchor_missing"
    )


@pytest.mark.parametrize("forbidden_key", ["release", "deploy", "config", "database_snapshot_id"])
def test_internal_provenance_is_forbidden(ds: Any, forbidden_key: str) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest[forbidden_key] = "synthetic-but-forbidden"
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "forbidden_provenance"
    )


def test_flipped_raw_byte_breaks_capture_validation(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    path = next(iter(raw_files))
    mutated = bytearray(raw_files[path])
    mutated[-1] ^= 1
    raw_files[path] = bytes(mutated)
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "raw_hash_mismatch"
    )


def test_unstable_pass_cannot_be_marked_canonical(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["canonical_pass_index"] = 0
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "capture_unstable"
    )


def test_capture_rejects_superseded_topology_fields(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["accepted_pair"] = [0, 1]
    manifest["pass_count"] = 2
    manifest["passes"][0]["index"] = 0  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                manifest,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == "manifest_invalid"
    )


def test_capture_manifest_does_not_coerce_contract_types(ds: Any) -> None:
    manifest, raw_files = synthetic_capture_bundle(ds)
    manifest["canonical_pass_index"] = "1"
    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(manifest, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes()),
        )
        == "manifest_invalid"
    )


def test_window_projection_validates_reference_hashes_counts_and_items(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    ds.validate_capture_manifest(capture, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes())
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    items = ds.validate_window_projection(
        window,
        manifest_path=WINDOW_MANIFEST_PATH,
        capture_manifest=capture,
        files=files,
        raw_files=raw_files,
    )
    assert [item.id for item in items] == ["fiction-item-alpha"]


def test_persisted_window_replay_reopens_canonical_manifest_and_item_bytes(
    ds: Any, tmp_path: Path
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    files[WINDOW_MANIFEST_PATH] = ds.canonical_json_bytes(window)
    artifacts = merged_artifacts(files, raw_files, ssr_raw_files)
    materialize_artifacts(tmp_path, artifacts)

    reopened = ds.load_persisted_artifacts(tmp_path, sorted(artifacts))
    reopened_capture = ds.load_canonical_json_object(
        reopened[CAPTURE_PATH], artifact_name="capture.json"
    )
    reopened_window = ds.load_canonical_json_object(
        reopened[WINDOW_MANIFEST_PATH], artifact_name="window manifest.json"
    )
    items = ds.validate_window_projection(
        reopened_window,
        manifest_path=WINDOW_MANIFEST_PATH,
        capture_manifest=reopened_capture,
        files=reopened,
        raw_files=reopened,
    )
    assert [item.id for item in items] == ["fiction-item-alpha"]
    assert reopened[WINDOW_MANIFEST_PATH] == ds.canonical_json_bytes(reopened_window)
    assert reopened[CAPTURE_PATH] == ds.canonical_json_bytes(reopened_capture)


def reopened_report_subject_artifacts(
    ds: Any,
    root: Path,
    *,
    subject_artifact_type: str,
) -> tuple[str, dict[str, bytes]]:
    capture, raw_files = synthetic_capture_bundle(ds)
    files: dict[str, bytes] = {
        CAPTURE_PATH: ds.canonical_json_bytes(capture),
        SCHEMA_RELATIVE_PATH: SCHEMA_PATH.read_bytes(),
    }
    subject_path = CAPTURE_PATH
    artifacts = merged_artifacts(files, raw_files)
    if subject_artifact_type == "aihot_window_v1":
        window, window_files, ssr_raw_files = synthetic_window_bundle(ds, capture)
        subject_path = WINDOW_MANIFEST_PATH
        window_files[subject_path] = ds.canonical_json_bytes(window)
        artifacts = merged_artifacts(raw_files, window_files, ssr_raw_files)
    materialize_artifacts(root, artifacts)
    return subject_path, ds.load_persisted_artifacts(root, sorted(artifacts))


@pytest.mark.parametrize("subject_artifact_type", ["aihot_capture_v1", "aihot_window_v1"])
def test_persisted_subject_report_is_derived_reopened_and_grounded(
    ds: Any,
    tmp_path: Path,
    subject_artifact_type: str,
) -> None:
    root = tmp_path / subject_artifact_type
    subject_path, reopened = reopened_report_subject_artifacts(
        ds,
        root,
        subject_artifact_type=subject_artifact_type,
    )
    report = ds.build_validation_report_v1(
        subject_path=subject_path,
        files=reopened,
        raw_files=reopened,
    )
    report_path = f"reports/{subject_artifact_type}.json"
    report_bytes = ds.canonical_json_bytes(report)
    materialize_artifacts(root, {report_path: report_bytes})
    reopened_report_bytes = ds.load_persisted_artifacts(root, [report_path])[report_path]
    reopened_report = ds.load_validation_report_v1(reopened_report_bytes)
    assert reopened_report["subject"] == {
        "artifact_type": subject_artifact_type,
        "path": subject_path,
        "sha256": ds.sha256_hex(reopened[subject_path]),
    }
    if subject_artifact_type == "aihot_capture_v1":
        assert reopened_report["window_validation"] is None
        assert reopened_report["integrity"]["window_manifest"] == "not_applicable_for_capture_subject"  # type: ignore[index]
    else:
        assert reopened_report["window_validation"]["pairing_strategy"] == {  # type: ignore[index]
            "primary": "original_url",
            "assistance": "original_title",
            "fallback": "aihot_title",
        }
    assert (
        ds.validate_grounded_validation_report_v1(
            reopened_report,
            subject_path=subject_path,
            files=reopened,
            raw_files=reopened,
        )
        == reopened_report
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "subject",
        "path",
        "hash",
        "identity",
        "stability",
        "window",
        "count",
        "coverage",
    ],
)
def test_grounded_window_report_rejects_persisted_subject_and_projection_drift(
    ds: Any,
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / mutation
    subject_path, reopened = reopened_report_subject_artifacts(
        ds,
        root,
        subject_artifact_type="aihot_window_v1",
    )
    report = ds.build_validation_report_v1(
        subject_path=subject_path,
        files=reopened,
        raw_files=reopened,
    )
    if mutation == "subject":
        report["subject"] = {
            "artifact_type": "aihot_capture_v1",
            "path": CAPTURE_PATH,
            "sha256": ds.sha256_hex(reopened[CAPTURE_PATH]),
        }
        report["window_validation"] = None
        for key in ("window_manifest", "items_jsonl", "tag_reconciliation"):
            report["integrity"][key] = "not_applicable_for_capture_subject"  # type: ignore[index]
    elif mutation == "path":
        report["subject"]["path"] = f"windows/{'f' * 8}/manifest.json"  # type: ignore[index]
    elif mutation == "hash":
        report["subject"]["sha256"] = "f" * 64  # type: ignore[index]
    elif mutation == "identity":
        report["identity"]["source_base_url"] = "https://other.invalid"  # type: ignore[index]
    elif mutation == "stability":
        report["stability"]["formal_window_target_item_id_sha256_comparisons"].reverse()  # type: ignore[index]
    elif mutation == "window":
        report["window_validation"]["window"] = {  # type: ignore[index]
            "start_inclusive": WINDOW_TWO[0],
            "end_exclusive": WINDOW_TWO[1],
            "time_basis": "aihot_timeline_v1",
        }
    elif mutation == "count":
        validation = report["window_validation"]
        validation["target_item_id_count"] = 2  # type: ignore[index]
        counts = validation["tag_reconciliation_counts"]  # type: ignore[index]
        counts["api_target_item_id_count"] = 2
        counts["normalized_item_record_count"] = 2
        counts["ssr_list_matched_target_item_id_count"] = 2
        pairing = validation["pairing"]  # type: ignore[index]
        for key in pairing:
            pairing[key] = 2
        coverage = validation["field_coverage"]  # type: ignore[index]
        for field_name, leaf in coverage.items():
            leaf["present_item_record_count"] = 2
            if field_name == "tags":
                leaf["observed_item_record_count"] = 2
    else:
        report["window_validation"]["field_coverage"]["aihot_summary"][  # type: ignore[index]
            "non_null_item_record_count"
        ] = 0
    ds.validate_validation_report_v1_contract_shape(report)
    assert (
        error_code(
            ds,
            lambda: ds.validate_grounded_validation_report_v1(
                report,
                subject_path=subject_path,
                files=reopened,
                raw_files=reopened,
            ),
        )
        == "report_grounding_mismatch"
    )


@pytest.mark.parametrize(
    "invalid_bytes",
    [b'{"z": 1, "a": 2}', b"[]", b'{"value":NaN}'],
)
def test_canonical_json_object_loader_rejects_noncanonical_or_nonobject_bytes(
    ds: Any, invalid_bytes: bytes
) -> None:
    assert (
        error_code(
            ds,
            lambda: ds.load_canonical_json_object(
                invalid_bytes,
                artifact_name="synthetic manifest.json",
            ),
        )
        == "manifest_invalid"
    )


def test_persisted_replay_rejects_reopened_normalized_item_mutation(
    ds: Any, tmp_path: Path
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    items_path = window["items"]["path"]  # type: ignore[index]
    item_payload = json.loads(files[items_path])
    item_payload["aihot_title"] = "Persisted but raw-inconsistent fictional title"
    files[items_path] = ds.canonical_json_bytes(item_payload) + b"\n"
    window["items"]["sha256"] = ds.sha256_hex(files[items_path])  # type: ignore[index]
    files[WINDOW_MANIFEST_PATH] = ds.canonical_json_bytes(window)
    artifacts = merged_artifacts(files, raw_files, ssr_raw_files)
    materialize_artifacts(tmp_path, artifacts)

    reopened = ds.load_persisted_artifacts(tmp_path, sorted(artifacts))
    reopened_capture = ds.load_canonical_json_object(
        reopened[CAPTURE_PATH], artifact_name="capture.json"
    )
    reopened_window = ds.load_canonical_json_object(
        reopened[WINDOW_MANIFEST_PATH], artifact_name="window manifest.json"
    )
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                reopened_window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=reopened_capture,
                files=reopened,
                raw_files=reopened,
            ),
        )
        == "window_integrity_failed"
    )


def test_two_persisted_windows_from_one_capture_reopen_without_path_collision(
    ds: Any, tmp_path: Path
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    first_window, first_files, first_ssr = synthetic_window_bundle(
        ds, capture, window_bounds=WINDOW_ONE
    )
    second_window, second_files, second_ssr = synthetic_window_bundle(
        ds, capture, window_bounds=WINDOW_TWO
    )
    first_manifest_path = f"{synthetic_window_root(WINDOW_ONE)}/manifest.json"
    second_manifest_path = f"{synthetic_window_root(WINDOW_TWO)}/manifest.json"
    first_files[first_manifest_path] = ds.canonical_json_bytes(first_window)
    second_files[second_manifest_path] = ds.canonical_json_bytes(second_window)
    artifacts = merged_artifacts(
        raw_files,
        first_files,
        second_files,
        first_ssr,
        second_ssr,
    )
    materialize_artifacts(tmp_path, artifacts)

    reopened = ds.load_persisted_artifacts(tmp_path, sorted(artifacts))
    reopened_capture = ds.load_canonical_json_object(
        reopened[CAPTURE_PATH], artifact_name="capture.json"
    )
    validated: list[tuple[str, tuple[str, str], list[str]]] = []
    for manifest_path in (first_manifest_path, second_manifest_path):
        reopened_window = ds.load_canonical_json_object(
            reopened[manifest_path], artifact_name="window manifest.json"
        )
        items = ds.validate_window_projection(
            reopened_window,
            manifest_path=manifest_path,
            capture_manifest=reopened_capture,
            files=reopened,
            raw_files=reopened,
        )
        validated.append(
            (
                manifest_path,
                (
                    reopened_window["window"]["start_inclusive"],
                    reopened_window["window"]["end_exclusive"],
                ),
                [item.id for item in items],
            )
        )

    assert validated == [
        (first_manifest_path, WINDOW_ONE, ["fiction-item-alpha"]),
        (second_manifest_path, WINDOW_TWO, ["fiction-item-beta"]),
    ]
    assert first_manifest_path != second_manifest_path
    assert first_window["items"]["path"] != second_window["items"]["path"]  # type: ignore[index]

    assert (
        error_code(
            ds,
            lambda: ds.load_persisted_artifacts(
                tmp_path,
                [first_manifest_path, first_manifest_path],
            ),
        )
        == "artifact_path_collision"
    )


def test_window_row_schema_rejects_ftp_even_if_runtime_url_helper_is_relaxed(
    ds: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds, "_http_url", lambda value, *, field_name: value)
    capture, raw_files = synthetic_capture_bundle(ds)
    forged_original_url = "ftp://origin.invalid/articles/fiction-item-alpha"
    replace_first_api_item_field(
        ds,
        capture,
        raw_files,
        ("links", "original"),
        forged_original_url,
    )
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    items_path = window["items"]["path"]  # type: ignore[index]
    persisted_item = json.loads(files[items_path])
    persisted_item["original_url"] = forged_original_url
    files[items_path] = ds.canonical_json_bytes(persisted_item) + b"\n"
    window["items"]["sha256"] = ds.sha256_hex(files[items_path])  # type: ignore[index]

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_multipage_capture_replays_shared_list_and_detail_ssr_into_one_window(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    item_ids = install_multipage_capture(ds, capture, raw_files)
    ds.validate_capture_manifest(capture, manifest_path=CAPTURE_PATH, raw_files=raw_files, schema_bytes=SCHEMA_PATH.read_bytes())
    window, files, _ = synthetic_window_bundle(ds, capture, item_ids=item_ids)
    raw_files.update(install_mixed_shared_ssr_observations(ds, window, item_ids=item_ids))

    items = ds.validate_window_projection(
        window,
        manifest_path=WINDOW_MANIFEST_PATH,
        capture_manifest=capture,
        files=files,
        raw_files=raw_files,
    )

    assert [item.id for item in items] == item_ids
    assert [item.tags for item in items] == [["fictional-observed-tag"]] * 3
    assert window["tag_reconciliation_counts"]["ssr_list_matched_target_item_id_count"] == 2  # type: ignore[index]
    assert window["tag_reconciliation_counts"]["detail_fallback_target_item_id_count"] == 1  # type: ignore[index]
    list_paths = [reference["response_raw_path"] for reference in window["tag_observation_bindings"][:2]]  # type: ignore[index]
    assert list_paths == [list_paths[0], list_paths[0]]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("drop_second_page", "terminal_missing"),
        ("reorder_pages", "reference_invalid"),
        ("raw_next_cursor_drift", "reference_invalid"),
    ],
)
def test_multipage_capture_rejects_page_chain_mutations(
    ds: Any,
    mutation: str,
    expected_code: str,
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    install_multipage_capture(ds, capture, raw_files)
    first_pass = capture["passes"][0]  # type: ignore[index]
    if mutation == "drop_second_page":
        first_pass["raw_pages"].pop()
    elif mutation == "reorder_pages":
        first_pass["raw_pages"].reverse()
    else:
        first_reference = first_pass["raw_pages"][0]
        path = first_reference["raw_path"]
        payload = json.loads(gzip.decompress(raw_files[path]))
        payload["page"]["nextCursor"] = "fiction-drifted-page-two"
        body = ds.canonical_json_bytes(payload)
        compressed = ds.deterministic_gzip(body)
        raw_files[path] = compressed
        first_reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
        first_reference["response_body_sha256"] = ds.sha256_hex(body)

    assert (
        error_code(
            ds,
            lambda: ds.validate_capture_manifest(
                capture,
                manifest_path=CAPTURE_PATH,
                raw_files=raw_files,
                schema_bytes=SCHEMA_PATH.read_bytes(),
            ),
        )
        == expected_code
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("reference_item_id", "tag_observation_reference_invalid"),
        ("shared_article_identity", "tag_observation_missing"),
    ],
)
def test_shared_list_ssr_replay_rejects_join_identity_mutations(
    ds: Any, mutation: str, expected_code: str
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    item_ids = install_multipage_capture(ds, capture, raw_files)
    window, files, _ = synthetic_window_bundle(ds, capture, item_ids=item_ids)
    raw_files.update(install_mixed_shared_ssr_observations(ds, window, item_ids=item_ids))
    first_reference = window["tag_observation_responses"][0]  # type: ignore[index]
    if mutation == "reference_item_id":
        window["tag_observation_bindings"][0]["item_id"] = "fiction-item-not-in-shared-list"  # type: ignore[index]
    else:
        path = first_reference["response_raw_path"]
        body = gzip.decompress(raw_files[path]).replace(
            b"fiction-item-alpha",
            b"fiction-item-wrong",
        )
        compressed = ds.deterministic_gzip(body)
        raw_files[path] = compressed
        first_reference["compressed_raw_sha256"] = ds.sha256_hex(compressed)
        first_reference["response_body_sha256"] = ds.sha256_hex(body)

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == expected_code
    )


def test_adr061_shared_ssr_response_is_stored_once_and_bound_to_multiple_items(
    ds: Any,
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    item_ids = install_multipage_capture(ds, capture, raw_files)
    window, files, _ = synthetic_window_bundle(ds, capture, item_ids=item_ids)
    raw_files.update(install_mixed_shared_ssr_observations(ds, window, item_ids=item_ids))
    responses = window["tag_observation_responses"]
    bindings = window["tag_observation_bindings"]
    assert isinstance(responses, list)
    assert isinstance(bindings, list)
    assert len(responses) == 2
    assert len(bindings) == 3
    assert bindings[0]["response_raw_path"] == bindings[1]["response_raw_path"]
    assert [
        item.id
        for item in ds.validate_window_projection(
            window,
            manifest_path=WINDOW_MANIFEST_PATH,
            capture_manifest=capture,
            files=files,
            raw_files=raw_files,
        )
    ] == item_ids


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("orphan_response", "tag_observation_reference_invalid"),
        ("duplicate_response", "tag_observation_reference_invalid"),
        ("missing_binding", "tag_observation_reference_invalid"),
        ("duplicate_binding", "tag_observation_reference_invalid"),
        ("dangling_binding", "tag_observation_reference_invalid"),
        ("bound_item_missing", "tag_observation_missing"),
        ("bound_item_duplicate", "tag_observation_duplicate"),
    ],
)
def test_adr061_shared_ssr_cross_field_relationships_fail_closed(
    ds: Any, mutation: str, expected_code: str
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    item_ids = install_multipage_capture(ds, capture, raw_files)
    window, files, _ = synthetic_window_bundle(ds, capture, item_ids=item_ids)
    raw_files.update(install_mixed_shared_ssr_observations(ds, window, item_ids=item_ids))
    responses = window["tag_observation_responses"]
    bindings = window["tag_observation_bindings"]
    assert isinstance(responses, list)
    assert isinstance(bindings, list)
    first_response = responses[0]
    first_path = first_response["response_raw_path"]
    assert isinstance(first_path, str)

    if mutation == "orphan_response":
        orphan_path = f"captures/{CAPTURE_ID}/raw/ssr/orphan-list.html.gz"
        orphan = dict(first_response)
        orphan["request_url"] = "https://aihot.invalid/all?page=2"
        orphan["response_raw_path"] = orphan_path
        raw_files[orphan_path] = raw_files[first_path]
        responses.append(orphan)
    elif mutation == "duplicate_response":
        responses.append(copy.deepcopy(first_response))
    elif mutation == "missing_binding":
        bindings.pop()
    elif mutation == "duplicate_binding":
        bindings.append(copy.deepcopy(bindings[0]))
    elif mutation == "dangling_binding":
        bindings[0]["response_raw_path"] = f"captures/{CAPTURE_ID}/raw/ssr/missing.html.gz"
    else:
        body = gzip.decompress(raw_files[first_path])
        if mutation == "bound_item_missing":
            body = body.replace(b"fiction-item-alpha", b"fiction-item-absent")
        else:
            duplicate_article = (
                b'<article class="timeline-item" data-aihot-id="fiction-item-alpha" '
                b'data-aihot-url="https://aihot.invalid/items/fiction-item-alpha">'
                b'<span class="topic-tag">fictional-observed-tag</span></article>'
            )
            body = body.replace(b"</body>", duplicate_article + b"</body>")
        compressed = ds.deterministic_gzip(body)
        raw_files[first_path] = compressed
        first_response["compressed_raw_sha256"] = ds.sha256_hex(compressed)
        first_response["response_body_sha256"] = ds.sha256_hex(body)

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == expected_code
    )


@pytest.mark.parametrize("mutation", ["drop", "reorder", "target_hash"])
def test_two_item_window_replay_rejects_record_set_mutations(ds: Any, mutation: str) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    move_all_capture_items_into_window_one(ds, capture, raw_files)
    item_ids = ["fiction-item-alpha", "fiction-item-beta"]
    window, files, ssr_raw_files = synthetic_window_bundle(
        ds,
        capture,
        item_ids=item_ids,
    )
    raw_files.update(ssr_raw_files)
    assert [
        item.id
        for item in ds.validate_window_projection(
            window,
            manifest_path=WINDOW_MANIFEST_PATH,
            capture_manifest=capture,
            files=files,
            raw_files=raw_files,
        )
    ] == item_ids

    items_path = window["items"]["path"]  # type: ignore[index]
    if mutation == "drop":
        mutated_items = files[items_path].splitlines(keepends=True)[:1]
        files[items_path] = b"".join(mutated_items)
        window["items"]["sha256"] = ds.sha256_hex(files[items_path])  # type: ignore[index]
        window["tag_reconciliation_counts"]["normalized_item_record_count"] = 1  # type: ignore[index]
    elif mutation == "reorder":
        mutated_items = list(reversed(files[items_path].splitlines(keepends=True)))
        files[items_path] = b"".join(mutated_items)
        window["items"]["sha256"] = ds.sha256_hex(files[items_path])  # type: ignore[index]
    else:
        window["canonical_pass_target_item_id_sha256_projection"] = "f" * 64

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_has_no_caller_supplied_tag_authority(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    forged = ds.TagObservation(
        item_id="fiction-item-alpha",
        channel="list",
        tags=["caller-forged-tag"],
        observed_aihot_url="https://aihot.invalid/items/fiction-item-alpha",
    )
    with pytest.raises(TypeError):
        ds.validate_window_projection(
            window,
            manifest_path=WINDOW_MANIFEST_PATH,
            capture_manifest=capture,
            files=files,
            raw_files=raw_files,
            raw_tag_observations=[forged],
        )


@pytest.mark.parametrize("suffix", ["?page=1", "#synthetic-fragment"])
def test_window_projection_rejects_detail_request_url_query_or_fragment(
    ds: Any, suffix: str
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture, channel="detail")
    raw_files.update(ssr_raw_files)
    reference = window["tag_observation_responses"][0]  # type: ignore[index]
    reference["request_url"] = f'{reference["request_url"]}{suffix}'
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "reference_invalid"
    )


@pytest.mark.parametrize("alias_path", ["/all/", "/all//"])
def test_window_projection_rejects_list_request_path_alias(
    ds: Any,
    alias_path: str,
) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    reference = window["tag_observation_responses"][0]  # type: ignore[index]
    reference["request_url"] = f"https://aihot.invalid{alias_path}?page=1"

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "reference_invalid"
    )


@pytest.mark.parametrize(
    "wrong_url",
    [
        "https://aihot.invalid/all",
        "https://aihot.invalid/all?page=0",
        "https://aihot.invalid/all?page=51",
        "https://aihot.invalid/all?page=01",
        "https://aihot.invalid/all?page=1&extra=forged",
        "https://aihot.invalid/all?page=1#synthetic-fragment",
        "https://aihot.invalid/all;forged?page=1",
    ],
)
def test_window_projection_rejects_noncanonical_list_request_url(ds: Any, wrong_url: str) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    reference = window["tag_observation_responses"][0]  # type: ignore[index]
    reference["request_url"] = wrong_url
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "reference_invalid"
    )


def test_window_projection_accepts_last_canonical_list_page(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    reference = window["tag_observation_responses"][0]  # type: ignore[index]
    reference["request_url"] = "https://aihot.invalid/all?page=50"
    ds.validate_window_projection(
        window,
        manifest_path=WINDOW_MANIFEST_PATH,
        capture_manifest=capture,
        files=files,
        raw_files=raw_files,
    )


def test_window_projection_replays_tag_values_from_stored_ssr_raw(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    reference = window["tag_observation_responses"][0]  # type: ignore[index]
    raw_path = reference["response_raw_path"]
    mutated_body = gzip.decompress(raw_files[raw_path]).replace(
        b"fictional-observed-tag",
        b"raw-authority-mutated-tag",
    )
    mutated_raw = ds.deterministic_gzip(mutated_body)
    raw_files[raw_path] = mutated_raw
    reference["compressed_raw_sha256"] = ds.sha256_hex(mutated_raw)
    reference["response_body_sha256"] = ds.sha256_hex(mutated_body)
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_deleted_api_item_breaks_window_projection(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    items_path = window["items"]["path"]  # type: ignore[index]
    files[items_path] = b""
    window["items"]["sha256"] = ds.sha256_hex(b"")  # type: ignore[index]
    window["tag_reconciliation_counts"]["normalized_item_record_count"] = 0  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_rejects_tag_join_id_mutation(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    items_path = window["items"]["path"]  # type: ignore[index]
    mutated_item = synthetic_item_record(ds, "fiction-item-wrong-join", tags=["fictional-observed-tag"])
    mutated_bytes = ds.serialize_items_jsonl([mutated_item])
    files[items_path] = mutated_bytes
    window["items"]["sha256"] = ds.sha256_hex(mutated_bytes)  # type: ignore[index]
    window["canonical_pass_target_item_id_sha256_projection"] = id_digest(
        ds, ["fiction-item-alpha"]
    )
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_rejects_items_outside_window_directory(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    original_path = window["items"]["path"]  # type: ignore[index]
    mutated_path = "captures/synthetic-wrong-directory/items.jsonl"
    files[mutated_path] = files.pop(original_path)
    window["items"]["path"] = mutated_path  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "reference_invalid"
    )


def test_window_projection_rejects_inexact_window_directory(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    original_path = window["items"]["path"]  # type: ignore[index]
    mutated_path = "windows/other/items.jsonl"
    files[mutated_path] = files.pop(original_path)
    window["items"]["path"] = mutated_path  # type: ignore[index]

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "reference_invalid"
    )


def test_window_projection_rejects_manifest_path_outside_exact_window_root(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)

    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path="windows/other/manifest.json",
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "reference_invalid"
    )


def test_window_projection_revalidates_capture_stability(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    capture["canonical_pass_index"] = 0
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "capture_unstable"
    )


def test_window_projection_rejects_api_authority_title_mutation(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(
        ds,
        capture,
        item_updates={"aihot_title": "Fabricated normalized title"},
    )
    raw_files.update(ssr_raw_files)
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_rejects_tag_mutation_against_raw_observation(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    items_path = window["items"]["path"]  # type: ignore[index]
    mutated_item = synthetic_item_record(ds, tags=["fabricated-normalized-tag"])
    mutated_bytes = ds.serialize_items_jsonl([mutated_item])
    files[items_path] = mutated_bytes
    window["items"]["sha256"] = ds.sha256_hex(mutated_bytes)  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_preserves_explicit_nullable_api_reason(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    replace_first_api_item_field(ds, capture, raw_files, ("reason",), None)
    window, files, ssr_raw_files = synthetic_window_bundle(
        ds,
        capture,
        item_updates={"aihot_recommendation_reason": None},
    )
    raw_files.update(ssr_raw_files)
    items = ds.validate_window_projection(
        window,
        manifest_path=WINDOW_MANIFEST_PATH,
        capture_manifest=capture,
        files=files,
        raw_files=raw_files,
    )
    assert items[0].aihot_recommendation_reason is None


def test_window_projection_rejects_list_detail_count_mutation(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture)
    raw_files.update(ssr_raw_files)
    window["tag_reconciliation_counts"]["ssr_list_matched_target_item_id_count"] = 0  # type: ignore[index]
    window["tag_reconciliation_counts"]["detail_fallback_target_item_id_count"] = 1  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_rejects_explicit_empty_count_mutation(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    window, files, ssr_raw_files = synthetic_window_bundle(ds, capture, tags=[])
    raw_files.update(ssr_raw_files)
    window["tag_reconciliation_counts"]["explicit_empty_tags_target_item_id_count"] = 0  # type: ignore[index]
    assert (
        error_code(
            ds,
            lambda: ds.validate_window_projection(
                window,
                manifest_path=WINDOW_MANIFEST_PATH,
                capture_manifest=capture,
                files=files,
                raw_files=raw_files,
            ),
        )
        == "window_integrity_failed"
    )


def test_window_projection_rejects_extra_schema_key(ds: Any) -> None:
    item = synthetic_item_record(ds).model_dump(mode="json")
    item["schema_mutation"] = True
    with pytest.raises(ValidationError):
        ds.AihotItem.model_validate(item)


def test_offline_slice_is_sorted_byte_identical_and_never_uses_transport(ds: Any) -> None:
    records = [
        synthetic_item_record(
            ds,
            "fiction-item-beta",
            tags=["fictional-tag"],
            discovered_at="2026-08-18T09:00:00Z",
        ),
        synthetic_item_record(
            ds,
            "fiction-item-alpha",
            tags=[],
            discovered_at="2026-08-18T08:00:00Z",
        ),
    ]
    first = ds.slice_offline(
        records,
        start=WINDOW_ONE[0],
        end=WINDOW_ONE[1],
        network_transport=FailIfNetworkTransport(),
    )
    second = ds.slice_offline(
        list(reversed(records)),
        start=WINDOW_ONE[0],
        end=WINDOW_ONE[1],
        network_transport=FailIfNetworkTransport(),
    )
    assert first == second
    assert first.endswith(b"\n")
    assert [json.loads(line)["id"] for line in first.splitlines()] == [
        "fiction-item-alpha",
        "fiction-item-beta",
    ]


def test_json_and_gzip_serialization_is_deterministic(ds: Any) -> None:
    payload = {"z": "fictional", "a": [3, 2, 1]}
    first_json = ds.canonical_json_bytes(payload)
    second_json = ds.canonical_json_bytes(copy.deepcopy(payload))
    assert first_json == second_json == b'{"a":[3,2,1],"z":"fictional"}'
    assert ds.deterministic_gzip(first_json) == ds.deterministic_gzip(second_json)
    assert hashlib.sha256(ds.deterministic_gzip(first_json)).hexdigest() == ds.sha256_hex(
        ds.deterministic_gzip(first_json)
    )


def test_synthetic_contract_contains_no_live_aihot_content() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden_hosts = [
        "aihot" + ".virxact.com",
        "https://" + "aihot.ai",
        "www." + "aihot.ai",
    ]
    assert all(host not in source for host in forbidden_hosts)
    assert ".invalid" in source


def test_generated_synthetic_artifacts_keep_all_upstream_authorities_fictional(ds: Any) -> None:
    capture, raw_files = synthetic_capture_bundle(ds)
    move_all_capture_items_into_window_one(ds, capture, raw_files)
    item_ids = ["fiction-item-alpha", "fiction-item-beta"]
    window, files, ssr_raw_files = synthetic_window_bundle(
        ds,
        capture,
        item_ids=item_ids,
    )
    raw_files.update(ssr_raw_files)
    items = ds.validate_window_projection(
        window,
        manifest_path=WINDOW_MANIFEST_PATH,
        capture_manifest=capture,
        files=files,
        raw_files=raw_files,
    )
    assert [item.id for item in items] == item_ids

    artifact_bytes = {
        "capture.json": ds.canonical_json_bytes(capture),
        "window.json": ds.canonical_json_bytes(window),
        "items.jsonl": files[window["items"]["path"]],  # type: ignore[index]
    }
    assert all(b"fiction" in payload.lower() or b"synthetic" in payload.lower() for payload in artifact_bytes.values())
    decompressed_raw = {path: gzip.decompress(payload) for path, payload in raw_files.items()}
    assert all(
        b"fiction" in body.lower() or b"synthetic" in body.lower()
        for body in decompressed_raw.values()
    )

    upstream_urls = [capture["source"]["base_url"]]  # type: ignore[index]
    upstream_urls.extend(reference["request_url"] for reference in capture["public_responses"])  # type: ignore[index]
    for capture_pass in capture["passes"]:  # type: ignore[index]
        upstream_urls.extend(
            f'{capture["source"]["base_url"]}/api/v1/items'
            for _reference in capture_pass["raw_pages"]
        )
        page_path = capture_pass["raw_pages"][0]["raw_path"]
        page_payload_value = json.loads(decompressed_raw[page_path])
        for api_item in page_payload_value["items"]:
            upstream_urls.extend(api_item["links"].values())
    upstream_urls.extend(reference["request_url"] for reference in window["tag_observation_responses"])
    for line in artifact_bytes["items.jsonl"].splitlines():
        item_payload = json.loads(line)
        upstream_urls.extend([item_payload["aihot_url"], item_payload["original_url"]])
    for path, body in decompressed_raw.items():
        if "/raw/ssr/" in path:
            upstream_urls.extend(
                match.decode()
                for match in re.findall(rb'https://[^"\s<]+', body)
            )

    assert len(upstream_urls) >= 20
    assert all(
        (urlparse(url).hostname or "").endswith(".invalid")
        for url in upstream_urls
    )


class FakeGitProvider:
    def __init__(
        self,
        *,
        tool_root: Path,
        output_root: Path,
        commit: str = "c" * 40,
        dirty: bool = False,
        output_repo_root: Path | None = None,
    ) -> None:
        self.tool_root = tool_root.resolve()
        self.output_root = output_root.resolve()
        self.commit = commit
        self.dirty = dirty
        self.output_repo_root = (output_repo_root or output_root).resolve()

    def checkout(self, path: Path) -> object:
        resolved = path.resolve()
        if resolved == self.tool_root:
            return dataset_module.GitCheckout(
                root=self.tool_root,
                commit=self.commit,
                dirty=self.dirty,
            )
        if resolved == self.output_root:
            return dataset_module.GitCheckout(
                root=self.output_repo_root,
                commit="d" * 40,
                dirty=False,
            )
        raise AssertionError(f"unexpected Git checkout lookup: {resolved}")


class FakeCaptureTransport:
    def __init__(
        self,
        ds: Any,
        *,
        clock: FakeClock,
        output_root: Path,
        capture_id: str,
        pass_item_ids: list[list[str]] | None = None,
        retry_ssr: bool = True,
    ) -> None:
        self.ds = ds
        self.clock = clock
        self.output_root = output_root
        self.capture_id = capture_id
        self.pass_item_ids = pass_item_ids or [
            ["fiction-item-alpha", "fiction-item-beta"],
            ["fiction-item-alpha", "fiction-item-beta"],
        ]
        self.retry_ssr = retry_ssr
        self.calls: list[dict[str, object]] = []
        self.api_page_index = 0
        self.list_page_one_attempts = 0
        self.detail_attempts = 0

    def _headers(self, content_type: str, *, date_offset: int) -> dict[str, str]:
        return {
            "Content-Type": content_type,
            "Date": f"Thu, 20 Aug 2026 12:00:{date_offset:02d} GMT",
            "ETag": '"fictional-etag"',
            "Cache-Control": "public, max-age=60",
        }

    def get(
        self,
        url: str,
        *,
        params: dict[str, object] | None,
        headers: dict[str, str],
    ) -> object:
        parsed = urlparse(url)
        self.calls.append(
            {
                "url": url,
                "path": parsed.path,
                "params": dict(params or {}),
                "started_at": self.clock.monotonic(),
                "user_agent": headers.get("User-Agent"),
            }
        )
        call_index = len(self.calls) - 1
        if parsed.path == "/feed.xml":
            assert params in (None, {})
            return self.ds.HttpResponse(
                status=200,
                headers=self._headers("application/rss+xml; charset=utf-8", date_offset=0),
                body=b"<rss><channel><title>Fictional Capture Feed</title></channel></rss>",
            )
        if parsed.path == "/openapi-v1.json":
            staged_rss = (
                self.output_root
                / ".staging"
                / self.capture_id
                / "captures"
                / self.capture_id
                / "raw"
                / "probes"
                / "00-rss.xml.gz"
            )
            assert staged_rss.is_file(), "RSS raw must be persisted before the OpenAPI request starts"
            return self.ds.HttpResponse(
                status=200,
                headers=self._headers("application/json; charset=utf-8", date_offset=2),
                body=self.ds.canonical_json_bytes(
                    {"openapi": "3.1.0", "info": {"title": "Fictional Capture API"}}
                ),
            )
        if parsed.path == "/api/v1/items":
            assert params is not None
            pass_index, page_index = divmod(self.api_page_index, 2)
            self.api_page_index += 1
            assert pass_index < len(self.pass_item_ids)
            expected_params: dict[str, object] = {
                "mode": "all",
                "by": "timeline",
                "window": "7d",
                "limit": 100,
            }
            if page_index > 0:
                expected_params["cursor"] = f"fiction-cursor-{pass_index}"
            assert params == expected_params
            item_id = self.pass_item_ids[pass_index][page_index]
            on_second_day = item_id != "fiction-item-alpha"
            item = synthetic_api_item(
                item_id,
                published_at=(
                    "2026-08-19T07:00:00Z" if on_second_day else "2026-08-18T07:00:00Z"
                ),
                discovered_at=(
                    "2026-08-19T08:00:00Z" if on_second_day else "2026-08-18T08:00:00Z"
                ),
            )
            return response(
                self.ds,
                page_payload(
                    [item],
                    has_more=page_index == 0,
                    next_cursor=f"fiction-cursor-{pass_index}" if page_index == 0 else None,
                ),
                headers={"Date": f"Thu, 20 Aug 2026 12:00:{4 + call_index:02d} GMT"},
            )
        if parsed.path == "/all":
            assert params is not None
            page = params["page"]
            if page == 1:
                self.list_page_one_attempts += 1
                if self.retry_ssr and self.list_page_one_attempts == 1:
                    return self.ds.HttpResponse(
                        status=429,
                        headers={"Retry-After": "5", "Date": "Thu, 20 Aug 2026 12:01:00 GMT"},
                        body=b"",
                    )
                body = (
                    b'<html><body><article class="timeline-item" '
                    b'data-aihot-id="fiction-item-alpha" '
                    b'data-aihot-url="https://aihot.invalid/items/fiction-item-alpha">'
                    b'<span class="topic-tag">fictional-list-tag</span></article></body></html>'
                )
            else:
                body = b"<html><body></body></html>"
            return self.ds.HttpResponse(
                status=200,
                headers=self._headers("text/html; charset=utf-8", date_offset=20),
                body=body,
            )
        if parsed.path.startswith("/items/"):
            item_id = parsed.path.removeprefix("/items/")
            self.detail_attempts += 1
            if self.retry_ssr and self.detail_attempts == 1:
                return self.ds.HttpResponse(
                    status=503,
                    headers={"Retry-After": "6", "Date": "Thu, 20 Aug 2026 12:02:00 GMT"},
                    body=b"",
                )
            body = (
                f'<html><body><article class="timeline-item" data-aihot-id="{item_id}" '
                f'data-aihot-url="https://aihot.invalid/items/{item_id}">'
                '<span class="topic-tag">fictional-detail-tag</span></article></body></html>'
            ).encode()
            return self.ds.HttpResponse(
                status=200,
                headers=self._headers("text/html; charset=utf-8", date_offset=30),
                body=body,
            )
        raise AssertionError(f"unexpected synthetic request: {url}")


def make_capture_writer(
    ds: Any,
    tmp_path: Path,
    *,
    pass_item_ids: list[list[str]] | None = None,
    dirty: bool = False,
    output_repo_root: Path | None = None,
    retry_ssr: bool = True,
) -> tuple[object, FakeCaptureTransport, FakeClock, Path, Path]:
    tool_root = tmp_path / "tool"
    output_root = tmp_path / "data"
    initialize_fictional_git_repo(tool_root)
    if dirty:
        (tool_root / "tracked-fiction.txt").write_text("dirty fictional content\n", encoding="utf-8")
    output_git_root = (output_repo_root or output_root).resolve()
    initialize_fictional_git_repo(output_git_root, create_commit=False)
    output_root.mkdir(parents=True, exist_ok=True)
    clock = FakeClock()
    capture_id = "fictional-capture-phase2"
    transport = FakeCaptureTransport(
        ds,
        clock=clock,
        output_root=output_root,
        capture_id=capture_id,
        pass_item_ids=pass_item_ids,
        retry_ssr=retry_ssr,
    )
    writer = ds.CaptureWriter(
        tool_repo_root=tool_root,
        output_root=output_root,
        base_url="https://aihot.invalid",
        user_agent="AI-Radar-Fictional-Phase2-Test/1.0",
        transport=transport,
        limiter=ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep),
        now=lambda: datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        capture_id=capture_id,
        schema_bytes=SCHEMA_PATH.read_bytes(),
    )
    return writer, transport, clock, tool_root, output_root


def test_capture_writer_runs_full_fake_transport_pipeline_and_offline_replay(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(ds, tmp_path)

    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])

    assert [call["path"] for call in transport.calls[:2]] == ["/feed.xml", "/openapi-v1.json"]
    assert all(call["path"] != "/rss" for call in transport.calls)
    assert transport.api_page_index == 4
    assert transport.list_page_one_attempts == 2
    assert transport.detail_attempts == 2
    assert [
        call["params"]["page"]  # type: ignore[index]
        for call in transport.calls
        if call["path"] == "/all"
    ] == [1, 1, *range(2, 51)]
    starts = [float(call["started_at"]) for call in transport.calls]
    assert all(current - previous >= 2.0 for previous, current in zip(starts, starts[1:], strict=False))
    list_one_starts = [
        float(call["started_at"])
        for call in transport.calls
        if call["path"] == "/all" and call["params"] == {"page": 1}
    ]
    detail_starts = [
        float(call["started_at"])
        for call in transport.calls
        if str(call["path"]).startswith("/items/")
    ]
    assert list_one_starts[1] - list_one_starts[0] >= 5.0
    assert detail_starts[1] - detail_starts[0] >= 6.0
    assert result.capture_path == "captures/fictional-capture-phase2/capture.json"
    assert result.window_manifest_paths == (
        f"{synthetic_window_root(WINDOW_ONE)}/manifest.json",
        f"{synthetic_window_root(WINDOW_TWO)}/manifest.json",
    )
    capture_payload = json.loads((output_root / result.capture_path).read_bytes())
    rss_reference, openapi_reference = capture_payload["public_responses"]
    assert rss_reference["request_url"] == "https://aihot.invalid/feed.xml"
    assert rss_reference["canonical_url_provenance"] == {
        "discovered_via_redirect_from_url": "https://aihot.invalid/rss",
        "discovered_via_redirect_status": 301,
    }
    assert openapi_reference["request_url"] == "https://aihot.invalid/openapi-v1.json"
    assert openapi_reference["canonical_url_provenance"] is None
    assert not (output_root / ".staging" / "fictional-capture-phase2").exists()

    capture_report = ds.validate_persisted_artifact(output_root, result.capture_path)
    window_reports = [
        ds.validate_persisted_artifact(output_root, path)
        for path in result.window_manifest_paths
    ]
    assert capture_report["result"] == "pass"
    assert capture_report["stability"]["pass_count"] == 2  # type: ignore[index]
    assert [report["window_validation"]["target_item_id_count"] for report in window_reports] == [1, 1]  # type: ignore[index]
    assert window_reports[0]["window_validation"]["tag_reconciliation_counts"] == {  # type: ignore[index]
        "api_target_item_id_count": 1,
        "normalized_item_record_count": 1,
        "ssr_list_matched_target_item_id_count": 1,
        "detail_fallback_target_item_id_count": 0,
        "explicit_empty_tags_target_item_id_count": 0,
        "missing_tag_observation_target_item_id_count": 0,
        "non_equivalent_tag_observation_target_item_id_count": 0,
        "api_ssr_identity_conflict_target_item_id_count": 0,
    }
    assert window_reports[1]["window_validation"]["tag_reconciliation_counts"][  # type: ignore[index]
        "detail_fallback_target_item_id_count"
    ] == 1
    sliced = ds.slice_persisted_capture(
        output_root,
        capture_path=result.capture_path,
        start=WINDOW_ONE[0],
        end=WINDOW_ONE[1],
        network_transport=FailIfNetworkTransport(),
    )
    persisted_items = (
        output_root / synthetic_window_root(WINDOW_ONE) / "items.jsonl"
    ).read_bytes()
    assert sliced == persisted_items


def test_capture_writer_sends_minimal_request_and_requires_normalized_response_query(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(ds, tmp_path)

    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])

    api_calls = [call for call in transport.calls if call["path"] == "/api/v1/items"]
    assert [call["params"] for call in api_calls] == [
        {"mode": "all", "by": "timeline", "window": "7d", "limit": 100},
        {
            "mode": "all",
            "by": "timeline",
            "window": "7d",
            "limit": 100,
            "cursor": "fiction-cursor-0",
        },
        {"mode": "all", "by": "timeline", "window": "7d", "limit": 100},
        {
            "mode": "all",
            "by": "timeline",
            "window": "7d",
            "limit": 100,
            "cursor": "fiction-cursor-1",
        },
    ]
    manifest = json.loads((output_root / result.capture_path).read_bytes())
    for capture_pass in manifest["passes"]:
        for page_reference in capture_pass["raw_pages"]:
            raw_path = output_root / page_reference["raw_path"]
            response_payload = json.loads(gzip.decompress(raw_path.read_bytes()))
            assert response_payload["query"] == ds.AIHOT_CAPTURE_QUERY_V1


def test_capture_writer_reads_clean_git_provenance_and_refuses_dirty_checkout(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, _transport, _clock, tool_root, output_root = make_capture_writer(ds, tmp_path)
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    manifest = json.loads((output_root / result.capture_path).read_bytes())
    actual_head = initialize_fictional_git_repo(tool_root)
    assert manifest["tool"] == {"commit": actual_head, "dirty": False}

    dirty_writer, dirty_transport, _clock, _tool_root, dirty_output = make_capture_writer(
        ds,
        tmp_path / "dirty",
        dirty=True,
    )
    assert (
        error_code(
            ds,
            lambda: dirty_writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]),
        )
        == "tool_checkout_dirty"
    )
    assert dirty_transport.calls == []
    assert not (dirty_output / "captures").exists()
    assert not (dirty_output / "windows").exists()
    assert not (dirty_output / ".staging").exists()


def initialize_fictional_git_repo(path: Path, *, create_commit: bool = True) -> str | None:
    path.mkdir(parents=True, exist_ok=True)
    if (path / ".git").is_dir():
        if not create_commit:
            return None
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Fictional Test Author"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "fictional@example.invalid"],
        check=True,
    )
    if not create_commit:
        return None
    (path / "tracked-fiction.txt").write_text("fictional tracked content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked-fiction.txt"], check=True)
    git_environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-08-20T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-08-20T12:00:00Z",
    }
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "test: fictional provenance"],
        check=True,
        env=git_environment,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_capture_writer_uses_actual_tool_checkout_head_not_provider_claim(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, _transport, _clock, tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    actual_head = initialize_fictional_git_repo(tool_root)

    with pytest.raises(TypeError, match="git_provider"):
        ds.CaptureWriter(
            tool_repo_root=tool_root,
            output_root=output_root,
            base_url=writer.base_url,
            user_agent=writer.user_agent,
            transport=writer.transport,
            git_provider=FakeGitProvider(tool_root=tool_root, output_root=output_root),
            limiter=writer.limiter,
            now=writer.now,
            capture_id=writer.capture_id,
            schema_bytes=writer.schema_bytes,
        )

    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    manifest = json.loads((output_root / result.capture_path).read_bytes())

    assert manifest["tool"] == {"commit": actual_head, "dirty": False}


def test_capture_writer_rejects_actual_dirty_tool_checkout_despite_clean_provider_claim(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, transport, _clock, tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    initialize_fictional_git_repo(tool_root)
    (tool_root / "tracked-fiction.txt").write_text("dirty fictional content\n", encoding="utf-8")

    assert (
        error_code(ds, lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]))
        == "tool_checkout_dirty"
    )
    assert transport.calls == []
    assert not (output_root / "captures").exists()
    assert not (output_root / "windows").exists()
    assert not (output_root / ".staging").exists()


def test_phase3_direct_negatives_cover_new_leaf_guards(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert error_code(ds, lambda: ds.SubprocessGitProvider().checkout(tmp_path / "not-a-repo")) == "git_checkout_invalid"

    git_results = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=f"{tmp_path}\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=f"{'a' * 40}\n", stderr=""),
            subprocess.CompletedProcess([], 1, stdout="", stderr="fictional status failure"),
        ]
    )
    monkeypatch.setattr(ds.subprocess, "run", lambda *_args, **_kwargs: next(git_results))
    assert error_code(ds, lambda: ds.SubprocessGitProvider().checkout(tmp_path)) == "git_checkout_invalid"
    monkeypatch.undo()

    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        ds.HttpxTransport(timeout_seconds=0, user_agent="Fictional-UA/1.0")
    empty_response = ds.HttpResponse(status=200, headers={}, body=b"")
    assert error_code(ds, lambda: ds._required_header(empty_response, "Date")) == "identity_anchor_missing"
    blank_response = ds.HttpResponse(status=200, headers={"ETag": "   "}, body=b"")
    assert error_code(ds, lambda: ds._optional_non_empty_header(blank_response, "ETag")) == "identity_anchor_missing"

    staging_root = tmp_path / "staging"
    staged_target = staging_root / "fictional.json"
    staged_target.parent.mkdir(parents=True)
    staged_target.write_bytes(b"preserve")
    assert error_code(ds, lambda: ds._write_staged(staging_root, "fictional.json", b"replacement")) == "target_exists"
    assert staged_target.read_bytes() == b"preserve"

    outside = tmp_path / "outside"
    allowed = tmp_path / "allowed"
    outside.mkdir()
    allowed.mkdir()
    assert error_code(ds, lambda: ds._remove_exact_tree(outside, allowed_parent=allowed)) == "cleanup_refused"
    assert outside.is_dir()


@pytest.mark.parametrize("failure", ["tool_root", "unborn_head"])
def test_phase3_direct_negatives_cover_capture_preflight_identity_guards(
    ds: Any,
    tmp_path: Path,
    failure: str,
) -> None:
    writer, transport, _clock, tool_root, _output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    if failure == "tool_root":
        nested = tool_root / "nested"
        nested.mkdir()
        writer.tool_repo_root = nested.resolve()
        expected_code = "tool_checkout_invalid"
    else:
        unborn = tmp_path / "unborn-tool"
        initialize_fictional_git_repo(unborn, create_commit=False)
        writer.tool_repo_root = unborn.resolve()
        expected_code = "tool_commit_invalid"

    assert error_code(ds, lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])) == expected_code
    assert transport.calls == []


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("list_duplicate", "tag_observation_duplicate"),
        ("detail_missing", "tag_observation_missing"),
        ("detail_duplicate", "tag_observation_duplicate"),
        ("no_decision", "capture_unstable"),
        ("wrong_window", "window_invalid"),
        ("existing_window", "target_exists"),
    ],
)
def test_phase3_direct_negatives_cover_capture_pipeline_guards(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: str,
) -> None:
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    real_get = transport.get

    def guarded_get(
        url: str,
        *,
        params: dict[str, object] | None,
        headers: dict[str, str],
    ) -> object:
        result = real_get(url, params=params, headers=headers)
        parsed = urlparse(url)
        if failure == "list_duplicate" and parsed.path == "/all" and params == {"page": 2}:
            body = (
                b'<article class="timeline-item" data-aihot-id="fiction-item-alpha" '
                b'data-aihot-url="https://aihot.invalid/items/fiction-item-alpha"></article>'
            )
            return ds.HttpResponse(status=200, headers=result.headers, body=body)
        if parsed.path.startswith("/items/") and failure in {"detail_missing", "detail_duplicate"}:
            if failure == "detail_missing":
                body = b"<html><body></body></html>"
            else:
                item_id = parsed.path.removeprefix("/items/")
                article = (
                    f'<article class="timeline-item" data-aihot-id="{item_id}" '
                    f'data-aihot-url="https://aihot.invalid/items/{item_id}"></article>'
                )
                body = f"<html><body>{article}{article}</body></html>".encode()
            return ds.HttpResponse(status=200, headers=result.headers, body=body)
        return result

    transport.get = guarded_get  # type: ignore[method-assign]
    if failure == "no_decision":
        monkeypatch.setattr(ds, "select_canonical_pass", lambda _observations: None)
    if failure == "existing_window":
        output_root.joinpath(*Path(synthetic_window_root(WINDOW_ONE)).parts).mkdir(parents=True)

    start, end = (WINDOW_ONE[0], WINDOW_ONE[1]) if failure == "wrong_window" else (WINDOW_ONE[0], WINDOW_TWO[1])
    assert error_code(ds, lambda: writer.capture(start=start, end=end)) == expected_code
    assert not (output_root / "captures" / "fictional-capture-phase2").exists()


def test_phase3_direct_negative_rejects_persisted_symlink_escape(ds: Any, tmp_path: Path) -> None:
    output_root = tmp_path / "data"
    captures = output_root / "captures"
    captures.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    (captures / "escaped.json").symlink_to(outside)

    assert error_code(ds, lambda: ds._persisted_artifact_maps(output_root)) == "reference_invalid"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("invalid_capture", "manifest_invalid"),
        ("duplicate_formal_window", "window_missing"),
        ("invalid_window", "window_integrity_failed"),
        ("outside_normalized_coverage", "window_out_of_coverage"),
        ("duplicate_item", "item_duplicate"),
    ],
)
def test_phase3_direct_negatives_cover_persisted_slice_guards(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: str,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    start, end = WINDOW_ONE

    if failure == "invalid_capture":
        (output_root / result.capture_path).write_bytes(ds.canonical_json_bytes({}))
    elif failure == "duplicate_formal_window":
        fake_window = SimpleNamespace(
            start_inclusive=WINDOW_ONE[0],
            end_exclusive=WINDOW_ONE[1],
        )
        fake_pass = SimpleNamespace(
            raw_pages=[
                SimpleNamespace(date="Thu, 20 Aug 2026 12:00:00 GMT"),
                SimpleNamespace(date="Thu, 20 Aug 2026 12:00:10 GMT"),
            ],
            formal_windows=[fake_window, fake_window],
        )
        fake_capture = SimpleNamespace(passes=[fake_pass], canonical_pass_index=0)
        monkeypatch.setattr(ds, "_load_report_capture", lambda *_args, **_kwargs: ({}, fake_capture))
    elif failure == "invalid_window":
        manifest_path = output_root / result.window_manifest_paths[0]
        manifest_path.write_bytes(ds.canonical_json_bytes({}))
    elif failure == "outside_normalized_coverage":
        start, end = "2026-08-17T00:00:00Z", "2026-08-18T00:00:00Z"
    else:
        duplicate = synthetic_item_record(ds)
        monkeypatch.setattr(ds, "validate_window_projection", lambda *_args, **_kwargs: [duplicate])

    assert (
        error_code(
            ds,
            lambda: ds.slice_persisted_capture(
                output_root,
                capture_path=result.capture_path,
                start=start,
                end=end,
                network_transport=FailIfNetworkTransport(),
            ),
        )
        == expected_code
    )


def test_capture_writer_refuses_non_repo_root_and_existing_capture(
    ds: Any,
    tmp_path: Path,
) -> None:
    wrong_root = tmp_path / "different-data-root"
    wrong_root.mkdir()
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path / "wrong-root",
        output_repo_root=wrong_root,
    )
    assert error_code(ds, lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])) == "output_root_invalid"
    assert transport.calls == []

    overwrite_writer, overwrite_transport, _clock, _tool_root, overwrite_root = make_capture_writer(
        ds,
        tmp_path / "overwrite",
    )
    capture_target = overwrite_root / "captures" / "fictional-capture-phase2"
    capture_target.mkdir(parents=True)
    assert (
        error_code(
            ds,
            lambda: overwrite_writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]),
        )
        == "target_exists"
    )
    assert overwrite_transport.calls == []


def test_capture_writer_uses_third_pass_then_fails_closed_when_still_unstable(
    ds: Any,
    tmp_path: Path,
) -> None:
    stable_third_writer, stable_transport, _clock, _tool_root, stable_root = make_capture_writer(
        ds,
        tmp_path / "stable-third",
        pass_item_ids=[
            ["fiction-item-alpha", "fiction-item-beta"],
            ["fiction-item-alpha", "fiction-item-gamma"],
            ["fiction-item-alpha", "fiction-item-gamma"],
        ],
        retry_ssr=False,
    )
    result = stable_third_writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    report = ds.validate_persisted_artifact(stable_root, result.capture_path)
    assert report["stability"]["pass_count"] == 3  # type: ignore[index]
    assert report["stability"]["canonical_pass_index"] == 2  # type: ignore[index]
    assert stable_transport.api_page_index == 6

    unstable_writer, unstable_transport, _clock, _tool_root, unstable_root = make_capture_writer(
        ds,
        tmp_path / "unstable",
        pass_item_ids=[
            ["fiction-item-alpha", "fiction-item-beta"],
            ["fiction-item-alpha", "fiction-item-gamma"],
            ["fiction-item-alpha", "fiction-item-delta"],
        ],
        retry_ssr=False,
    )
    assert (
        error_code(
            ds,
            lambda: unstable_writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]),
        )
        == "capture_unstable"
    )
    assert unstable_transport.api_page_index == 6
    assert not (unstable_root / "windows").exists()
    assert not (unstable_root / "captures" / "fictional-capture-phase2").exists()
    assert not (unstable_root / ".staging" / "fictional-capture-phase2").exists()


def test_capture_writer_validates_staged_artifacts_before_publish(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(ds, tmp_path)
    real_validator = ds.validate_capture_manifest
    calls = 0

    def rejecting_validator(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        real_validator(*args, **kwargs)
        raise ds.DatasetContractError("synthetic_staged_rejection", "fictional staged mutation")

    monkeypatch.setattr(ds, "validate_capture_manifest", rejecting_validator)
    assert (
        error_code(
            ds,
            lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]),
        )
        == "synthetic_staged_rejection"
    )
    assert calls == 1
    assert not (output_root / "captures" / "fictional-capture-phase2").exists()
    assert not (output_root / "windows").exists()
    assert not (output_root / ".staging" / "fictional-capture-phase2").exists()


def test_capture_writer_invokes_both_staged_validators_before_successful_publish(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, _transport, _clock, _tool_root, _output_root = make_capture_writer(ds, tmp_path)
    real_capture_validator = ds.validate_capture_manifest
    real_window_validator = ds.validate_window_projection
    call_order: list[str] = []

    def capture_validator(*args: object, **kwargs: object) -> object:
        call_order.append("capture")
        return real_capture_validator(*args, **kwargs)

    def window_validator(*args: object, **kwargs: object) -> object:
        call_order.append("window")
        return real_window_validator(*args, **kwargs)

    monkeypatch.setattr(ds, "validate_capture_manifest", capture_validator)
    monkeypatch.setattr(ds, "validate_window_projection", window_validator)
    writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    assert call_order == ["capture", "window", "capture", "window", "capture"]


@pytest.mark.parametrize("fail_on_publish_number", [2, 3])
def test_capture_writer_rolls_back_partial_publish_and_same_id_can_retry(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_publish_number: int,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    real_rename = Path.rename
    publish_count = 0

    def failing_rename(source: Path, target: Path) -> Path:
        nonlocal publish_count
        if ".staging" in source.parts:
            publish_count += 1
            if publish_count == fail_on_publish_number:
                raise OSError(f"fictional publish rename failure {fail_on_publish_number}")
        return real_rename(source, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(OSError, match=f"fictional publish rename failure {fail_on_publish_number}"):
        writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])

    formal_roots = [
        output_root / "captures" / "fictional-capture-phase2",
        output_root.joinpath(*Path(synthetic_window_root(WINDOW_ONE)).parts),
        output_root.joinpath(*Path(synthetic_window_root(WINDOW_TWO)).parts),
    ]
    assert all(not root.exists() for root in formal_roots)
    assert not (output_root / ".staging" / "fictional-capture-phase2").exists()

    monkeypatch.setattr(Path, "rename", real_rename)
    writer.transport.calls.clear()
    writer.transport.api_page_index = 0
    writer.transport.list_page_one_attempts = 0
    writer.transport.detail_attempts = 0
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    assert (output_root / result.capture_path).is_file()
    assert all(root.is_dir() for root in formal_roots)


def test_capture_writer_accepts_vendor_json_openapi_media_type(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    real_get = transport.get

    def vendor_openapi_get(
        url: str,
        *,
        params: dict[str, object] | None,
        headers: dict[str, str],
    ) -> object:
        result = real_get(url, params=params, headers=headers)
        if urlparse(url).path != "/openapi-v1.json":
            return result
        return ds.HttpResponse(
            status=result.status,
            headers={**result.headers, "Content-Type": "application/vnd.oai.openapi+json; charset=utf-8"},
            body=result.body,
        )

    transport.get = vendor_openapi_get  # type: ignore[method-assign]
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    assert ds.validate_persisted_artifact(output_root, result.capture_path)["result"] == "pass"


def test_capture_writer_rejects_empty_vendor_json_subtype(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    real_get = transport.get

    def empty_vendor_subtype_get(
        url: str,
        *,
        params: dict[str, object] | None,
        headers: dict[str, str],
    ) -> object:
        result = real_get(url, params=params, headers=headers)
        if urlparse(url).path != "/openapi-v1.json":
            return result
        return ds.HttpResponse(
            status=result.status,
            headers={**result.headers, "Content-Type": "application/+json"},
            body=result.body,
        )

    transport.get = empty_vendor_subtype_get  # type: ignore[method-assign]
    assert (
        error_code(ds, lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]))
        == "content_type_invalid"
    )
    assert not (output_root / "captures").exists()


def test_slice_ignores_unrelated_malformed_historical_window(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    unrelated = output_root / "windows" / "1900-01-01T000000Z--1900-01-02T000000Z" / "manifest.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"{malformed fictional history")

    sliced = ds.slice_persisted_capture(
        output_root,
        capture_path=result.capture_path,
        start=WINDOW_ONE[0],
        end=WINDOW_ONE[1],
        network_transport=FailIfNetworkTransport(),
    )

    assert sliced == (output_root / synthetic_window_root(WINDOW_ONE) / "items.jsonl").read_bytes()


def test_slice_requires_both_capture_scoped_formal_windows(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    missing_manifest = output_root / synthetic_window_root(WINDOW_TWO) / "manifest.json"
    missing_manifest.unlink()

    assert (
        error_code(
            ds,
            lambda: ds.slice_persisted_capture(
                output_root,
                capture_path=result.capture_path,
                start=WINDOW_ONE[0],
                end=WINDOW_ONE[1],
                network_transport=FailIfNetworkTransport(),
            ),
        )
        == "reference_missing"
    )


def test_slice_rejects_capture_scoped_window_path_alias(
    ds: Any,
    tmp_path: Path,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    first_manifest = output_root / synthetic_window_root(WINDOW_ONE) / "manifest.json"
    second_manifest = output_root / synthetic_window_root(WINDOW_TWO) / "manifest.json"
    second_manifest.unlink()
    second_manifest.symlink_to(first_manifest)

    assert (
        error_code(
            ds,
            lambda: ds.slice_persisted_capture(
                output_root,
                capture_path=result.capture_path,
                start=WINDOW_ONE[0],
                end=WINDOW_ONE[1],
                network_transport=FailIfNetworkTransport(),
            ),
        )
        == "artifact_path_collision"
    )


def test_capture_writer_is_byte_deterministic_for_same_frozen_inputs(
    ds: Any,
    tmp_path: Path,
) -> None:
    first_writer, _transport, _clock, _tool_root, first_root = make_capture_writer(
        ds,
        tmp_path / "first",
        retry_ssr=False,
    )
    second_writer, _transport, _clock, _tool_root, second_root = make_capture_writer(
        ds,
        tmp_path / "second",
        retry_ssr=False,
    )
    first_writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    second_writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])

    def artifact_tree(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    assert artifact_tree(first_root) == artifact_tree(second_root)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("rss_301", "http_301"),
        ("rss_403", "http_403"),
        ("rss_content_type", "content_type_invalid"),
        ("ssr_parse", "ssr_parse_failed"),
        ("retry_exhausted", "retry_budget_exhausted"),
    ],
)
def test_capture_writer_failure_paths_publish_nothing(
    ds: Any,
    tmp_path: Path,
    failure: str,
    expected_code: str,
) -> None:
    writer, transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    real_get = transport.get

    def failing_get(
        url: str,
        *,
        params: dict[str, object] | None,
        headers: dict[str, str],
    ) -> object:
        parsed = urlparse(url)
        if failure == "rss_301" and parsed.path == "/feed.xml":
            return ds.HttpResponse(
                status=301,
                headers={"Location": "https://aihot.invalid/fictional-redirect"},
                body=b"",
            )
        if failure == "rss_403" and parsed.path == "/feed.xml":
            return ds.HttpResponse(status=403, headers={}, body=b"")
        if failure == "rss_content_type" and parsed.path == "/feed.xml":
            return ds.HttpResponse(
                status=200,
                headers={
                    "Content-Type": "text/html",
                    "Date": "Thu, 20 Aug 2026 12:00:00 GMT",
                },
                body=b"<html></html>",
            )
        if failure == "ssr_parse" and parsed.path == "/all":
            return ds.HttpResponse(
                status=200,
                headers={
                    "Content-Type": "text/html",
                    "Date": "Thu, 20 Aug 2026 12:01:00 GMT",
                },
                body=b'<article class="timeline-item">',
            )
        if failure == "retry_exhausted" and parsed.path == "/all":
            return ds.HttpResponse(
                status=429,
                headers={
                    "Retry-After": "2",
                    "Date": "Thu, 20 Aug 2026 12:01:00 GMT",
                },
                body=b"",
            )
        return real_get(url, params=params, headers=headers)

    transport.get = failing_get  # type: ignore[method-assign]
    assert (
        error_code(
            ds,
            lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]),
        )
        == expected_code
    )
    assert not (output_root / "captures" / "fictional-capture-phase2").exists()
    assert not (output_root / "windows").exists()
    assert not (output_root / ".staging" / "fictional-capture-phase2").exists()


def test_capture_writer_interval_mutation_is_rejected_before_publish(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    monkeypatch.setattr(ds, "MINIMUM_REQUEST_INTERVAL_SECONDS", 1.9)
    assert (
        error_code(
            ds,
            lambda: writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1]),
        )
        == "manifest_invalid"
    )
    assert not (output_root / "captures" / "fictional-capture-phase2").exists()


def test_httpx_transport_disables_environment_proxy_and_sets_timeout_and_user_agent(
    ds: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeHttpxResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b"{}"

    class FakeHttpxClient:
        def __init__(self, **kwargs: object) -> None:
            observed["client_kwargs"] = kwargs

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, **kwargs: object) -> object:
            observed["url"] = url
            observed["request_kwargs"] = kwargs
            return FakeHttpxResponse()

    monkeypatch.setattr(ds.httpx, "Client", FakeHttpxClient)
    transport = ds.HttpxTransport(timeout_seconds=13.0, user_agent="Fictional-Test-UA/1.0")
    result = transport.get("https://aihot.invalid/example", params={"fiction": "1"}, headers={})
    assert observed["client_kwargs"] == {
        "trust_env": False,
        "follow_redirects": False,
        "timeout": 13.0,
        "headers": {"User-Agent": "Fictional-Test-UA/1.0"},
    }
    assert observed["request_kwargs"] == {"params": {"fiction": "1"}, "headers": {}}
    assert result == ds.HttpResponse(
        status=200,
        headers={"Content-Type": "application/json"},
        body=b"{}",
    )


def load_capture_cli_module() -> Any:
    script_path = REPO_ROOT / "scripts/capture_aihot_dataset.py"
    spec = importlib.util.spec_from_file_location("capture_aihot_dataset_phase2_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_capture_cli_is_thin_and_dispatches_capture_slice_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = load_capture_cli_module()
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.chdir(tmp_path)

    def fake_capture(**kwargs: object) -> object:
        calls.append(("capture", kwargs))
        return dataset_module.CaptureResult(
            capture_path="captures/fictional/capture.json",
            window_manifest_paths=("windows/one/manifest.json", "windows/two/manifest.json"),
            request_starts=(),
        )

    monkeypatch.setattr(cli, "capture_dataset", fake_capture)
    monkeypatch.setattr(
        cli,
        "slice_persisted_capture",
        lambda *args, **kwargs: calls.append(("slice", {"args": args, **kwargs})) or b'{"fiction":true}\n',
    )
    monkeypatch.setattr(
        cli,
        "validate_persisted_artifact",
        lambda *args, **kwargs: calls.append(("validate", {"args": args, **kwargs}))
        or {"artifact_type": "aihot_validation_report_v1", "result": "pass"},
    )

    assert cli.main(
        [
            "capture",
            "--start",
            WINDOW_ONE[0],
            "--end",
            WINDOW_TWO[1],
            "--output-root",
            "fictional-data",
        ]
    ) == 0
    assert calls[0] == (
        "capture",
        {
            "start": WINDOW_ONE[0],
            "end": WINDOW_TWO[1],
            "output_root": Path("fictional-data"),
        },
    )

    assert cli.main(
        [
            "slice",
            "--capture",
            "fictional-data/captures/fictional/capture.json",
            "--start",
            WINDOW_ONE[0],
            "--end",
            WINDOW_ONE[1],
            "--output",
            "fictional-slice.jsonl",
        ]
    ) == 0
    assert (tmp_path / "fictional-slice.jsonl").read_bytes() == b'{"fiction":true}\n'
    assert cli.main(
        ["validate", "--report-json", "fictional-data/captures/fictional/capture.json"]
    ) == 0
    assert calls[1] == (
        "slice",
        {
            "args": (Path("fictional-data"),),
            "capture_path": "captures/fictional/capture.json",
            "start": WINDOW_ONE[0],
            "end": WINDOW_ONE[1],
        },
    )
    assert calls[2] == (
        "validate",
        {
            "args": (Path("fictional-data"), "captures/fictional/capture.json"),
        },
    )
    report_line = capsys.readouterr().out.splitlines()[-1]
    assert json.loads(report_line) == {
        "artifact_type": "aihot_validation_report_v1",
        "result": "pass",
    }


def test_capture_cli_errors_are_named_and_slice_refuses_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = load_capture_cli_module()
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "existing.jsonl"
    output.write_text("preserve me", encoding="utf-8")
    slice_called = False

    def forbidden_slice(*_args: object, **_kwargs: object) -> bytes:
        nonlocal slice_called
        slice_called = True
        return b""

    monkeypatch.setattr(cli, "slice_persisted_capture", forbidden_slice)
    assert cli.main(
        [
            "slice",
            "--capture",
            "fictional-data/captures/fictional/capture.json",
            "--start",
            WINDOW_ONE[0],
            "--end",
            WINDOW_ONE[1],
            "--output",
            str(output),
        ]
    ) == 2
    assert slice_called is False
    assert output.read_text(encoding="utf-8") == "preserve me"
    error_lines = capsys.readouterr().err.splitlines()
    assert error_lines[0] == f"ERROR target_exists: refusing to overwrite slice output: {output}"
    assert error_lines[1] == "Choose a new output path and retry; existing files are never overwritten."


@pytest.mark.parametrize(
    ("code", "expected_action"),
    [
        (
            "tool_checkout_dirty",
            "Retry from a clean tool checkout; the capture records the checkout's exact HEAD.",
        ),
        (
            "reference_missing",
            "Check that the subject path and its referenced artifacts exist under the dataset root.",
        ),
    ],
)
def test_capture_cli_error_actions_are_specific(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    code: str,
    expected_action: str,
) -> None:
    cli = load_capture_cli_module()
    monkeypatch.chdir(tmp_path)

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise cli.DatasetContractError(code, "fictional failure detail")

    monkeypatch.setattr(cli, "validate_persisted_artifact", fail_validation)
    assert cli.main(["validate", "fictional-data/captures/fictional/capture.json"]) == 2
    error_lines = capsys.readouterr().err.splitlines()
    assert error_lines == [f"ERROR {code}: fictional failure detail", expected_action]


def test_capture_cli_validate_and_slice_replay_real_persisted_synthetic_artifacts(
    ds: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    writer, _transport, _clock, _tool_root, output_root = make_capture_writer(
        ds,
        tmp_path,
        retry_ssr=False,
    )
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    cli = load_capture_cli_module()
    monkeypatch.chdir(output_root)

    assert cli.main(["validate", "--report-json", result.capture_path]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["result"] == "pass"
    assert report["subject"]["artifact_type"] == "aihot_capture_v1"

    assert cli.main(
        [
            "slice",
            "--capture",
            result.capture_path,
            "--start",
            WINDOW_ONE[0],
            "--end",
            WINDOW_ONE[1],
            "--output",
            "offline-fictional-slice.jsonl",
        ]
    ) == 0
    slice_output_lines = capsys.readouterr().out.splitlines()
    assert slice_output_lines == [
        "Offline slice written: output=offline-fictional-slice.jsonl; "
        f"source_capture={result.capture_path}; "
        f"window=[{WINDOW_ONE[0]}, {WINDOW_ONE[1]}); bytes="
        f"{len((output_root / synthetic_window_root(WINDOW_ONE) / 'items.jsonl').read_bytes())}"
    ]
    assert (output_root / "offline-fictional-slice.jsonl").read_bytes() == (
        output_root / synthetic_window_root(WINDOW_ONE) / "items.jsonl"
    ).read_bytes()
