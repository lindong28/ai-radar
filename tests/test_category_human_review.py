"""Human/model authority and input-bound category review round trips."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "category_human_review", ROOT / "evals/content-enrichment/aihot-category-navigation/human_review.py")
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


@pytest.fixture
def material():
    return review.read(ROOT / "human-evals/content-enrichment/category-review.json")


def ballot_for(material):
    return {"format": review.BALLOT, "batch_id": material["metadata"]["batch_id"],
        "material_identity": review.material_identity(material),
        "material_sha256": review.digest(material), "exported_at": "2026-09-27T08:00:00Z",
        "judgments": [{"case_id": c["case_id"], "input_sha256": c["input_sha256"],
            "reason": "", "status": "pending", "acceptable_labels": []} for c in material["cases"]]}


def test_real_frozen_material_and_codex_coverage(material):
    review.validate(material)
    assert len(material["cases"]) == 98
    latest = [c["c5_observations"][-1]["label"] != c["aihot"]["label"] for c in material["cases"]]
    assert sum(latest) == 70
    opinions = material["model_reviews"]
    assert opinions[0]["reviewer"]["name"] == "codex"
    assert len(opinions[0]["judgments"]) == 98
    assert {j["case_id"] for j in opinions[0]["judgments"]} == {c["case_id"] for c in material["cases"]}
    assert {c["aihot"]["label"] for c in material["cases"]} == set(review.LABELS)


def test_multiselect_import_idempotent_and_model_opinions_are_not_human(material, tmp_path):
    ballot = ballot_for(material)
    ballot["judgments"][0].update(status="reviewed", acceptable_labels=["paper", "industry"], reason="两个都可接受")
    ballot["judgments"][1].update(status="uncertain", reason="正文不足")
    path, output = tmp_path / "ballot.json", tmp_path / "reviews.json"
    review.write_new(path, ballot)
    before = review.digest(material)
    first = review.import_ballot(material, path, output, "test fixture only")
    assert review.import_ballot(material, path, output, "test fixture only") == first
    book = review.read_reviews(output)
    assert len(book["batches"]) == 1
    assert first["data"]["annotations"] == []
    assert len(first["data"]["category_judgments"]) == 1
    assert review.digest(material) == before
    c = material["cases"][0]
    assert review.accepted_categories(output)[(c["case_id"], c["input_sha256"])] == {"paper", "industry"}
    assert first["data"]["material"]["model_reviews"] == material["model_reviews"]


@pytest.mark.parametrize("mutation", ["input", "revision", "scope", "duplicate", "missing", "label", "pending-label", "empty-reviewed"])
def test_reject_bad_ballots(material, tmp_path, mutation):
    ballot = ballot_for(material)
    row = ballot["judgments"][0]
    if mutation == "input": row["input_sha256"] = "wrong"
    elif mutation == "revision": ballot["material_sha256"] = "wrong"
    elif mutation == "scope": ballot["material_identity"] = "wrong"
    elif mutation == "duplicate": ballot["judgments"].append(deepcopy(row))
    elif mutation == "missing": ballot["judgments"].pop()
    elif mutation == "label": row.update(status="reviewed", acceptable_labels=["unknown"])
    elif mutation == "pending-label": row["acceptable_labels"] = ["paper"]
    elif mutation == "empty-reviewed": row["status"] = "reviewed"
    path = tmp_path / "bad.json"
    review.write_new(path, ballot)
    with pytest.raises(ValueError): review.import_ballot(material, path, tmp_path / "reviews.json", "fixture")
    assert not (tmp_path / "reviews.json").exists()


def test_future_claude_append_and_old_ballot_revision_guard(material, tmp_path):
    opinion = deepcopy(material["model_reviews"][0])
    opinion["review_id"] = "claude-fixture-not-real"
    opinion["reviewer"]["name"] = "claude"
    opinion["reviewer"]["method"] = "test fixture, not a real Claude judgment"
    opinion["judgments"] = opinion["judgments"][:1]
    extended = review.add_opinions(material, opinion)
    assert review.material_identity(extended) == review.material_identity(material)
    assert extended["model_reviews"][0] == material["model_reviews"][0]
    assert review.add_opinions(extended, opinion) == extended
    bad = deepcopy(opinion)
    bad["judgments"][0]["reason"] = "changed"
    with pytest.raises(ValueError): review.add_opinions(extended, bad)
    path = tmp_path / "old-ballot.json"
    review.write_new(path, ballot_for(material))
    with pytest.raises(ValueError, match="exact displayed"):
        review.import_ballot(extended, path, tmp_path / "reviews.json", "fixture")


def test_conflicting_human_votes_require_resolution(material, tmp_path):
    output = tmp_path / "reviews.json"
    for index, label in enumerate(["paper", "industry"]):
        ballot = ballot_for(material)
        ballot["judgments"][0].update(status="reviewed", acceptable_labels=[label])
        path = tmp_path / f"ballot-{index}.json"
        review.write_new(path, ballot)
        review.import_ballot(material, path, output, "fixture")
    with pytest.raises(ValueError, match="conflicting human"):
        review.accepted_categories(output)


def test_tampered_model_material_and_user_impersonation_rejected(material):
    bad = deepcopy(material)
    bad["cases"][0]["input"]["title"] = "different news"
    with pytest.raises(ValueError): review.validate(bad)
    opinion = deepcopy(material["model_reviews"][0])
    opinion["reviewer"]["kind"] = "human"
    with pytest.raises(ValueError, match="user authority"): review.add_opinions(material, opinion)


def test_render_is_portable_and_does_not_overwrite(material, tmp_path):
    path = tmp_path / "source.json"
    review.write_new(path, material)
    output = tmp_path / "page"
    review.render(path, output)
    assert {p.name for p in output.iterdir()} == {"index.html", "app.js", "style.css", "material.json", "identity.json"}
    assert review.read(output / "identity.json")["material_sha256"] == review.digest(material)
    assert (output / "material.json").read_bytes() == path.read_bytes()
    with pytest.raises(FileExistsError): review.render(path, output)


def test_browser_multiselect_export_and_two_tab_preservation(material, tmp_path):
    """Exercise actual static renderer, Web Locks and browser storage together."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from playwright.sync_api import sync_playwright

    source = tmp_path / "source.json"
    review.write_new(source, material)
    root = tmp_path / "page"
    review.render(source, root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            a, b = context.new_page(), context.new_page()
            url = f"http://127.0.0.1:{server.server_port}/index.html"
            for page in (a, b):
                page.goto(url)
                page.wait_for_selector(".vote")
            a.locator('input[value="industry"]').check()
            a.wait_for_function('document.querySelector("#progress").innerText.includes("1 / 98")')
            with b.expect_download():
                b.locator("#download").click()
            assert b.locator('input[value="industry"]').is_checked()
            b.locator("#case-list button").nth(1).click()
            b.locator('input[value="paper"]').check()
            b.wait_for_function('document.querySelector("#progress").innerText.includes("2 / 98")')
            a.reload()
            a.wait_for_selector(".vote")
            assert "2 / 98" in a.locator("#progress").inner_text()
            b.reload()
            b.wait_for_selector(".vote")
            a.locator("#case-list button").nth(1).click()
            a.locator("#human-reason").fill("A fixture reason")
            a.wait_for_function('Object.values(localStorage).some(x=>x.includes("A fixture reason"))')
            b.locator("#human-reason").fill("B fixture reason")
            b.wait_for_function('document.querySelector("#notice").innerText.includes("另一页面")')
            with b.expect_download() as download:
                b.locator("#download").click()
            exported = tmp_path / "ballot.json"
            download.value.save_as(exported)
            ballot = review.read(exported)
            assert ballot["judgments"][1]["reason"] == "B fixture reason"
            assert len(ballot["judgments"]) == 98
            a.reload()
            a.wait_for_selector(".vote")
            assert a.locator("#human-reason").input_value() == "A fixture reason"
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
