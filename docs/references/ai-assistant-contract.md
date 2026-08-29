# ai-assistant summary-agent integration

> Reader: [Developer] — whoever implements an external summary-agent that AI Radar's `interpret` stage will call. This file is the contract; the operational entry point (how to enable it, how to run it in production) is [operations/wechat-ingestion.md §微信文章解读与知识库回写](../operations/wechat-ingestion.md#微信文章解读与知识库回写).

`./run.sh interpret` can call an external article-summary implementation that is compatible with the `ai-assistant` summary-agent scripts. This integration is optional and disabled by default.

## Enablement

Set all required variables before running `interpret`:

```bash
AI_RADAR_ENABLE_INTERPRET=true
AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root
AI_RADAR_INTERPRET_USER=default
```

If `AI_RADAR_ENABLE_INTERPRET` is unset or false, `interpret` exits successfully with `skipped=true` and does not inspect `AI_ASSISTANT_ROOT`. If the flag is true but `AI_ASSISTANT_ROOT` is missing, `interpret` exits successfully with a skipped message. This keeps external repositories disabled unless explicitly enabled.

## Script layout

AI Radar expects these executable scripts under `$AI_ASSISTANT_ROOT`:

```text
$AI_ASSISTANT_ROOT/
└── agents/
    └── summary-agent/
        ├── summarize.sh
        └── run.sh
```

Both scripts run with `cwd=$AI_ASSISTANT_ROOT`. AI Radar removes `VIRTUAL_ENV` and overwrites all six standard proxy variables with the status-validated domain selector plus loopback `NO_PROXY`. This only controls subprocesses that honor the standard environment; an implementation that creates `trust_env=False`, a custom transport, native sockets or unmanaged descendants is outside the guarantee and must not claim compatibility without the receipt below.

## Selector compatibility receipt

Interpret remains disabled for an external root unless `$AI_ASSISTANT_ROOT/ai-radar-egress-contract-v2.json` exists and exactly matches this schema:

```json
{
  "schema_version": 2,
  "policy_id": "domain-routing-v1",
  "policy_sha256": "<64 lowercase hex of the tested T1 policy>",
  "egress_implementation_sha256": "<64 lowercase hex of the framed code/lock closure>",
  "parent_gcp_env_selector_only_test": "passed",
  "summarize_llm_selector_test": "passed",
  "check_url_local_only_test": "passed",
  "save_embedding_selector_test": "passed",
  "save_unknown_tag_classification_selector_test": "passed"
}
```

The machine authority for this v2 shape is `airadar.interpret.runner.expected_selector_compatibility_receipt`; a regression test parses this JSON example through the same builder so the reader contract cannot drift independently from the preflight consumer. The implementation digest is produced by `airadar.interpret.runner.egress_implementation_sha256` from repo-relative path plus file-byte framed records over these exact inputs:

- `agents/summary-agent/summarize.sh` and `agents/summary-agent/run.sh`
- root `pyproject.toml` and root `uv.lock`
- every non-test `.py` file under `agents/summary-agent/src/`
- every `.py` file under `shared/`

Missing fixed files, missing or empty code roots, symlinks/non-regular files, read failures, extra or missing receipt fields, invalid JSON, a non-`passed` test result, another policy identity, or a digest mismatch makes `interpret` exit 0 with `skip interpret: selector compatibility is unproven ...`; no external script is started. A new in-scope Python file changes the digest automatically. Runtime `docs/tags.md`, KB data and temporary outputs are deliberately excluded because they are mutable data, not executable implementation.

The trusted operator may write the receipt only after mechanically copying or comparing the exact target code bytes into an isolated mirror with temporary data/tmp/tags and running the production entrypoints through a fake selector. With all six parent proxy variables first set to an unusable endpoint, AI Radar's managed subprocess environment must be the only route observed for summarize LLM, save embedding and the extra unknown-tag classification LLM request. `run.sh --check-url` must complete against the local index without any selector request. Known-tag and unknown-tag save cases are separate test inputs. Old v1, a changed closure file and a changed attestation field must be rejected, while the valid v2 receipt must pass.

