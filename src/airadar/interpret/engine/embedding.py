"""Embedding management and index queries for the knowledge base.

Provides CLI modes including:
  --build          Build embeddings for all articles in a user's knowledge base.
  --add            Add/update embedding for a single article by slug.
  --search         Semantic search over the knowledge base (with optional string matching).
  --check-url      Check if a URL already exists in the index (for dedup).
  --verify-url-vector  Verify that a URL has an aligned, finite, nonzero vector.
  --list-article-records  Export a versioned JSONL catalog for external consumers.
  --list-keywords  List all unique keywords from the index.
  --append-entry   Append a new entry to the index from JSON.
  --add-project    Add an open source project to the tracking list.
  --list-projects  List all tracked open source projects.

Usage:
  uv run python agents/summary-agent/src/embedding.py --build --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --add <slug> --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --search "查询文本" --user dong_lin --top-k 10
  uv run python agents/summary-agent/src/embedding.py --search "查询文本" --terms "Seedance,Skill,视频" --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --check-url "https://mp.weixin.qq.com/s/..." --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --list-keywords --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --append-entry '{"title":"...", "slug":"...", "tags":[], "keywords":[], "model_name":"gpt-5.4-mini"}' --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --add-project '{"name":"...", "url":"...", "description":"..."}' --user dong_lin
  uv run python agents/summary-agent/src/embedding.py --list-projects --user dong_lin
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

import numpy as np

from airadar.provider.llm_gateway import gateway_client, gateway_error, gateway_headers, gateway_identity

from . import tag_normalizer
from .paths import kb_root, stored_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
KB_ROOT = kb_root()
MIN_SCORE_THRESHOLD = 0.2


# ---------------------------------------------------------------------------
# File locking
# ---------------------------------------------------------------------------


def _user_lock_path(user: str) -> Path:
    if not user or "/" in user or "\\" in user:
        raise ValueError(f"Invalid user name for lock: {user!r}")
    root = KB_ROOT.resolve()
    # Preserve the original writer's lock for the existing data layout.
    lock_dir = root.parent.parent / "tmp" if root.parts[-2:] == ("data", "summary_agent") else root / ".locks"
    return lock_dir / f"summary_agent_{user}.lock"


@contextmanager
def _user_lock(user: str):
    """Advisory file lock for all write operations on a user's data.

    Uses a single per-user lock to prevent concurrent read-modify-write races
    on index.json, open_source_projects.json, and vectors.npy/manifest.json.
    Lock identity follows the knowledge base, independent of caller cwd.
    """
    lock_path = _user_lock_path(user)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_openai_client():
    """Return the centrally routed embedding client, without provider keys."""
    return gateway_client(callsite_id="interpret.engine.embeddings")


def _embeddings_dir(user: str) -> Path:
    return KB_ROOT / user / "embeddings"


def _load_index(user: str) -> list[dict]:
    index_path = KB_ROOT / user / "index.json"
    if not index_path.exists():
        print(f"Error: Index file not found: {index_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(
            f"Error: Invalid JSON in {index_path}: {exc}\nIf using Git LFS, ensure you have run: git -C data lfs pull",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(data, list):
        print(f"Error: Expected JSON array in {index_path}, got {type(data).__name__}", file=sys.stderr)
        sys.exit(1)
    return data


def _read_summary_text(summary_path: str) -> str:
    """Read a summary file and return its text content, or empty string on failure."""
    full_path = stored_path(summary_path, KB_ROOT)
    if not full_path.exists():
        return ""
    try:
        return full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: Could not read summary file {full_path}: {exc}", file=sys.stderr)
        return ""


def _read_summary_excerpt(summary_path: Path, limit: int = 500) -> str:
    try:
        return summary_path.read_text(encoding="utf-8")[:limit]
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: Could not read summary file {summary_path}: {exc}", file=sys.stderr)
        return ""


def _project_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").exists())


_TRACKING_QUERY_PARAMS = frozenset(
    {
        "ascene",
        "chksm",
        "clicktime",
        "dclid",
        "fbclid",
        "from",
        "gbraid",
        "gclid",
        "igshid",
        "isappinstalled",
        "mc_cid",
        "mc_eid",
        "mpshare",
        "msclkid",
        "scene",
        "sessionid",
        "spm",
        "srcid",
        "subscene",
        "wbraid",
        "yclid",
    }
)
_TRACKING_QUERY_PREFIXES = ("utm_", "sharer_")


def _is_weixin_host(hostname: str) -> bool:
    return hostname == "mp.weixin.qq.com" or hostname.endswith(".weixin.qq.com")


def _canonical_netloc(parsed) -> str:
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return parsed.netloc.lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if hostname == "mp.weixin.qq.com" and (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        port = None
    return f"{hostname}:{port}" if port else hostname


def _is_tracking_query_param(name: str) -> bool:
    lower = name.lower()
    return lower in _TRACKING_QUERY_PARAMS or lower.startswith(_TRACKING_QUERY_PREFIXES)


def canonicalize_url(url: str) -> str:
    """Return a canonical URL identity for deduplication.

    Modern WeChat article paths carry non-identity query parameters, while the
    legacy ``/s`` form identifies an article with ``__biz``, ``mid``, ``idx``,
    and ``sn``. Other hosts keep non-tracking query parameters sorted by
    key/value.
    """
    if not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return raw.split("#", 1)[0]

    scheme = parsed.scheme.lower()
    netloc = _canonical_netloc(parsed)
    path = parsed.path

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_param(key)
    ]

    if hostname == "mp.weixin.qq.com" and path == "/s":
        legacy_identity = {}
        for key in ("__biz", "mid", "idx", "sn"):
            values = [value for item_key, value in query_items if item_key == key and value]
            if len(values) != 1:
                break
            legacy_identity[key] = values[0]
        else:
            query = urlencode(list(legacy_identity.items()))
            return urlunparse((scheme, netloc, path, "", query, ""))

    if _is_weixin_host(hostname):
        if hostname != "mp.weixin.qq.com" or path != "/s":
            return urlunparse((scheme, netloc, path, "", "", ""))

    query_items.sort(key=lambda item: (item[0].lower(), item[1]))
    query = urlencode(query_items, doseq=True)
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def _extract_overview(summary_path: str) -> str:
    """Extract the 文章概况 line from a summary file.

    Looks for ``### 📋 文章概况`` and returns the first non-empty line after it.
    Returns empty string on failure.
    """
    text = _read_summary_text(summary_path)
    if not text:
        return ""

    match = re.search(r"###\s*📋\s*文章概况\s*\n(.*?)(?:\n###|\Z)", text, re.DOTALL)
    if not match:
        return ""
    block = match.group(1).strip()
    for line in block.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _extract_suitable_scenarios(summary_path: str) -> str:
    """Extract the 适合场景 line from a summary file.

    Looks for ``**适合场景**:`` anywhere in the file and returns the text after
    the colon. Returns empty string on failure.
    """
    text = _read_summary_text(summary_path)
    if not text:
        return ""

    match = re.search(r"\*\*适合场景\*\*:\s*(.+)", text)
    if not match:
        return ""
    return match.group(1).strip()


def _slug_from_path(file_path: str) -> str:
    """Derive slug from a summary file path like .../<slug>_output.md."""
    name = Path(file_path).stem  # e.g. "building_effective_agents_output"
    if name.endswith("_output"):
        return name[: -len("_output")]
    return name


def build_embedding_text(entry: dict, overview: str, scenarios: str = "") -> str:
    """Compose the text to embed for one article.

    Format:
        {title}
        {overview (one-liner)}
        Tags: {tag1}, {tag2}
        Keywords: {kw1}, {kw2}
        Scenarios: {scenarios}
    """
    parts = [entry["title"]]
    if overview:
        parts.append(overview)
    tags = entry.get("metadata", {}).get("tags", [])
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    keywords = entry.get("metadata", {}).get("keywords", [])
    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}")
    if scenarios:
        parts.append(f"Scenarios: {scenarios}")
    return "\n".join(parts)


def get_embeddings(client, texts: list[str]) -> np.ndarray:
    """Call OpenAI embeddings API. Returns (N, EMBEDDING_DIM) float32 array."""
    request_id = str(uuid4())
    response = None
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL, input=texts, encoding_format="float",
            extra_headers=gateway_headers(request_id), extra_body={"timeout": 90},
        )
        identity = gateway_identity(response, request_id)
        if identity["requested_logical_model"] != EMBEDDING_MODEL:
            raise ValueError("Gateway embedding logical model mismatch")
        rows = sorted(response.data, key=lambda item: item.index)
        if [row.index for row in rows] != list(range(len(texts))):
            raise ValueError("Gateway embedding response indices mismatch")
        vectors = np.array([row.embedding for row in rows], dtype=np.float32)
        if vectors.shape != (len(texts), EMBEDDING_DIM) or not np.isfinite(vectors).all():
            raise ValueError("Gateway embedding shape or values invalid")
        return vectors
    except Exception as exc:
        raise gateway_error(exc, request_id, completion=response) from exc


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a query vector and a matrix of vectors."""
    # query_vec: (D,), matrix: (N, D)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return np.zeros(matrix.shape[0])
    matrix_norms = np.linalg.norm(matrix, axis=1)
    # Avoid division by zero
    matrix_norms = np.where(matrix_norms == 0, 1e-10, matrix_norms)
    return (matrix @ query_vec) / (matrix_norms * query_norm)


