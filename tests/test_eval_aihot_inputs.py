from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_aihot_dataset import FakeClock, structural_detail_html
from test_eval_object_datasets import records, reference
from test_eval_system_dataset import CONTRACT, hot, raw, source, write_raw
from test_eval_system_interval import END, START, Transport

from airadar.eval import aihot_dataset as ds
from evals._shared import interval
from evals._shared import object_datasets as ob
from evals._shared.aihot_inputs import OriginalBody
from evals._shared.assets import load_dataset, read_json, read_jsonl


def body(item="a", text="Original article"):
    return (f'<div id="detail-article-{item}" class="dt-article-content">'
            f'<div class="dt-tweet"><p>{text}<br>next &amp; last<img src="x"></p></div></div>')


def original_reference(*, title="Original title", text="Original article"):
    item = {**hot().model_dump(), "original_title": title}
    ref = reference([item])
    ref.manifest["tag_observation_bindings"] = [{"item_id": "a", "response_raw_path": "detail.gz"}]
    ref.capture["finished_at"] = "2026-09-18T13:01:00Z"
    ref.files["detail.gz"] = gzip.compress(("<section>AI answer</section>" + body(text=text)).encode())
    return ref


def test_parser_only_identity_bound_original_not_surrounding_answers():
    parser = OriginalBody("a")
    parser.feed('<article>Score: 90<section>AI summary</section>' + body(text='<div class="dt-body-label"><span>UI label</span></div><script>noise</script><article>Source text</article>')
                + '<div>AI reason</div></article>')
    assert parser.text() == "Source text\nnext & last"


@pytest.mark.parametrize("html", [body("other"), body() + body(), '<div id="detail-article-a">wrong class</div>',
                                     '<div class="dt-article-content" id="detail-article-a">unclosed',
                                     body(text="").replace("<br>next &amp; last", "")])
def test_parser_rejects_missing_ambiguous_or_empty_input(html):
    parser = OriginalBody("a")
    parser.feed(html)
    with pytest.raises(ValueError):
        parser.text()


@pytest.mark.parametrize("message", ["站内暂未收录这条内容的完整正文。阅读完整原文", "按该来源的展示范围，站内仅提供摘要。前往原站阅读", "A new wording"])
def test_explanation_is_not_original_body_even_when_nonempty(message):
    ref = original_reference()
    ref.files["detail.gz"] = gzip.compress((f'<div id="detail-article-a" class="dt-article-content">'
        f'<div class="dt-explain"><p>{message}</p></div></div>').encode())
    cases, excluded, _ = ob.construct({}, [ref], CONTRACT, {"gazette": source()}, {ref.key})
    assert not cases["visible-score"] and not cases["content-enrichment"]
    assert excluded["visible-score"][0]["reason"] == "missing_original_body"


def test_article_body_shape_keeps_original_but_not_labels():
    parser = OriginalBody("a")
    parser.feed('<div id="detail-article-a" class="dt-article-content"><div class="dt-body">'
                '<div class="dt-body-label">UI chrome</div><div class="dt-article">Actual original</div></div></div>')
    assert parser.text() == "Actual original"


def test_only_o2_o3_expand_and_radar_wins():
    ref = original_reference()
    args = ([ref], CONTRACT, {"gazette": source()}, {ref.key})
    cases, excluded, extra = ob.construct({}, *args)
    assert not cases["news-admission"] and not cases["featured-members"] and not extra
    assert excluded["featured-members"][0]["reason"] == "missing_raw"
    for target in ("visible-score", "content-enrichment"):
        assert cases[target][0]["input"]["title"] == "Original title"
        assert "AI answer" not in cases[target][0]["input"]["content_text"]
        assert cases[target][0]["provenance"]["input_origin"] == "aihot-original-detail"
    baseline = ob.construct(records(raw()), [ref], CONTRACT, {"gazette": source()})
    assert ob.construct(records(raw()), *args) == baseline
    cases, _, _ = ob.construct(records(raw(), {**raw(), "title": "changed"}), *args)
    assert not cases["visible-score"] and not cases["content-enrichment"]


def test_missing_title_conflicting_bodies_and_reference_fields():
    ref = original_reference(title=None)
    cases, excluded, _ = ob.construct({}, [ref], CONTRACT, {"gazette": source()}, {ref.key})
    assert not cases["visible-score"]
    assert excluded["visible-score"][0]["reason"] == "missing_original_title"
    a, b = original_reference(), original_reference(text="changed")
    b.manifest["observation"] = "second"
    cases, excluded, _ = ob.construct({}, [a, b], CONTRACT, {"gazette": source()}, {a.key, b.key})
    assert not cases["visible-score"]
    assert excluded["visible-score"][0]["reason"] == "ambiguous_aihot_original_version"
    b = original_reference()
    b.items[0]["aihot_score_0_to_100"] = 99
    cases, excluded, _ = ob.construct({}, [a, b], CONTRACT, {"gazette": source()}, {a.key, b.key})
    assert not cases["visible-score"] and cases["content-enrichment"]


class DetailTransport(Transport):
    def get(self, url, *, params, headers):
        if "/items/" not in url:
            return super().get(url, params=params, headers=headers)
        item = url.rsplit("/", 1)[-1]
        html = structural_detail_html(article_attributes=f'class="dt-detail" data-item-id="{item}"',
            identity_urls=(url, url))
        html = html.replace(b"</article>", (body(item) + "</article>").encode())
        return ds.HttpResponse(200, {"Content-Type": "text/html"}, html)


def test_real_capture_build_frozen_base_rebuild_and_new_radar_updates(tmp_path):
    root = tmp_path / "capture"
    clock = FakeClock()
    writer = ds.CaptureWriter(tool_repo_root=Path(__file__).resolve().parents[1], output_root=root,
        base_url="https://aihot.invalid", user_agent="test", transport=DetailTransport(missing_tags=True),
        limiter=ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep),
        now=lambda: datetime.now(UTC), capture_id="fixture-originals", schema_bytes=b"")
    ref = interval._capture(writer, START, END, root)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(CONTRACT))
    kwargs = dict(data_root=tmp_path / "data", contract_path=contract,
                  targets=["visible-score", "content-enrichment"])
    first = ob.build(references=[ref], aihot_inputs=True, version="v1", **kwargs)
    leaf = Path(first["datasets"]["visible-score"]["path"])
    manifest, cases = load_dataset(leaf)
    assert len(cases) == 2 and manifest["aihot_input_references"]
    assert not read_jsonl(leaf / "evidence/raw-inputs.jsonl")
    root.rename(tmp_path / "unavailable-original-capture")
    second = ob.build(bases=[leaf], version="v2", **kwargs)
    newer = Path(second["datasets"]["visible-score"]["path"])
    assert load_dataset(newer)[1] == cases
    assert second["datasets"]["visible-score"]["merge"]["added"] == 0
    # Deliberately include O1/O4 in a base-only rebuild: neither gets AIHOT-only cases.
    all_targets = ob.build(bases=[newer], version="v3", data_root=tmp_path / "data", contract_path=contract)
    assert all_targets["datasets"]["news-admission"]["main"] == 0
    assert all_targets["datasets"]["featured-members"]["main"] == 0
    raw_root = tmp_path / "raw"
    write_raw(raw_root, START, END, [raw()])
    third = ob.build(bases=[newer], raw_root=raw_root, start=START, end=END, version="v4", **kwargs)
    changes = read_jsonl(Path(third["datasets"]["visible-score"]["path"]) / "changes.jsonl")
    assert any(c["status"] == "updated" for c in changes)
    assert read_json(leaf / "manifest.json") == manifest