AI Radar consumes this trusted operator attestation; it does not authenticate its author or turn the receipt into live-route proof. The guarantee covers only the listed code/lock snapshot and the four production path assertions. It does not cover the installed Python/uv/site-packages bytes, future imports outside the two code roots, plugins, native sockets, custom transports or runtime monkeypatching. Update the external implementation or keep interpret skipped when these boundaries cannot be attested.

## Input article files

For each WeChat item, AI Radar writes a temporary Markdown file named `{item_id}.md`:

```markdown
# {normalized_title}

{content_text}
```

The file path is passed to `summarize.sh` as:

```bash
$AI_ASSISTANT_ROOT/agents/summary-agent/summarize.sh \
  --input "$TMP_FILE" \
  --user "$AI_RADAR_INTERPRET_USER" \
  --model ai-radar-interpret-deepseek
```

Before summarizing, AI Radar checks for existing KB content:

```bash
$AI_ASSISTANT_ROOT/agents/summary-agent/run.sh \
  --check-url "$WECHAT_URL" \
  --user "$AI_RADAR_INTERPRET_USER"
```

**Not every article reaches this query.** AI Radar skips the lookup entirely — no subprocess is spawned — for URLs the index provably cannot key: a `mp.weixin.qq.com` link whose query carries `__biz` (the long-form share link). Every such link has the path `/s`, so an index keyed on URL answers *some* article for all of them. The skip is on the call, not on its answer, because the lookup runs with `check=True` and a non-zero exit would block the article from being summarized even if the answer were discarded (`_index_cannot_distinguish` in `src/airadar/interpret/runner.py`). Short links (`/s/<token>`) carry their identity in the path and still go through this query.

## stdout JSON contracts

All script stdout must contain a JSON object. Extra log lines are allowed before the final JSON object; AI Radar scans stdout from the end and uses the last parseable JSON object.

### `run.sh --check-url`

The JSON object may expose the match fields at the top level or nested under `dedup`, `result`, or `data`:

```json
{
  "found": true,
  "slug": "article-slug",
  "summary_file_path": "data/summary_agent/default/article_summaries/article-slug_output.md",
  "recommendation": "必读",
  "save_reason": "URL already exists in KB",
  "tags": ["Agent"],
  "title": "被缓存那篇文章的标题",
  "model": "summary-model"
}
```

Recognized fields:

- `exists` or `found`: truthy means an existing summary was found.
- `summary_file_path` or `summary_file`: absolute path or path relative to `$AI_ASSISTANT_ROOT`.
- `slug`: slug used to derive the `/wechat/<slug>` route.
- `title`: **consumed, not decorative.** AI Radar compares it against the title of the article it is currently interpreting (`_check_url_hit(..., title=...)`); if the two name different articles, the hit is rejected and the article is summarized fresh. A hit that omits `title`, or states an empty one, is accepted as before — that shape is part of this contract and rejecting it would re-summarize at the implementer's expense. Returning a *wrong* title is the damaging case: on 2026-08-20 ten different articles were published under one article's summary because the index answered for URLs it could not distinguish and nothing cross-checked the title.
- `recommendation`, `save_reason`, `tags`, `model`: optional metadata copied into `wechat_interpretations`.

When a hit includes a readable summary file, AI Radar does not call `summarize.sh`.

### `summarize.sh --input`

For a new article, `summarize.sh` must return:

```json
{
  "ok": true,
  "batch_dir": "data/summary_agent/default/batches/20260612",
  "result": {
    "slug": "article-slug",
    "save_decision": true,
    "save_reason": "has reusable engineering value",
    "recommendation": "值得一看",
    "tags": ["Agent", "工程化"],
    "model": "summary-model",
    "llm_metadata": {
      "requested_model": "ai-radar-interpret-deepseek",
      "backend_attempted": "deepseek-ark-first",
      "backend_used": "openai-api",
      "provider": "ark",
      "backend_model": "ark-deepseek-model",
      "fallback_used": false,
      "criteria_reason_source": "json",
      "input_char_count": 12345,
      "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
        "input_tokens": 1000,
        "output_tokens": 200,
        "prompt_tokens_details": {
          "cached_tokens": 800
        }
      }
    }
  }
}
```

