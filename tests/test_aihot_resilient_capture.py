from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest
from test_aihot_dataset import (
    WINDOW_ONE,
    WINDOW_TWO,
    load_capture_cli_module,
    make_capture_writer,
)

from airadar.eval import aihot_dataset as ds


def fail_surface(monkeypatch, transport, path: str, *, malformed: bool = False) -> None:
    original = transport.get

    def get(url, **kwargs):
        if urlparse(url).path == path:
            return ds.HttpResponse(
                status=200 if malformed else 404,
                headers={"Content-Type": "application/json", "Date": "Thu, 20 Aug 2026 12:00:02 GMT"},
                body=b"{}" if malformed else b"not found",
            )
        return original(url, **kwargs)

    monkeypatch.setattr(transport, "get", get)


@pytest.mark.parametrize("fill_missing", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
def test_resilient_capture_records_probe_failure_and_replays_windows(
    tmp_path, monkeypatch, fill_missing, malformed,
):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    fail_surface(monkeypatch, transport, "/openapi-v1.json", malformed=malformed)
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1],
                            resilient=True, fill_missing=fill_missing)
    capture = json.loads((root / result.capture_path).read_bytes())
    assert capture["artifact_type"] == "aihot_capture_v2"
    assert capture["probe_errors"] == [{
        "surface": "openapi", "request_url": "https://aihot.invalid/openapi-v1.json",
        "error_code": "public_surface_invalid" if malformed else "http_404",
    }]
    assert [row["surface"] for row in capture["public_responses"]] == ["rss"]
    assert transport.api_page_index == 4
    capture_report = ds.validate_persisted_artifact(root, result.capture_path)
    assert capture_report["artifact_type"] == "aihot_capture_validation_report_v2"
    for path in result.window_manifest_paths:
        report = ds.validate_persisted_artifact(root, path)
        assert report["artifact_type"] == "aihot_window_validation_report_v3"
        assert report["subject"]["artifact_type"] == "aihot_window_v3"
        assert report["raw_api_and_ssr_replay"] == "pass"
        assert report["target_item_id_count"] == 1
        assert report["supplemental_probes"]["probe_errors"] == capture["probe_errors"]
    with pytest.raises(ds.DatasetContractError, match="manifest_invalid"):
        ds.slice_persisted_capture(root, capture_path=result.capture_path,
                                  start=WINDOW_ONE[0], end=WINDOW_TWO[1])


@pytest.mark.parametrize("path", ["/api/v1/items", "/all"])
def test_resilient_capture_does_not_swallow_primary_failure(tmp_path, monkeypatch, path):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    fail_surface(monkeypatch, transport, path)
    with pytest.raises(ds.DatasetContractError, match="http_404"):
        writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    assert not (root / "captures" / writer.capture_id).exists()
    assert not (root / "windows").exists()


def test_legacy_capture_still_requires_probes(tmp_path, monkeypatch):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path)
    fail_surface(monkeypatch, transport, "/openapi-v1.json")
    with pytest.raises(ds.DatasetContractError, match="http_404"):
        writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
    assert transport.api_page_index == 0
    assert not (root / "captures" / writer.capture_id).exists()


def test_both_supplemental_probes_can_fail_with_api_identity(tmp_path, monkeypatch):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    fail_surface(monkeypatch, transport, "/feed.xml")
    fail_surface(monkeypatch, transport, "/openapi-v1.json")
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    report = ds.validate_persisted_artifact(root, result.window_manifest_paths[0])
    assert report["supplemental_probes"]["validated_surfaces"] == []
    assert len(report["supplemental_probes"]["probe_errors"]) == 2
    assert report["identity"]["first_api_response_observed_at"] == "2026-08-20T12:00:04Z"
    assert report["raw_api_and_ssr_replay"] == "pass"


def test_probe_transport_failure_is_diagnostic_after_bounded_retries(tmp_path, monkeypatch):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    original = transport.get
    attempts = []

    def get(url, **kwargs):
        if urlparse(url).path == "/openapi-v1.json":
            attempts.append(url)
            raise httpx.ReadTimeout("fictional timeout")
        return original(url, **kwargs)

    monkeypatch.setattr(transport, "get", get)
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    capture = json.loads((root / result.capture_path).read_bytes())
    assert len(attempts) == 3
    assert capture["probe_errors"][0]["error_code"] == "transport_failed"


def test_resilient_fill_missing_validates_existing_windows_and_skips_them(tmp_path):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True, fill_missing=True)
    before = (root / result.window_manifest_paths[0]).read_bytes()
    writer.capture_id = "second-capture"
    transport.capture_id = writer.capture_id
    transport.api_page_index = 0
    transport.calls.clear()
    with pytest.raises(ds.DatasetContractError, match="no_missing_windows"):
        writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True, fill_missing=True)
    assert (root / result.window_manifest_paths[0]).read_bytes() == before
    assert not (root / "captures" / writer.capture_id).exists()


