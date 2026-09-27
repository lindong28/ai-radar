"""Freeze source-only supplements for a category run; never changes cases or labels."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from airadar.egress import selector_httpx_client
from airadar.enrich.article_context import input_digest, prepare_article_context, prepare_linked_contexts
from airadar.fetcher.x_api import X_TWEET_FIELDS, _post_entities, _post_text
from airadar.runtime_env import load_runtime_env, read_value
from evals._shared.aihot_inputs import original_body
from evals._shared.assets import read_jsonl


def dump(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def rows(path):
    return read_jsonl(path)


def x_id(raw):
    parts = urlsplit(raw.get("url", "")).path.strip("/").split("/")
    return parts[-1] if len(parts) >= 2 and parts[-2] == "status" and parts[-1].isdigit() else None


def archived_articles(case, evidence_root):
    """Only inspect bound detail responses; no summaries, scores, tags or categories."""
    result = []
    for binding in case["provenance"].get("aihot", []):
        root = evidence_root / binding["reference"]
        item_id = binding["item_id"]
        for manifest_path in sorted(root.glob("windows/**/manifest.json")):
            manifest = json.loads(manifest_path.read_text())
            paths = {b["response_raw_path"] for b in manifest.get("tag_observation_bindings", [])
                     if b["item_id"] == item_id}
            for relative in sorted(paths):
                path = root / relative
                if not path.is_file():
                    continue
                payload = path.read_bytes()
                html = gzip.decompress(payload).decode() if path.suffix == ".gz" else payload.decode()
                try:
                    text = original_body(html, item_id)
                except ValueError:
                    continue
                result.append({"kind": "aihot-original", "content_text": text,
                               "source_path": str(path), "source_sha256": hashlib.sha256(payload).hexdigest(),
                               "item_id": item_id, "reference": binding["reference"],
                               "completeness": "archived-original-section; not independently verified full article"})
    return result


def lookup(ids, output, env_file):
    """One attempt per <=100 ID batch, raw response saved even on an API error."""
    token = read_value("X_BEARER_TOKEN", project_env=env_file)
    if not token:
        dump(output / "x-lookup-missing-credential.json", {"status": "missing_credential", "ids": ids})
        return {}, None
    posts = {}
    fetched_at = datetime.now(UTC).isoformat()
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset+100]
        receipt = {"requested_ids": batch, "fetched_at": datetime.now(UTC).isoformat()}
        try:
            with selector_httpx_client(callsite_id="scripts.eval.category_input_repair",
                                       request_url="https://api.x.com/2/tweets", timeout=30,
                                       follow_redirects=False) as client:
                response = client.get("https://api.x.com/2/tweets", headers={"Authorization": "Bearer " + token},
                                      params={"ids": ",".join(batch), "tweet.fields": X_TWEET_FIELDS,
                                              "expansions": "referenced_tweets.id,referenced_tweets.id.author_id,author_id",
                                              "user.fields": "username"})
                receipt.update(http_status=response.status_code, payload=response.json())
        except Exception as exc:
            receipt["error"] = type(exc).__name__
        dump(output / f"x-lookup-{offset//100}.json", receipt)
        if receipt.get("http_status") != 200:
            continue
        payload = receipt["payload"]
        for post in payload.get("data", []) + payload.get("includes", {}).get("tweets", []):
            posts[str(post["id"])] = post
    return posts, fetched_at


def prepare(case, *, old, evidence_root, posts, lookup_time, fetch_web):
    raw = case["input"]
    row = dict(old.get(case["case_id"], {}))
    if row and (row["original_input_sha256"] != input_digest(raw) or row["url"] != raw["url"]):
        raise ValueError("previous context does not match case")
    if not row:
        row = {"original_input_sha256": input_digest(raw), "url": raw["url"],
               "status": "not_applicable", "content_text": "", "fetched_at": None}
    previous_materials = list(row.get("repair_materials", []))
    materials = []
    if raw.get("source_kind") in {"feed", "web"}:
        originals = archived_articles(case, evidence_root)
        base = row["content_text"] if row.get("status") == "available" else str(raw.get("content_text") or "")
        if originals:
            candidate = max(originals, key=lambda x: len(x["content_text"]))
            if len(candidate["content_text"]) > len(base):
                materials.append(candidate)
        if fetch_web and raw.get("source_kind") == "web" and not originals:
            fetched = prepare_article_context(raw)
            if fetched["status"] == "available":
                materials.append({"kind": "current-article", **fetched})
    if raw.get("source_kind") == "x":
        current = posts.get(x_id(raw), {})
        text = _post_text(current)
        if text and text.strip() != str(raw.get("content_text") or "").strip():
            materials.append({"kind": "current-post", "post_id": x_id(raw), "content_text": text,
                              "fetched_at": lookup_time, "url": raw["url"],
                              "completeness": "API text; media and external links not included"})
        references = current.get("referenced_tweets", raw.get("extra", {}).get("referenced_tweets", []))
        ids = sorted({str(r["id"]) for r in references if r.get("type") == "quoted"})
        for post_id in ids:
            post = posts.get(post_id, {})
            text = _post_text(post)
            materials.append({"kind": "quoted-post", "post_id": post_id,
                              "url": f"https://x.com/i/web/status/{post_id}",
                              "status": "available" if text else "unavailable",
                              "content_text": text, "fetched_at": lookup_time,
                              "published_at": post.get("created_at"),
                              "completeness": "API text; media and external links not included"})
        if fetch_web:
            extra = {**raw.get("extra", {}), "x_post_id": x_id(raw), "entities": _post_entities(current) or raw.get("extra", {}).get("entities", {})}
            quotes = [{"post_id": i, "entities": _post_entities(posts.get(i, {}))} for i in ids]
            materials.extend(prepare_linked_contexts(extra, quotes))
    usable = [m for m in materials if m.get("content_text")]
    if usable:
        articles = [m for m in usable if m["kind"] in {"aihot-original", "current-article"}]
        if articles and (row.get("status") != "available" or
                         max(len(m["content_text"]) for m in articles) > len(row["content_text"])):
            best = max(articles, key=lambda m: len(m["content_text"]))
            segments = ["Original article:\n" + best["content_text"]]
        else:
            segments = [row["content_text"]] if row.get("status") == "available" else []
        segments.extend(m["kind"] + " " + m.get("url", "") + ":\n" + m["content_text"]
                        for m in usable if m not in articles and m["content_text"] not in "\n".join(segments))
        row.update(status="available", content_text="\n\n".join(segments),
                   fetched_at=datetime.now(UTC).isoformat(),
                   detail="source repair supplement; preparation time, NOT historical as-of availability")
    # Keep earlier successful evidence even when a subsequent retrieval fails.
    merged = {json.dumps(m, sort_keys=True, ensure_ascii=False): m for m in previous_materials + materials}
    return {"case_id": case["case_id"], **row, "repair_materials": list(merged.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--previous-context", type=Path, required=True)
    parser.add_argument("--aihot-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--lookup-x", action="store_true")
    parser.add_argument("--fetch-web", action="store_true")
    parser.add_argument("--x-response", type=Path, help="Reuse a frozen lookup receipt without requesting its IDs again")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    load_runtime_env(project_env=args.env_file)
    cases = rows(args.cases)
    old = {r["case_id"]: r for r in rows(args.previous_context)}
    ids = sorted({str(r["id"]) for c in cases for r in c["input"].get("extra", {}).get("referenced_tweets", [])
                  if r.get("type") == "quoted"} | {x_id(c["input"]) for c in cases if c["input"].get("source_kind") == "x" and x_id(c["input"])})
    posts, fetched_at = {}, None
    if args.x_response:
        prior = json.loads(args.x_response.read_text())
        if prior.get("http_status") != 200:
            raise ValueError("prior X lookup was not successful")
        for post in prior["payload"].get("data", []) + prior["payload"].get("includes", {}).get("tweets", []):
            posts[str(post["id"])] = post
        fetched_at = prior["fetched_at"]
        dump(args.output / "x-lookup-reused.json", prior)
    missing = [i for i in ids if i not in posts]
    if args.lookup_x and missing:
        fetched, fetched_at = lookup(missing, args.output, args.env_file)
        posts.update(fetched)
    def work(case):
        return prepare(case, old=old, evidence_root=args.aihot_evidence, posts=posts,
                       lookup_time=fetched_at, fetch_web=args.fetch_web)
    with (args.output / "body-context.jsonl").open("x") as stream:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for row in pool.map(work, cases):
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
    dump(args.output / "case-ids.json", [c["case_id"] for c in cases])
    dump(args.output / "receipt.json", {"cases": str(args.cases),
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "previous_context": str(args.previous_context),
        "previous_context_sha256": hashlib.sha256(args.previous_context.read_bytes()).hexdigest(),
        "requested_quote_ids": ids, "returned_post_ids": sorted(posts),
        "temporal_scope": "repaired source material, not a historical availability simulation"})


if __name__ == "__main__":
    main()
