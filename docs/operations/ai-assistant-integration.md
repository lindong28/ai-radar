# ai-assistant summary-agent integration

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

Both scripts run with `cwd=$AI_ASSISTANT_ROOT`. AI Radar passes its normal environment except `VIRTUAL_ENV`, which is removed so the external implementation can choose its own runtime.

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
  "model": "summary-model"
}
```

Recognized fields:

- `exists` or `found`: truthy means an existing summary was found.
- `summary_file_path` or `summary_file`: absolute path or path relative to `$AI_ASSISTANT_ROOT`.
- `slug`: slug used to derive the `/wechat/<slug>` route.
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
- `result.llm_metadata.provider`, `backend_model`, `usage`, and `input_char_count`: optional but required for LLM usage metering. When present on the successful result path, AI Radar writes one `llm_usage` row with `stage='interpret'`. `usage` may use either OpenAI names (`prompt_tokens`, `completion_tokens`, `total_tokens`) or normalized names (`input_tokens`, `output_tokens`, `total_tokens`). Cache usage is normalized into nullable `llm_usage.cached_input_tokens` from `prompt_cache_hit_tokens`, then `prompt_tokens_details.cached_tokens`, or—when only miss tokens are present—`input_tokens - prompt_cache_miss_tokens`; absent facts remain `NULL`, while contradictory or out-of-bounds provider fields fail metering loudly without turning the already-paid summary into a provider retry. This landing contract is not an attempt ledger: a paid call without a successful result path or usable metadata can have no row, so downstream cost totals are recorded-row lower bounds and cohort statistics describe only recorded calls; see [ADR-023](../adr/023-define-recorded-row-measurement-scope.md) and [ISSUE-021](../issues/cost-observability.md#issue-021--interpret-usage-只记录下游成功样本漏掉已计费的失败响应).

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

Each item's interpretation outcome is upserted into `wechat_interpretations`; failures record the message in `error`. Errored items are retried automatically with exponential backoff: the first failure becomes eligible again after 15 minutes, and each further failure doubles the wait (15m, 30m, 1h, ... tracked in `error_retry_count`). After 8 retries the item is skipped permanently until its row is deleted by hand. A successful interpretation clears `error` and resets the counter. `pipeline.sh` caps each run at `--limit 30` items so a large error backlog drains across runs instead of holding the pipeline lock for hours.