def _slugs_from_index(index: list[dict]) -> list[str]:
    """Derive the ordered slug list from index.json entries."""
    return [_slug_from_path(entry.get("output", {}).get("summary_file_path", "")) for entry in index]


def _save_vectors(emb_dir: Path, vectors: np.ndarray, slugs: list[str]) -> None:
    emb_dir.mkdir(parents=True, exist_ok=True)
    np.save(emb_dir / "vectors.npy", vectors)
    manifest_path = emb_dir / "vectors_manifest.json"
    manifest_path.write_text(
        json.dumps({"slugs": slugs}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_vectors(emb_dir: Path) -> tuple[np.ndarray | None, list[str] | None]:
    """Load vectors and manifest. Returns (vectors, manifest_slugs).

    - vectors.npy missing → (None, None)
    - manifest missing (legacy data) → (vectors, None)
    """
    vectors_path = emb_dir / "vectors.npy"
    if not vectors_path.exists():
        return None, None
    vectors = np.load(vectors_path)
    manifest_path = emb_dir / "vectors_manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return vectors, data["slugs"]
    return vectors, None


def _check_alignment(
    manifest_slugs: list[str] | None, index_slugs: list[str]
) -> Literal["exact", "append_only", "mismatch"]:
    """Compare manifest slugs against current index slugs.

    Returns:
        "exact"       — identical lists
        "append_only" — index_slugs has extra entries appended at the end
        "mismatch"    — reorder, deletion, or manifest missing
    """
    if manifest_slugs is None:
        return "mismatch"
    if manifest_slugs == index_slugs:
        return "exact"
    if len(index_slugs) > len(manifest_slugs) and index_slugs[: len(manifest_slugs)] == manifest_slugs:
        return "append_only"
    return "mismatch"


def _incremental_rebuild(user: str) -> None:
    """Rebuild vectors reusing existing embeddings where possible.

    Caller must hold ``_user_lock(user)`` before calling this function.
    """
    index = _load_index(user)
    if not index:
        print("Index is empty, nothing to build.", file=sys.stderr)
        sys.exit(1)

    slugs = _slugs_from_index(index)
    emb_dir = _embeddings_dir(user)
    old_vectors, old_manifest = _load_vectors(emb_dir)

    # Build old slug → row index mapping
    old_slug_to_row: dict[str, int] = {}
    if old_manifest is not None and old_vectors is not None:
        for i, s in enumerate(old_manifest):
            if i < old_vectors.shape[0]:
                old_slug_to_row[s] = i

    # Determine which slugs need embedding
    to_embed: list[tuple[int, dict]] = []  # (new_index, entry)
    reused = 0
    for new_idx, (slug, entry) in enumerate(zip(slugs, index)):
        old_row = old_slug_to_row.get(slug)
        if old_row is not None and old_vectors is not None and np.any(old_vectors[old_row] != 0):
            reused += 1
        else:
            to_embed.append((new_idx, entry))

    print(
        f"Incremental rebuild: {len(slugs)} total, {reused} reused, {len(to_embed)} to embed",
        file=sys.stderr,
    )

    # Embed only new/zero-vector slugs
    new_vecs_map: dict[int, np.ndarray] = {}
    if to_embed:
        client = _get_openai_client()
        texts = []
        for _, entry in to_embed:
            summary_path = entry.get("output", {}).get("summary_file_path", "")
            overview = _extract_overview(summary_path)
            scenarios = _extract_suitable_scenarios(summary_path)
            texts.append(build_embedding_text(entry, overview, scenarios))
        embedded = get_embeddings(client, texts)
        for (new_idx, _), vec in zip(to_embed, embedded):
            new_vecs_map[new_idx] = vec

    # Assemble new vectors in index order
    new_vectors = np.zeros((len(slugs), EMBEDDING_DIM), dtype=np.float32)
    for new_idx, slug in enumerate(slugs):
        if new_idx in new_vecs_map:
            new_vectors[new_idx] = new_vecs_map[new_idx]
        else:
            old_row = old_slug_to_row.get(slug)
            if old_row is not None and old_vectors is not None:
                new_vectors[new_idx] = old_vectors[old_row]

    _save_vectors(emb_dir, new_vectors, slugs)
    print(
        f"Saved {new_vectors.shape[0]} embeddings to {emb_dir}/",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# String matching
# ---------------------------------------------------------------------------

# Bonus weights for string match positions
_TITLE_BONUS = 0.10
_TAG_BONUS = 0.05
_KEYWORD_BONUS = 0.03


def _string_match_entry(entry: dict, terms: list[str]) -> tuple[float, list[str]]:
    """Score an index entry against search terms via string matching.

    Returns (bonus_score, list_of_match_descriptions).
    """
    title = entry.get("title", "").lower()
    tags = [t.lower() for t in entry.get("metadata", {}).get("tags", [])]
    keywords = [k.lower() for k in entry.get("metadata", {}).get("keywords", [])]

    bonus = 0.0
    matches: list[str] = []

    for term in terms:
        t = term.lower()
        if t in title:
            bonus += _TITLE_BONUS
            matches.append(f"{term} (title)")
        if any(t in tag or tag in t for tag in tags):
            bonus += _TAG_BONUS
            matches.append(f"{term} (tag)")
        if any(t in kw or kw in t for kw in keywords):
            bonus += _KEYWORD_BONUS
            matches.append(f"{term} (keyword)")

    return bonus, matches


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_build(user: str) -> None:
    """Build embeddings for all articles in the index (incremental)."""
    with _user_lock(user):
        _incremental_rebuild(user)


def _catalog_file_status(article_path: str, summary_path: str) -> str:
    article_exists = bool(article_path) and Path(article_path).is_file()
    summary_exists = bool(summary_path) and Path(summary_path).is_file()
    if article_exists and summary_exists:
        return "ok"
    if not article_exists and not summary_exists:
        return "both_missing"
    return "article_missing" if not article_exists else "summary_missing"


def _catalog_resolved_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        return str(path.resolve())
    stored_root = Path("data/summary_agent")
    try:
        relative_path = path.relative_to(stored_root)
    except ValueError:
        relative_path = path
    return str((KB_ROOT / relative_path).resolve())


def _catalog_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _catalog_entry_status(
    raw_entry: object,
    *,
    input_data: dict[str, object],
    output_data: dict[str, object],
    metadata: dict[str, object],
    slug: str,
) -> str:
    required_strings = (
        raw_entry.get("title") if isinstance(raw_entry, dict) else None,
        input_data.get("article_file_path"),
        output_data.get("summary_file_path"),
        metadata.get("url"),
        metadata.get("source"),
        metadata.get("saved_at"),
    )
    string_lists = (metadata.get("tags"), metadata.get("keywords"))
    valid = (
        isinstance(raw_entry, dict)
        and isinstance(raw_entry.get("input"), dict)
        and isinstance(raw_entry.get("output"), dict)
        and isinstance(raw_entry.get("metadata"), dict)
        and bool(slug)
        and all(isinstance(value, str) and bool(value.strip()) for value in required_strings)
        and all(isinstance(value, list) and all(isinstance(item, str) for item in value) for value in string_lists)
    )
    return "ok" if valid else "invalid"


def _catalog_vector_status(
    *,
    row_index: int,
    alignment_status: str,
    vectors: np.ndarray | None,
) -> str:
    if alignment_status != "exact":
        return "alignment_mismatch"
    if vectors is None or vectors.ndim != 2 or vectors.shape[1] != EMBEDDING_DIM:
        return "vector_shape_mismatch"
    if row_index >= vectors.shape[0]:
        return "vector_shape_mismatch"
    vector = vectors[row_index]
    if not np.all(np.isfinite(vector)) or not np.any(vector != 0):
        return "zero_or_nonfinite"
    return "ok"


def cmd_list_article_records(user: str) -> None:
    """Print a stable, versioned JSONL catalog without exposing private stores."""
    with _user_lock(user):
        index = _load_index(user)
        vectors, manifest_slugs = _load_vectors(_embeddings_dir(user))
        index_slugs = [
            _slug_from_path(
                str(
                    raw_entry.get("output", {}).get("summary_file_path", "")
                    if isinstance(raw_entry, dict) and isinstance(raw_entry.get("output"), dict)
                    else ""
                )
            )
            for raw_entry in index
        ]
        alignment_status = _check_alignment(manifest_slugs, index_slugs)

        vector_ndim = int(vectors.ndim) if vectors is not None else 0
        vector_rows = int(vectors.shape[0]) if vectors is not None and vectors.ndim >= 1 else 0
        vector_dim = int(vectors.shape[1]) if vectors is not None and vectors.ndim == 2 else 0
        header = {
            "record_type": "catalog",
            "schema_version": 1,
            "user": user,
            "index_rows": len(index),
            "manifest_rows": len(manifest_slugs or []),
            "vector_rows": vector_rows,
            "vector_ndim": vector_ndim,
            "vector_dim": vector_dim,
            "expected_vector_dim": EMBEDDING_DIM,
            "alignment_status": alignment_status,
        }
        print(json.dumps(header, ensure_ascii=False, sort_keys=True))

        for row_index, raw_entry in enumerate(index):
            entry = raw_entry if isinstance(raw_entry, dict) else {}
            input_data: dict[str, object] = entry.get("input") if isinstance(entry.get("input"), dict) else {}
            output_data: dict[str, object] = entry.get("output") if isinstance(entry.get("output"), dict) else {}
            metadata: dict[str, object] = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            article_path = _catalog_resolved_path(input_data.get("article_file_path"))
            summary_path = _catalog_resolved_path(output_data.get("summary_file_path"))
            url = str(metadata.get("url") or "")
            slug = index_slugs[row_index] if row_index < len(index_slugs) else ""
            entry_status = _catalog_entry_status(
                raw_entry,
                input_data=input_data,
                output_data=output_data,
                metadata=metadata,
                slug=slug,
            )
            record = {
                "record_type": "article",
                "schema_version": 1,
                "user": user,
                "kb_slug": slug,
                "title": str(entry.get("title") or ""),
                "url": url,
                "canonical_url": canonicalize_url(url) if url else "",
                "source": str(metadata.get("source") or ""),
                "saved_at": str(metadata.get("saved_at") or ""),
                "tags": _catalog_string_list(metadata.get("tags")),
                "keywords": _catalog_string_list(metadata.get("keywords")),
                "article_file_path": article_path,
                "summary_file_path": summary_path,
                "entry_status": entry_status,
                "file_status": _catalog_file_status(article_path, summary_path),
                "vector_status": _catalog_vector_status(
                    row_index=row_index,
                    alignment_status=alignment_status,
                    vectors=vectors,
                ),
            }
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))


def cmd_add(slug: str, user: str) -> None:
    """Add or update the embedding for a single article.

    Uses two-phase locking: Phase 1 (no lock) prepares the embedding vector
    via OpenAI API call. Phase 2 (locked) re-reads state and writes vectors.
    This keeps the lock duration short (milliseconds) in the common case.

    Safety: index is append-only and entries are immutable once written, so the
    Phase 1 vector remains valid in Phase 2.  The Phase 2 re-read is a defensive
    check — if the append-only invariant is ever violated, we fail fast rather
    than silently writing a stale vector.
    """
    # Phase 1: prepare embedding (no lock, API call happens here)
    index = _load_index(user)
    slugs = _slugs_from_index(index)

    if slug not in slugs:
        print(
            f"Error: Slug '{slug}' not found in index.json",
            file=sys.stderr,
        )
        sys.exit(1)

    target_entry = index[slugs.index(slug)]

    summary_path = target_entry.get("output", {}).get("summary_file_path", "")
    if not summary_path:
        print(f"Error: Index entry for slug '{slug}' has no summary_file_path", file=sys.stderr)
        sys.exit(1)

    client = _get_openai_client()
    overview = _extract_overview(summary_path)
    scenarios = _extract_suitable_scenarios(summary_path)
    text = build_embedding_text(target_entry, overview, scenarios)
    new_vec = get_embeddings(client, [text])[0]

    # Phase 2: update vectors (locked)
    with _user_lock(user):
        index = _load_index(user)
        slugs = _slugs_from_index(index)

        if slug not in slugs:
            print(f"Error: Slug '{slug}' disappeared from index.json", file=sys.stderr)
            sys.exit(1)

        target_idx = slugs.index(slug)
        emb_dir = _embeddings_dir(user)
        vectors, manifest_slugs = _load_vectors(emb_dir)
        alignment = _check_alignment(manifest_slugs, slugs)

        if alignment == "mismatch":
            print("Alignment mismatch detected, rebuilding first...", file=sys.stderr)
            _incremental_rebuild(user)
            vectors, manifest_slugs = _load_vectors(emb_dir)
            if vectors is None or vectors.shape[0] != len(slugs):
                print("Error: Rebuild failed to produce correct vectors.", file=sys.stderr)
                sys.exit(1)

        if vectors is not None:
            if vectors.shape[0] < len(slugs):
                padding = np.zeros((len(slugs) - vectors.shape[0], EMBEDDING_DIM), dtype=np.float32)
                vectors = np.vstack([vectors, padding])
        else:
            vectors = np.zeros((len(slugs), EMBEDDING_DIM), dtype=np.float32)

        vectors[target_idx] = new_vec
        _save_vectors(emb_dir, vectors, slugs)

    print(
        f"Updated embedding for '{slug}' (total: {len(slugs)})",
        file=sys.stderr,
    )


def cmd_search(query: str, user: str, top_k: int, terms: list[str] | None = None) -> None:
    """Search the knowledge base using embedding similarity + optional string matching."""
    emb_dir = _embeddings_dir(user)
    vectors, manifest_slugs = _load_vectors(emb_dir)

    index = _load_index(user)
    slugs = _slugs_from_index(index)

    needs_rebuild = vectors is None or _check_alignment(manifest_slugs, slugs) == "mismatch"
    if needs_rebuild:
        reason = "not found" if vectors is None else "alignment mismatch"
        print(f"Embeddings {reason}, rebuilding...", file=sys.stderr)
        with _user_lock(user):
            _incremental_rebuild(user)
        vectors, manifest_slugs = _load_vectors(emb_dir)
        index = _load_index(user)
        slugs = _slugs_from_index(index)
        if vectors is None:
            print("Error: Failed to build embeddings.", file=sys.stderr)
            sys.exit(1)

    client = _get_openai_client()
    query_vec = get_embeddings(client, [query])[0]

    # Only compare against rows that exist in vectors
    n_vectors = min(vectors.shape[0], len(slugs))
    similarities = _cosine_similarity(query_vec, vectors[:n_vectors])

    # Compute combined scores for all articles
    scored: list[tuple[int, float, float, float, list[str]]] = []
    for i in range(n_vectors):
        emb_score = float(similarities[i])
        if terms:
            str_bonus, str_matches = _string_match_entry(index[i], terms)
        else:
            str_bonus, str_matches = 0.0, []
        combined = emb_score + str_bonus
        scored.append((i, combined, emb_score, str_bonus, str_matches))

    # Sort by combined score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    results = []
    for i, combined, emb_score, str_bonus, str_matches in scored[:top_k]:
        # Skip if both signals are weak
        if emb_score < MIN_SCORE_THRESHOLD and str_bonus == 0:
            continue
        entry = index[i]
        result = {
            "slug": slugs[i],
            "title": entry.get("title", slugs[i]),
            "score": round(combined, 4),
            "embedding_score": round(emb_score, 4),
            "tags": entry.get("metadata", {}).get("tags", []),
            "summary_file": entry.get("output", {}).get("summary_file_path", ""),
        }
        if str_matches:
            result["string_matches"] = str_matches
        results.append(result)

    output = {
        "results": results,
        "query": query,
        "total_indexed": n_vectors,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _entry_url_match_payload(entry: dict) -> dict:
    return {
        "found": True,
        "title": entry.get("title", ""),
        "slug": _slug_from_path(entry.get("output", {}).get("summary_file_path", "")),
        "summary_file_path": entry.get("output", {}).get("summary_file_path", ""),
        "article_file_path": entry.get("input", {}).get("article_file_path", ""),
        "saved_at": entry.get("metadata", {}).get("saved_at", ""),
    }


def _find_entry_by_canonical_url(index: list[dict], url: str) -> dict | None:
    canonical_url = canonicalize_url(url)
    if not canonical_url:
        return None
    for entry in index:
        entry_url = entry.get("metadata", {}).get("url", "")
        if canonicalize_url(entry_url) == canonical_url:
            return entry
    return None


def check_url_in_index(url: str, user: str) -> dict | None:
    """Look up a URL in the user's index. Returns the entry summary dict or None."""
    entry = _find_entry_by_canonical_url(_load_index(user), url)
    if entry is None:
        return None
    return _entry_url_match_payload(entry)


def cmd_check_url(url: str, user: str) -> None:
    """Check if a URL already exists in the index. Outputs JSON."""
    result = check_url_in_index(url, user)
    print(json.dumps(result if result is not None else {"found": False}, ensure_ascii=False, indent=2))


def cmd_verify_url_vector(url: str, user: str) -> None:
    """Verify one URL against a consistent index, manifest, and vector snapshot."""
    with _user_lock(user):
        index = _load_index(user)
        index_slugs = _slugs_from_index(index)
        vectors, manifest_slugs = _load_vectors(_embeddings_dir(user))
        entry = _find_entry_by_canonical_url(index, url)
        derived_slug = _slug_from_path(entry.get("output", {}).get("summary_file_path", "")) if entry else None
        kb_slug = derived_slug or None

        manifest_rows = len(manifest_slugs) if manifest_slugs is not None else None
        vector_ndim = int(vectors.ndim) if vectors is not None else None
        vector_rows = int(vectors.shape[0]) if vectors is not None and vectors.ndim == 2 else None
        vector_dim = int(vectors.shape[1]) if vectors is not None and vectors.ndim == 2 else None
        if entry is not None and not kb_slug:
            status = "index_entry_malformed"
        elif _check_alignment(manifest_slugs, index_slugs) != "exact":
            status = "alignment_mismatch"
        elif (
            vectors is None
            or vectors.ndim != 2
            or vectors.shape[0] != len(index_slugs)
            or vectors.shape[1] != EMBEDDING_DIM
        ):
            status = "vector_shape_mismatch"
        elif entry is None:
            status = "url_not_found"
        else:
            vector = vectors[index_slugs.index(kb_slug)]
            status = "ok" if np.isfinite(vector).all() and np.linalg.norm(vector) > 0 else "zero_or_nonfinite"

        print(
            json.dumps(
                {
                    "status": status,
                    "kb_slug": kb_slug,
                    "index_rows": len(index_slugs),
                    "manifest_rows": manifest_rows,
                    "vector_rows": vector_rows,
                    "vector_ndim": vector_ndim,
                    "vector_dim": vector_dim,
                    "expected_vector_dim": EMBEDDING_DIM,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def cmd_list_keywords(user: str) -> None:
    """List all unique keywords from the index. Outputs JSON array."""
    index = _load_index(user)
    all_keywords = [kw for entry in index for kw in entry.get("metadata", {}).get("keywords", [])]
    unique_keywords = list(dict.fromkeys(all_keywords))
    print(json.dumps(unique_keywords, ensure_ascii=False, indent=2))


_REPO_HOSTS = frozenset({"github.com", "gitlab.com", "gitee.com", "bitbucket.org", "codeberg.org"})
_GIT_REPO_RESERVED_PREFIXES = frozenset(
    {
        "about",
        "collections",
        "enterprise",
        "events",
        "explore",
        "features",
        "login",
        "marketplace",
        "new",
        "notifications",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "settings",
        "signup",
        "topics",
        "trending",
    }
)
_HUGGINGFACE_RESERVED_PREFIXES = frozenset({"blog", "docs", "join", "pricing", "settings"})
_PKG_GO_RESERVED_PREFIXES = frozenset({"about", "license", "search"})


def _normalized_hostname(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        return hostname[4:]
    return hostname


def _url_path_parts(url: str) -> list[str]:
    parsed = urlparse(url)
    return [part for part in parsed.path.strip("/").split("/") if part]


def _looks_like_git_repo_path(parts: list[str]) -> bool:
    return len(parts) >= 2 and parts[0].lower() not in _GIT_REPO_RESERVED_PREFIXES


def is_reusable_project_url(url: str) -> bool:
    """Return True when a URL points to directly reusable code, package, or skill source."""
    if not isinstance(url, str):
        return False
    raw = url.strip()
    if not raw:
        return False

    host = _normalized_hostname(raw)
    if not host:
        return False
    parts = _url_path_parts(raw)

    if host in _REPO_HOSTS:
        return _looks_like_git_repo_path(parts)
    if host == "raw.githubusercontent.com":
        return len(parts) >= 3
    if host == "gist.github.com":
        return bool(parts) and parts[0].lower() not in _GIT_REPO_RESERVED_PREFIXES
    if host == "sourceforge.net":
        return len(parts) >= 2 and parts[0].lower() in {"projects", "p"}
    if host in {"huggingface.co", "hf.co"}:
        return bool(parts) and parts[0].lower() not in _HUGGINGFACE_RESERVED_PREFIXES
    if host == "pypi.org":
        return len(parts) >= 2 and parts[0].lower() == "project"
    if host == "npmjs.com":
        return len(parts) >= 2 and parts[0].lower() == "package"
    if host == "crates.io":
        return len(parts) >= 2 and parts[0].lower() == "crates"
    if host == "pkg.go.dev":
        return bool(parts) and parts[0].lower() not in _PKG_GO_RESERVED_PREFIXES
    if host == "rubygems.org":
        return len(parts) >= 2 and parts[0].lower() == "gems"
    return False


def _normalize_project_url(url: str) -> str:
    """Normalize URL for dedup: lowercase, strip slashes, GitHub org/repo only."""
    url = url.strip().lower().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.hostname in ("github.com", "www.github.com"):
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return f"github.com/{parts[0]}/{parts[1]}"
    return url


def _projects_path(user: str) -> Path:
    return KB_ROOT / user / "open_source_projects.json"


def _load_projects(user: str) -> list[dict]:
    path = _projects_path(user)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, list):
        print(f"Error: Expected JSON array in {path}, got {type(data).__name__}", file=sys.stderr)
        sys.exit(1)
    return data


def _save_projects(user: str, projects: list[dict]) -> None:
    path = _projects_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def cmd_add_project(project_json: str, user: str) -> None:
    """Add an open source project to the tracking list.

    Required JSON fields: name, url, description.
    Optional JSON fields: source_url, tags, keywords, saved_at.
    """
    try:
        data = json.loads(project_json)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    required = ["name", "url", "description"]
    missing = [f for f in required if not isinstance(data.get(f), str) or not data[f].strip()]
    if missing:
        print(f"Error: Missing required fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    new_url = data["url"].strip()
    if not is_reusable_project_url(new_url):
        print(
            f"Error: Project URL must point to reusable code or a skill: {new_url!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    for field in ("tags", "keywords"):
        if field in data:
            if not isinstance(data[field], list):
                print(f"Error: '{field}' must be a JSON array, got {type(data[field]).__name__}", file=sys.stderr)
                sys.exit(1)
            if not all(isinstance(item, str) for item in data[field]):
                print(f"Error: '{field}' must contain only strings", file=sys.stderr)
                sys.exit(1)

    from datetime import datetime

    project = {
        "name": data["name"].strip(),
        "url": new_url,
        "description": data["description"].strip(),
        "source_url": data.get("source_url", ""),
        "tags": data.get("tags", []),
        "keywords": data.get("keywords", []),
        "saved_at": data.get("saved_at", datetime.now().strftime("%Y-%m-%d %H:%M")),
    }

    with _user_lock(user):
        projects = _load_projects(user)

        new_name = project["name"]
        norm_url = _normalize_project_url(new_url)

        # Dedup: non-empty normalized URL matches → already tracked
        if norm_url:
            for existing in projects:
                if _normalize_project_url(existing.get("url", "")) == norm_url:
                    print(
                        json.dumps(
                            {
                                "added": False,
                                "reason": "url_exists",
                                "name": existing.get("name"),
                                "total": len(projects),
                            },
                            ensure_ascii=False,
                        )
                    )
                    return
        # Empty URL: dedup by name instead
        else:
            for existing in projects:
                if existing.get("name", "").strip() == new_name:
                    print(
                        json.dumps(
                            {
                                "added": False,
                                "reason": "name_exists",
                                "name": existing.get("name"),
                                "total": len(projects),
                            },
                            ensure_ascii=False,
                        )
                    )
                    return

        projects.append(project)
        _save_projects(user, projects)

    print(json.dumps({"added": True, "name": project["name"], "total": len(projects)}, ensure_ascii=False, indent=2))


def cmd_list_projects(user: str) -> None:
    """List all tracked open source projects. Outputs JSON array."""
    projects = _load_projects(user)
    print(json.dumps(projects, ensure_ascii=False, indent=2))


def cmd_append_entry(entry_json: str, user: str) -> None:
    """Append a new entry to index.json from a JSON string.

    Required JSON fields: title, slug, tags (list), keywords (list).
    Optional JSON fields: source, url, saved_at, model_name.
    File paths are derived from slug + user.
    """
    try:
        data = json.loads(entry_json)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    required = ["title", "slug", "tags", "keywords"]
    missing = [f for f in required if f not in data]
    if missing:
        print(f"Error: Missing required fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    for field in ("tags", "keywords"):
        if not isinstance(data[field], list):
            print(f"Error: '{field}' must be a JSON array, got {type(data[field]).__name__}", file=sys.stderr)
            sys.exit(1)
        if not all(isinstance(item, str) for item in data[field]):
            print(f"Error: '{field}' must contain only strings", file=sys.stderr)
            sys.exit(1)

    slug = data["slug"]
    entry = {
        "title": data["title"],
        "input": {
            "article_file_path": f"data/summary_agent/articles/{slug}.md",
        },
        "output": {
            "summary_file_path": f"data/summary_agent/{user}/article_summaries/{slug}_output.md",
        },
        "metadata": {
            "source": data.get("source", ""),
            "url": data.get("url", ""),
            "saved_at": data.get("saved_at", ""),
            "model_name": data.get("model_name", ""),
            "tags": data["tags"],
            "keywords": data["keywords"],
        },
    }

    index_path = KB_ROOT / user / "index.json"

    with _user_lock(user):
        index = _load_index(user)

        existing_slugs = _slugs_from_index(index)
        if slug in existing_slugs:
            print(f"Error: Slug '{slug}' already exists in index.json", file=sys.stderr)
            sys.exit(1)

        existing_entry = _find_entry_by_canonical_url(index, data.get("url", ""))
        if existing_entry is not None:
            existing_slug = _slug_from_path(existing_entry.get("output", {}).get("summary_file_path", ""))
            print(
                f"Error: URL already exists in index.json as slug '{existing_slug}'",
                file=sys.stderr,
            )
            sys.exit(1)

        updated_index = [*index, entry]
        index_path.write_text(
            json.dumps(updated_index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps({"appended": True, "slug": slug, "total": len(updated_index)}, ensure_ascii=False, indent=2))


def cmd_save_from_batch(
    slug: str,
    user: str,
    batch_dir: str,
    meta_json: str,
    skip_embedding: bool = False,
    skip_projects: bool = False,
) -> None:
    """Atomic save: copy article + summary into KB, append index, update embeddings, register projects.

    Replaces the previous shell wrapper at
    .agents/skills/summarize-article/scripts/save_article.sh.
    """
    batch = Path(batch_dir)
    article_src = batch / f"{slug}_article.md"
    summary_src = batch / f"{slug}_summary.md"

    for src in (article_src, summary_src):
        if not src.is_file():
            print(f"Error: Required scratch file missing: {src}", file=sys.stderr)
            sys.exit(1)

    try:
        meta = json.loads(meta_json)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON in --meta-json: {exc}", file=sys.stderr)
        sys.exit(1)

    existing_entry = check_url_in_index(str(meta.get("url", "")), user)
    if existing_entry is not None:
        print(
            json.dumps(
                {
                    "saved": False,
                    "reason": "url_exists",
                    "slug": slug,
                    "existing": existing_entry,
                    "tag_normalization": tag_normalizer.report_to_json(
                        tag_normalizer.NormalizationReport([], False, None, False)
                    ),
                },
                ensure_ascii=False,
            )
        )
        return

    with _user_lock(user):
        if slug in _slugs_from_index(_load_index(user)):
            print(f"Error: Slug '{slug}' already exists in index.json", file=sys.stderr)
            sys.exit(1)
    normalization_report = tag_normalizer.normalize_meta_tags(
        meta,
        project_root=_project_root(),
        article_title=str(meta.get("title", "")),
        article_summary_excerpt=_read_summary_excerpt(summary_src),
    )
    normalized_meta_json = json.dumps(meta, ensure_ascii=False)

    article_dst = KB_ROOT / "articles" / f"{slug}.md"
    summary_dst = KB_ROOT / user / "article_summaries" / f"{slug}_output.md"
    article_dst.parent.mkdir(parents=True, exist_ok=True)
    summary_dst.parent.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(article_src, article_dst)
    shutil.copyfile(summary_src, summary_dst)

    cmd_append_entry(normalized_meta_json, user)

    embedding_updated = False
    if not skip_embedding:
        try:
            cmd_add(slug, user)
            embedding_updated = True
        except SystemExit as exc:
            if exc.code not in (0, None):
                print(
                    f"Warning: Embedding update failed for slug '{slug}'. Article files were saved.",
                    file=sys.stderr,
                )

    projects_added: list[str] = []
    if not skip_projects:
        for project in meta.get("projects", []) or []:
            if not isinstance(project, dict):
                continue
            project_name = str(project.get("name", "")).strip()
            project_url = project.get("url", "")
            if not is_reusable_project_url(project_url):
                print(
                    f"Skipping project '{project_name}': URL is not reusable code or skill content: {project_url!r}",
                    file=sys.stderr,
                )
                continue
            try:
                cmd_add_project(json.dumps(project, ensure_ascii=False), user)
                projects_added.append(project_name)
            except SystemExit as exc:
                if exc.code not in (0, None):
                    print(
                        f"Warning: Project tracking failed for '{project_name}'.",
                        file=sys.stderr,
                    )

    print(
        json.dumps(
            {
                "saved": True,
                "slug": slug,
                "summary_file_path": str(summary_dst),
                "article_file_path": str(article_dst),
                "embedding_updated": embedding_updated,
                "projects_added": projects_added,
                "tag_normalization": tag_normalizer.report_to_json(normalization_report),
            },
            ensure_ascii=False,
        )
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_project_env() -> None:
    # Config comes from Radar; never source ai-assistant .env.
    global KB_ROOT
    KB_ROOT = kb_root()


def main() -> None:
    """CLI entry point for embedding management."""
    _load_project_env()

    parser = argparse.ArgumentParser(description="Knowledge base embedding management")
    parser.add_argument("--user", default="default", help="User directory name")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Build embeddings for all articles")
    group.add_argument("--add", metavar="SLUG", help="Add/update embedding for a slug")
    group.add_argument("--search", metavar="QUERY", help="Search by query text")
    group.add_argument("--check-url", metavar="URL", help="Check if URL exists in index")
    group.add_argument(
        "--verify-url-vector",
        metavar="URL",
        help="Print diagnostic JSON for a URL's KB slug and vector snapshot",
    )
    group.add_argument("--list-keywords", action="store_true", help="List all unique keywords")
    group.add_argument(
        "--list-article-records",
        action="store_true",
        help="Print a versioned JSONL catalog of indexed articles and validation state",
    )
    group.add_argument("--append-entry", metavar="JSON", help="Append entry to index from JSON string")
    group.add_argument("--add-project", metavar="JSON", help="Add open source project from JSON string")
    group.add_argument("--list-projects", action="store_true", help="List all tracked open source projects")
    group.add_argument(
        "--save-from-batch",
        metavar="SLUG",
        help="Atomically save scratch article+summary into KB, append index, update embedding",
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of top results to return (default: 10)",
    )
    parser.add_argument(
        "--terms",
        help="Comma-separated precise search terms for string matching (used with --search)",
    )
    parser.add_argument(
        "--batch-dir",
        help="Scratch dir holding <slug>_article.md / <slug>_summary.md (used with --save-from-batch)",
    )
    parser.add_argument(
        "--meta-json",
        help="JSON string with index entry fields (used with --save-from-batch)",
    )
    parser.add_argument(
        "--skip-embedding",
        action="store_true",
        help="Skip the embedding update step (used with --save-from-batch)",
    )
    parser.add_argument(
        "--skip-projects",
        action="store_true",
        help="Skip auto-registering meta.projects entries (used with --save-from-batch)",
    )

    args = parser.parse_args()

    if args.build:
        cmd_build(args.user)
    elif args.add:
        cmd_add(args.add, args.user)
    elif args.search:
        terms = [t.strip() for t in args.terms.split(",") if t.strip()] if args.terms else None
        cmd_search(args.search, args.user, args.top_k, terms=terms)
    elif args.check_url:
        cmd_check_url(args.check_url, args.user)
    elif args.verify_url_vector:
        cmd_verify_url_vector(args.verify_url_vector, args.user)
    elif args.append_entry:
        cmd_append_entry(args.append_entry, args.user)
    elif args.list_keywords:
        cmd_list_keywords(args.user)
    elif args.list_article_records:
        cmd_list_article_records(args.user)
    elif args.add_project:
        cmd_add_project(args.add_project, args.user)
    elif args.list_projects:
        cmd_list_projects(args.user)
    elif args.save_from_batch:
        if not args.batch_dir or not args.meta_json:
            print("Error: --save-from-batch requires --batch-dir and --meta-json", file=sys.stderr)
            sys.exit(1)
        cmd_save_from_batch(
            slug=args.save_from_batch,
            user=args.user,
            batch_dir=args.batch_dir,
            meta_json=args.meta_json,
            skip_embedding=args.skip_embedding,
            skip_projects=args.skip_projects,
        )


if __name__ == "__main__":
    main()