def test_invalid_probe_date_is_recorded_as_diagnostic(tmp_path, monkeypatch):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    original = transport.get

    def get(url, **kwargs):
        response = original(url, **kwargs)
        if urlparse(url).path == "/openapi-v1.json":
            return ds.HttpResponse(status=200, headers={**response.headers, "Date": "invalid"},
                                   body=response.body)
        return response

    monkeypatch.setattr(transport, "get", get)
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    report = ds.validate_persisted_artifact(root, result.window_manifest_paths[0])
    assert report["supplemental_probes"]["probe_errors"][0]["error_code"] == "public_response_metadata_invalid"


@pytest.mark.parametrize("resilient", [False, True])
def test_cached_openapi_date_before_rss_is_diagnostic_only_in_resilient_mode(
    tmp_path, monkeypatch, resilient,
):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    original = transport.get

    def get(url, **kwargs):
        response = original(url, **kwargs)
        if urlparse(url).path == "/openapi-v1.json":
            return ds.HttpResponse(
                status=200,
                headers={**response.headers, "Date": "Wed, 19 Aug 2026 12:00:02 GMT"},
                body=response.body,
            )
        return response

    monkeypatch.setattr(transport, "get", get)
    if not resilient:
        with pytest.raises(ds.DatasetContractError, match="manifest_invalid"):
            writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1])
        assert not (root / "captures" / writer.capture_id).exists()
        return
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    for manifest_path in result.window_manifest_paths:
        report = ds.validate_persisted_artifact(root, manifest_path)
        assert report["raw_api_and_ssr_replay"] == "pass"
        assert report["supplemental_probes"]["validated_surfaces"] == ["rss"]
        assert report["supplemental_probes"]["probe_errors"] == [{
            "surface": "openapi", "request_url": "https://aihot.invalid/openapi-v1.json",
            "error_code": "public_response_metadata_invalid",
        }]


def test_legacy_manifest_cannot_claim_resilient_probe_omission(tmp_path, monkeypatch):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    fail_surface(monkeypatch, transport, "/openapi-v1.json")
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    path = root / result.capture_path
    capture = json.loads(path.read_bytes())
    capture["artifact_type"] = "aihot_capture_v1"
    del capture["probe_errors"]
    path.write_bytes(ds.canonical_json_bytes(capture))
    with pytest.raises(ds.DatasetContractError, match="manifest_invalid"):
        ds.validate_persisted_artifact(root, result.capture_path)


@pytest.mark.parametrize("surface", ["api", "ssr", "rss"])
def test_resilient_window_rejects_changed_raw_bytes(tmp_path, monkeypatch, surface):
    writer, transport, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    fail_surface(monkeypatch, transport, "/openapi-v1.json")
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    capture = json.loads((root / result.capture_path).read_bytes())
    window_path = result.window_manifest_paths[0]
    window = json.loads((root / window_path).read_bytes())
    raw = {
        "api": capture["passes"][0]["raw_pages"][0]["raw_path"],
        "ssr": window["tag_observation_responses"][0]["response_raw_path"],
        "rss": capture["public_responses"][0]["raw_path"],
    }[surface]
    (root / raw).write_bytes(b"tampered")
    with pytest.raises(ds.DatasetContractError, match="raw_hash_mismatch"):
        ds.validate_persisted_artifact(root, window_path)


def test_validation_reads_only_subject_dependencies(tmp_path, monkeypatch):
    writer, _, _, _, root = make_capture_writer(ds, tmp_path, retry_ssr=False)
    result = writer.capture(start=WINDOW_ONE[0], end=WINDOW_TWO[1], resilient=True)
    unrelated = root / "captures" / "unrelated" / "unreadable.json"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"not a manifest")
    original = Path.read_bytes
    reads = []

    def read_bytes(path):
        assert path != unrelated
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    report = ds.validate_persisted_artifact(root, result.window_manifest_paths[0])
    assert report["result"] == "pass"
    assert root / result.window_manifest_paths[1] not in reads


def test_resilient_cli_dispatch(tmp_path, monkeypatch, capsys):
    cli = load_capture_cli_module()
    seen = []

    def capture(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(capture_path="captures/test/capture.json", window_manifest_paths=())

    monkeypatch.setattr(cli, "capture_dataset", capture)
    assert cli.main(["capture", "--start", WINDOW_ONE[0], "--end", WINDOW_TWO[1],
                     "--output-root", str(tmp_path), "--fill-missing", "--resilient"]) == 0
    assert seen[0]["resilient"] is True
    assert seen[0]["fill_missing"] is True