Recognized fields:

- `batch_dir`: absolute path or path relative to `$AI_ASSISTANT_ROOT`.
- `result.slug`: base slug. AI Radar may normalize it for WeChat title artifacts or uniqueness.
- `result.save_decision`: true means save back to the external KB and show the article on `/wechat`.
- `result.save_reason`, `result.recommendation`, `result.tags`, `result.model` or `result.model_name`: metadata copied into `wechat_interpretations`.
- `result.llm_metadata.criteria_reason_source`: `json` when the reason came from the trailing JSON, or `markdown_value_judgment_line` when the compatible summary-agent recovered the only non-empty parenthesized reason on a matching `推荐等级` line inside the `价值判断` section. Fresh results from a compatible summary-agent must contain exactly one of those values; AI Radar rejects a missing or unknown value before attempting a KB save. Cached legacy results may omit it, and absence means unknown rather than `json`.
- `result.llm_metadata.provider`, `backend_model`, `usage`, and `input_char_count`: optional but required for LLM usage metering. When present on the successful result path, AI Radar writes one `llm_usage` row with `stage='interpret'`. `usage` may use either OpenAI names (`prompt_tokens`, `completion_tokens`, `total_tokens`) or normalized names (`input_tokens`, `output_tokens`, `total_tokens`). Cache usage is normalized into nullable `llm_usage.cached_input_tokens` from `prompt_cache_hit_tokens`, then `prompt_tokens_details.cached_tokens`, or—when only miss tokens are present—`input_tokens - prompt_cache_miss_tokens`. Absent facts remain `NULL`; contradictory or out-of-bounds provider fields fail metering loudly without turning the already-paid summary into a provider retry.

AI Radar persists provenance independently of metering. `wechat_interpretations.criteria_reason_source` is the authoritative accepted-reason source, `wechat_interpretations.slug` is the authoritative AI Radar interpretation slug, and `wechat_interpretations.interpret_user` identifies the per-user Summary Agent namespace. These nullable columns are additive: historical rows and cached legacy results may lack the source marker, and readers must treat that as unverified. `llm_usage.item_id` remains the sole item identity in the usage ledger; `attribution_json` does not duplicate the item, interpretation slug, or reason source. A Markdown fallback also emits a pipeline stdout audit line with item ID, interpret user, interpretation slug, and the stored URL's SHA-256; the URL itself is not logged.

