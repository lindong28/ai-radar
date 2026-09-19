"""Observed membership, with label-blind history eligibility (no model calls)."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import re
from urllib.parse import unquote, urlsplit

from airadar.eval import aihot_dataset as ds

from .dataset import resolve_sources, timestamp
from .identity import news_key, substantive_hash

BENCHMARK = "aihot-observed-membership"


def publication_bound(raw: dict) -> dict | None:
    """Only recover producer-supported publication evidence, never label-derived dates.

    RSS published/updated/fallback branches were not archived. Web cards similarly
    lost their date provenance. Keep those unknown, including HF's fetch-time date.
    """
    value = raw.get("published_at")
    if raw.get("source_kind") == "x" and (raw.get("extra") or {}).get("x_post_id") and value:
        post_id = str(raw["extra"]["x_post_id"])
        if urlsplit(raw["url"]).path.rstrip("/").split("/")[-1] != post_id:
            return None
        at = timestamp(value)
        if at > timestamp(raw["fetched_at"]):
            return None
        return {"lower_bound": at.isoformat(), "precision": "timestamp",
                "evidence": "x_api.created_at; archived published_at and matching x_post_id",
                "original_value": value}
    # These adapters encode a release-heading date in the URL. With no archived
    # source timezone use the earliest possible civil-day boundary (UTC+14), not
    # the adapter's implicit UTC midnight as an exact timestamp.
    if raw.get("source_id") in {"claude_platform_releases", "deepseek_api_updates"}:
        fragment = unquote(urlsplit(raw["url"]).fragment)
        match = re.search(r"(?:^|[^0-9])(20\d{2}-\d{2}-\d{2})(?:$|[^0-9])", fragment)
        day = match[1] if match else None
        if raw["source_id"] == "claude_platform_releases":
            heading = re.search(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}", raw["title"])
            if heading:
                day = datetime.strptime(heading[0], "%B %d, %Y").date().isoformat()
        if day:
            at = datetime.fromisoformat(day).replace(tzinfo=UTC) - timedelta(hours=14)
            return {"lower_bound": at.isoformat(), "precision": "day; source timezone unknown",
                    "evidence": "release heading date retained in title or URL fragment",
                    "original_value": day}
    return None


class ObservedAdmissionIndex:
    def __init__(self, references, contract, sources):
        self.matches = defaultdict(list)
        self.proofs = []
        self.names = {key: {row["name"].casefold(), *(a.casefold() for a in row.get("aihot_aliases", []))}
                      for key, row in sources.items()}
        for ref in references:
            sets, uncertain = [], set()
            passes = ref.passes
            if len(ref.capture["passes"]) > 2:
                passes = [ds._load_capture_pass_items(ds.CapturePassManifest.model_validate(p), raw_files=ref.files)
                          for p in ref.capture["passes"]]
            for items in passes:
                mapped, unresolved = resolve_sources([i.model_dump() for i in items], contract, allow_empty=True)
                uncertain.update(row["observed_name"].casefold() for row in unresolved)
                by_source = defaultdict(list)
                for item in items:
                    source = mapped.get(item.upstream_publisher_name)
                    if source and source["slug"] in sources:
                        slug = source["slug"]
                        by_source[slug].append((item.id, ds.timeline_key(item)))
                        self.matches[news_key(slug, item.original_url)].append((item.id, ref.key))
                sets.append(by_source)
            payloads = ref.capture["passes"][-2:]
            if len(payloads) != 2 or len(sets) < 2:
                continue
            # read_reference has replayed the exact 7d/by_timeline cursor chain,
            # terminal page, hashes and HTTP dates. Bounds are their intersection,
            # not the min/max item dates or the pagination completion timestamp.
            left = max(parsedate_to_datetime(p["raw_pages"][-1]["date"]) for p in payloads) - timedelta(days=7)
            right = min(parsedate_to_datetime(p["raw_pages"][0]["date"]) for p in payloads)
            self.proofs.append((left, right, ref.key, sets[-2:], uncertain))
        self.horizon = max((p[1] for p in self.proofs), default=None)

    def label(self, case, record):
        key, raw = case["case_id"], case["input"]
        witness = self.matches[key]
        evidence = {"matched_aihot_ids": sorted({i for i, _ in witness}),
                    "match_references": sorted({r for _, r in witness}),
                    "history_right_bound": self.horizon.isoformat() if self.horizon else None}
        if not raw.get("title", "").strip() or not raw.get("content_text", "").strip():
            return "unusable_input", evidence
        if len({substantive_hash(o["raw"], "news-admission") for o in record["variants"].values()}) != 1:
            return "ambiguous_raw_or_reference_version", evidence
        try:
            bound = publication_bound(raw)
            arrived = timestamp(case["provenance"]["observed_at"])
        except (ValueError, TypeError, KeyError, AttributeError):
            bound = None
            arrived = None
        evidence["publication"] = bound
        reason = "unknown_publication_history_bound"
        if bound and self.horizon and arrived is not None:
            cursor = timestamp(bound["lower_bound"])
            used = []
            reason = "incomplete_reference_history"
            if cursor <= arrived <= self.horizon:
                for left, right, ref, sets, uncertain in sorted(self.proofs, key=lambda p: p[:3]):
                    start, end = max(cursor, left), min(self.horizon, right)
                    if start > cursor or end <= cursor or uncertain.intersection(self.names[raw["source_id"]]):
                        continue
                    members = [{i for i, at in s.get(raw["source_id"], []) if start <= at < end} for s in sets]
                    if members[0] != members[1]:
                        continue
                    cursor = end
                    used.append(ref)
                    if cursor >= self.horizon:
                        evidence["coverage_references"] = used
                        return "main", evidence
            else:
                reason = "arrival_outside_reference_history"
        evidence["main_ineligible_reason"] = reason
        return ("recall-only" if witness else reason), evidence
