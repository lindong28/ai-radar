"""Original article inputs from already validated, frozen AIHOT references.

Do not use the surrounding page: it contains the answers we are fitting.
"""
from __future__ import annotations

import gzip
from html.parser import HTMLParser

from .assets import digest
from .dataset import raw_content_hash, timestamp
from .identity import input_url, split_for, substantive_hash


class OriginalBody(HTMLParser):
    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
    BLOCK = frozenset("div p article section li ul ol blockquote pre h1 h2 h3 h4 br hr".split())

    def __init__(self, item_id):
        super().__init__(convert_charrefs=True)
        self.container = f"detail-article-{item_id}"
        self.stack = []
        self.hidden = []
        self.original = []
        self.has_original = False
        self.depth = None
        self.count = 0
        self.closed = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == self.container:
            self.count += 1
            self.depth = len(self.stack)
            if tag != "div" or "dt-article-content" not in attrs.get("class", "").split():
                self.count += 1  # A changed container contract is not a whole-page fallback.
        if self.depth is not None and tag in self.BLOCK:
            self.parts.append("\n")
        if tag not in self.VOID:
            self.stack.append(tag)
            self.hidden.append((self.hidden[-1] if self.hidden else False)
                               or tag in {"script", "style", "button", "noscript"}
                               or "dt-body-label" in attrs.get("class", "").split())
            is_original = self.depth is not None and tag == "div" and bool(
                {"dt-tweet", "dt-article"}.intersection(attrs.get("class", "").split()))
            self.original.append((self.original[-1] if self.original else False) or is_original)
            self.has_original |= is_original

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        position = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.depth is not None:
            if tag in self.BLOCK:
                self.parts.append("\n")
            if position <= self.depth:
                self.closed = position == self.depth
                self.depth = None
        del self.stack[position:]
        del self.hidden[position:]
        del self.original[position:]

    def handle_data(self, data):
        if self.depth is not None and self.original[-1] and not self.hidden[-1]:
            self.parts.append(data)

    def text(self):
        if self.count != 1 or not self.closed:
            raise ValueError("missing_or_ambiguous_original_body")
        if not self.has_original:
            raise ValueError("missing_original_body")
        result = "\n".join(line for part in "".join(self.parts).splitlines() if (line := " ".join(part.split())))
        if not result:
            raise ValueError("empty_original_body")
        return result


def original_input(ref, item, source):
    title = item.get("original_title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing_original_title")
    paths = {b["response_raw_path"] for b in ref.manifest.get("tag_observation_bindings", [])
             if b["item_id"] == item["id"]}
    if len(paths) != 1:
        raise ValueError("missing_or_ambiguous_original_binding")
    path = next(iter(paths))
    if path not in ref.files:
        raise ValueError("missing_original_response")
    body = ref.files[path]
    if path.endswith(".gz"):
        body = gzip.decompress(body)
    parser = OriginalBody(item["id"])
    parser.feed(body.decode("utf-8"))
    parser.close()
    content = parser.text()
    observed = ref.capture["finished_at"]
    raw = {"source_id": source["slug"], "url": item["original_url"], "title": title,
           "author": None, "published_at": item.get("published_at"), "fetched_at": observed,
           "content_text": content, "content_html": None, "extra": {}}
    provenance = {"input_origin": "aihot-original-detail", "reference": ref.key,
                  "item_id": item["id"], "response_raw_path": path, "observed_at": observed,
                  "observation_time_basis": "capture_finished_at"}
    return raw, provenance


def fallback_case(key, pairs, references, source, allowed):
    """Require a single input version; never rescue an ambiguous Radar input."""
    def order(pair):
        return timestamp(pair[1]["observed_at"]), pair[1]["reference"], digest(pair[0])

    variants, reasons = {}, set()
    for ref_id, item in pairs:
        if ref_id not in allowed:
            continue
        try:
            raw, provenance = original_input(references[ref_id], item, source)
        except ValueError as exc:
            reasons.add(str(exc))
            continue
        identity = substantive_hash(raw, "visible-score")
        previous = variants.get(identity)
        candidate = (raw, provenance)
        if previous is None or order(candidate) < order(previous):
            variants[identity] = candidate
    if len(variants) != 1:
        return None, "ambiguous_aihot_original_version" if variants else ",".join(sorted(reasons)) or "missing_raw"
    raw, provenance = next(iter(variants.values()))
    raw = {**raw, "url": input_url(raw["url"], source)}
    return {"case_id": key, "split": split_for(raw["url"]), "reference": {}, "provenance": provenance,
            "input": {**raw, "case_id": key, "item_id": key, "tier": source["tier"],
                      "source_kind": source["kind"], "source_name": source["name"],
                      "source_enabled": True, "content_hash": raw_content_hash(raw)}}, None