Measurement-scope policy (not a field requirement): this landing contract is not an attempt ledger, so downstream totals are recorded-row lower bounds — see [ADR-023](../adr/023-define-recorded-row-measurement-scope.md) and [ISSUE-021](../issues/cost-observability.md#issue-021--interpret-usage-只记录下游成功样本漏掉已计费的失败响应).

AI Radar reads the Markdown summary from:

```text
{batch_dir}/{result.slug}_summary.md
```

If `save_decision` is true, AI Radar patches:

```text
{batch_dir}/{result.slug}_meta.json
```

with the article URL, source, publish date, normalized title, and result fields, then calls:

```bash
$AI_ASSISTANT_ROOT/agents/summary-agent/run.sh \
  --save-from-batch "$SLUG" \
  --user "$AI_RADAR_INTERPRET_USER" \
  --batch-dir "$BATCH_DIR" \
  --meta-json "$PATCHED_META_JSON"
```

If `run.sh --save-from-batch` reports `Slug '<slug>' already exists in index.json`, AI Radar retries with a unique slug derived from the item id.

## Summary Markdown contract

The summary Markdown is stored in `wechat_interpretations.summary_md` and rendered on `/wechat/<slug>`. AI Radar also extracts card metadata from it:

- `### 文章概况`: first paragraph becomes the card `abstract`.
- `推荐等级：必读`, `推荐等级：值得一看`, or `推荐等级：可跳过`: fallback recommendation when JSON omits it.
- Tags should be provided in JSON `tags`; Markdown tags may still be present for readers.

Generated Markdown is treated as untrusted HTML input and sanitized before rendering.

## Index contract

Existing-summary metadata is read from:

```text
$AI_ASSISTANT_ROOT/data/summary_agent/{AI_RADAR_INTERPRET_USER}/index.json
```

AI Radar expects this file to be a JSON array. Each entry may include:

```json
{
  "output": {
    "summary_file_path": "data/summary_agent/default/article_summaries/article-slug_output.md"
  },
  "metadata": {
    "url": "https://mp.weixin.qq.com/s/example",
    "tags": ["Agent"],
    "model_name": "summary-model",
    "recommendation": "必读"
  }
}
```

The runner uses `metadata.url` for URL hits and `output.summary_file_path` or `output.summary_file` for slug and summary-file lookup.

## Failure retry semantics

Each item's interpretation outcome is upserted into `wechat_interpretations`; failures record the message in `error` (the subprocess stderr when the script exited non-zero). On the fresh-summary path, the exact `summary JSON missing non-empty criteria_reason` subprocess error is retried immediately once with the same command; retrying, recovered, and immediate-retry-exhausted outcomes are identified in stdout. No other subprocess or schema error gets this immediate retry. If the item still fails, it follows the normal exponential backoff: the first failure becomes eligible again after 15 minutes, and each further failure doubles the wait (15m, 30m, 1h, ... tracked in `error_retry_count`). After 8 retries the item is skipped permanently until its row is deleted by hand. A successful interpretation clears `error` and resets the counter. `pipeline.sh` caps each run at `--limit 30` items so a large error backlog drains across runs instead of holding the pipeline lock for hours.

## Verifying an implementation against this contract

Run these from the AI Radar checkout after your scripts are in place. Nothing here writes to the external KB unless step 3 finds an article that has not been summarized yet.

1. **Both scripts are discoverable and executable** — otherwise `interpret` silently skips instead of failing:

   ```bash
   test -x "$AI_ASSISTANT_ROOT/agents/summary-agent/summarize.sh"
   test -x "$AI_ASSISTANT_ROOT/agents/summary-agent/run.sh"
   ```

2. **`--check-url` returns a parseable JSON object** — AI Radar scans stdout from the end and uses the last JSON object, so log lines before it are fine, but nothing may follow it:

   ```bash
   cd "$AI_ASSISTANT_ROOT"
   ./agents/summary-agent/run.sh --check-url 'https://mp.weixin.qq.com/s/<token>' \
     --user "${AI_RADAR_INTERPRET_USER:-default}" | tail -n 1 | jq .
   ```

   Expected: an object carrying `found`/`exists` (top level or nested under `dedup`/`result`/`data`), and, when it reports a hit, `slug`, `summary_file_path` and the `title` of the article that summary is actually about. A hit whose `title` names a different article is the failure this check exists to catch.

3. **One real article end to end** — `--limit 1` keeps the blast radius to a single item:

   ```bash
   AI_RADAR_ENABLE_INTERPRET=true \
   AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root \
   ./run.sh interpret --limit 1
   ```

   Read stdout, not the exit code — `interpret` exits `0` in every one of these cases:

   | stdout | meaning |
   |---|---|
   | `interpret processed=1 errors=0` | the contract held for that item |
   | `interpret processed=1 errors=1` | the scripts ran but something in the exchange failed; the message is in `wechat_interpretations.error` |
   | `interpret skipped=true message=…` | never reached your scripts — inspect the message for the disabled flag, missing `AI_ASSISTANT_ROOT`/executables, or a missing/invalid/mismatched selector compatibility attestation |

4. **When it fails, where AI Radar says so.** Contract violations do not raise; they land as data:

   ```bash
   sqlite3 data/radar.db \
     "SELECT item_id, substr(error,1,200), error_retry_count FROM wechat_interpretations WHERE error IS NOT NULL ORDER BY processed_at DESC LIMIT 5;"
   ```

   A rejected-title hit is not an error row — it is a `WARNING` on the `airadar.interpret.runner` logger reading `Ignoring a cached summary for a different article: cached=… wanted=…`, followed by a normal fresh summarization. Repeated occurrences mean your index is answering for URLs it cannot key.
