# ai-assistant summary-agent integration

> Reader: [Developer] — whoever implements an external summary-agent consumed by AI Radar's `interpret` stage or manual KB archive importer. This file is the cross-repository interface contract, including the AI Radar receipt writer interface; production enablement and general operator runbooks belong to [operations/wechat-ingestion.md](../operations/wechat-ingestion.md).

`./run.sh interpret` can call an external article-summary implementation that is compatible with the `ai-assistant` summary-agent scripts. Separately, `./run.sh admin wechat-kb import` can consume the versioned read-only catalog described below to copy missing WeChat archive articles into AI Radar. The live interpretation integration is optional and disabled by default; the archive importer is an explicit maintenance command and is never part of `pipeline.sh`.

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
  "policy_id": "domain-routing-v2",
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

The trusted operator may write the receipt only after every path-level attestation check below passes in an isolated mirror with temporary data/tmp/tags:

| Check | Required observation |
|---|---|
| Mirror identity | The report records `closure_copy.source`, `closure_copy.mirror` and `closure_copy.match=true`; the two SHA-256 values equal the tested implementation SHA. A mismatch exits non-zero and no receipt writer is run. |
| Parent proxy isolation | All six parent proxy variables are first set to an unusable endpoint; the production entrypoints run through a fake selector, and AI Radar's managed subprocess environment is the only route observed for summarize LLM, save embedding and the extra unknown-tag classification LLM request. |
| Network fence | Isolated execution denies and reports non-loopback IP outbound attempts. The same run includes a non-loopback positive control that is denied and observed, plus a loopback negative control that succeeds with zero denial. |
| Local lookup | `run.sh --check-url` completes against the local index with zero selector requests. |
| Save-path identities | Known-tag and unknown-tag saves use separate inputs; their request payload identities and persisted embedding artifacts are observed independently. |
| Receipt rejection matrix | Old v1, a changed closure file and a changed attestation field are rejected; the valid v2 receipt returns `ok`. |

After those path-level tests pass, the operator writes the receipt through the tested AI Radar receipt writer rather than editing JSON by hand. The command must run under the same selector authority as the production interpret consumer: the same host, user, `HOME`, shell configuration and status command observation. If that identity cannot be established, do not write the receipt; run the writer in the target production consumer environment or keep interpret skipped.

```bash
uv run python -m airadar.interpret.receipt_writer \
  --assistant-root "$AI_ASSISTANT_ROOT" \
  --tested-policy-sha "$TESTED_POLICY_SHA256" \
  --tested-implementation-sha "$TESTED_IMPLEMENTATION_SHA256"
```

The receipt consumer check used below is intentionally separate from `./run.sh egress-preflight`, which verifies selector status but does not consume the receipt:

```bash
PYTHONPATH=src uv run python -c 'import os; from pathlib import Path; from airadar.interpret.runner import _preflight; print(_preflight(Path(os.environ["AI_ASSISTANT_ROOT"])))'
```

The writer's ordered control flow is implementation recheck → fresh selector-policy read → optional backup → atomic receipt write:

| Outcome | Observable result | File effects | Next action |
|---|---|---|---|
| Implementation SHA mismatch | Non-zero; reports attested and live implementation SHAs | Receipt unchanged; no backup | Rerun the full attestation against the current closure |
| Selector preflight failure or policy SHA mismatch | Non-zero; reports the failed preflight or attested/live policy SHAs | Receipt unchanged; no backup | Restore selector health if needed, then rerun the full attestation against the live policy |
| Timestamped backup path already exists | Non-zero; reports the conflicting path | Existing receipt and backup unchanged | Resolve the collision and rerun the writer with the same attested identities |
| All gates pass | Zero; reports policy SHA, implementation SHA, receipt path and backup path or `none` | If a receipt exists, preserve it as `ai-radar-egress-contract-v2.json.bak-<timestamp>`; atomically write the authoritative v2 receipt | Run the receipt consumer check defined above and require `(True, 'ok')` |
| Other filesystem error | Non-zero; completed receipt write is not confirmed | File state is unknown until the named receipt, backup and temporary-file readings below are collected | Require zero temporary files and either a consumer-accepted receipt or an explicit restore from a chosen backup followed by a new full attestation |

Other filesystem errors return non-zero without confirming a completed receipt write. Before retrying, record whether the receipt exists plus its SHA-256 and parsed v2 fields, every `ai-radar-egress-contract-v2.json.bak-*` path plus SHA-256, and the count of `.ai-radar-egress-contract-v2.json.*.tmp` files. Require zero temporary files and either a receipt accepted by the consumer check or an explicit restore from a chosen backup followed by a new full attestation.

AI Radar consumes this trusted operator attestation; it does not authenticate its author or turn the receipt into live-route proof. The guarantee covers only the listed code/lock snapshot and the listed v2 attestation fields. It does not cover the installed Python/uv/site-packages bytes, future imports outside the two code roots, plugins, Unix-domain sockets, custom non-IP transports or runtime monkeypatching. Update the external implementation or keep interpret skipped when these boundaries cannot be attested.

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

All script stdout must contain a JSON object. AI Radar first tries to parse the entire trimmed stdout as one JSON object; if that fails, it scans non-empty lines from the end and accepts the last parseable line that is itself a complete JSON object. Extra non-JSON log lines may precede or follow that compact one-line object. Pretty-printed multi-line JSON mixed with logs is not accepted.

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

## Interpret index contract

The `interpret` compatibility path reads existing-summary metadata from:

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

The runner uses `metadata.url` for URL hits and `output.summary_file_path` or `output.summary_file` for slug and summary-file lookup. This direct index read is not available to the archive importer; that consumer must use the locked, versioned catalog below.

## 只读文章目录 JSONL

`admin wechat-kb import` 不直接读取 `index.json`、manifest 或 NumPy 文件。它调用同一个稳定 wrapper，让 ai-assistant 在 per-user `fcntl` 锁内输出一份一致快照：

```bash
$AI_ASSISTANT_ROOT/agents/summary-agent/run.sh \
  --list-article-records \
  --user "$AI_RADAR_INTERPRET_USER"
```

stdout 是 JSON Lines，第一行必须是 schema header：

```json
{"record_type":"catalog","schema_version":1,"user":"dong_lin","index_rows":3448,"manifest_rows":3448,"vector_rows":3448,"vector_ndim":2,"vector_dim":1536,"expected_vector_dim":1536,"alignment_status":"exact"}
```

后续每行是一篇 index 文章，必需字段如下：

```json
{"record_type":"article","schema_version":1,"kb_slug":"article-slug","title":"文章标题","url":"https://mp.weixin.qq.com/s/example","canonical_url":"https://mp.weixin.qq.com/s/example","source":"公众号名","saved_at":"2026-02-10 12:00","tags":["视频生成"],"keywords":["Seedance"],"article_file_path":"/absolute/path/article.md","summary_file_path":"/absolute/path/article-slug_output.md","entry_status":"ok","file_status":"ok","vector_status":"ok"}
```

AI Radar 只接受 header 的 `record_type=catalog`、`schema_version=1`、`user` 与请求 namespace 一致、header `index_rows` 与随后 article 行数一致、三层 row count 相等、二维 1536 维向量和 `alignment_status=exact`。后续行必须全部是同版本的 `record_type=article`；不认识的 record type 或 schema version 使整份 catalog 失败，而不是猜测兼容。

文章行只有在 `entry_status=file_status=vector_status=ok`、URL 是 `mp.weixin.qq.com/s...`、canonical URL 与原 URL 的文章身份一致、slug 非空且两个绝对文件路径可读时才有资格导入。导入前还会以 UTF-8 读取 article/summary，并要求 article header 或 catalog fallback 能提供非空 title、author/source 和可解析的 published_at/saved_at。单篇不合格记录进入 `skipped_reasons`，不会阻止同一 catalog 中其它合格记录；Malformed index 条目因此应由 producer 输出成同版本、非 `ok` 的 article 行，便于 consumer 计数并跳过，而不是让目录静默截断。

`SUMMARY_AGENT_KB_ROOT` 可把目录根指向另一个持久数据 checkout，供隔离 worktree 或测试使用；正常维护者命令无需设置。这个 JSONL 是 ai-assistant 私有 store 的唯一跨仓读取面，字段增删或语义变化必须切换新的 `schema_version`，不能让 AI Radar 猜测兼容。

AI Radar 把这次调用登记为本地进程并设置 `UV_OFFLINE=1`；目录导出不得解析依赖或访问网络。目标 ai-assistant checkout 尚未安装依赖时命令会显式失败，维护者应先完成该仓正常安装，而不是让归档扫描在后台下载包。

### Archive import consumer contract

`admin wechat-kb import` 当前 CLI help 暴露 `--dry-run`、`--limit`、`--assistant-root`、`--user` 与 `--db-path`。开发者判断调用场景时使用以下边界；面向生产数据库的完整命令顺序、输出判读和回滚政策仍以 [摄取 runbook §手动补录 ai-assistant 知识库归档](../operations/wechat-ingestion.md#手动补录-ai-assistant-知识库归档) 为准。

| 场景 | 参数语义 |
|---|---|
| 验证 producer/consumer 契约及候选规模，不写数据库 | `--dry-run`；仍会导出并校验完整 catalog、读取目标数据库、按 canonical URL 判重并验证所有候选文件 |
| 有界补导 | `--limit N`；按 catalog 顺序只选择前 N 个合格且缺失的候选，未选择数写入 `remaining`，后续显式再运行 |
| 选择跨仓 producer 与 namespace | `--assistant-root` 指向兼容 checkout，`--user` 同时约束 catalog header 与落库 `interpret_user` |
| 选择目标实例 | `--db-path`；它决定判重、写入和 postcheck 的 SQLite，不从 ai-assistant 路径推断 |

consumer 先按 canonical URL 建立目标库现状。已有且已有 interpretation 的文章计入 `already_present`；已有 item 但缺 interpretation 的文章计入 `existing_without_interpretation`，本命令不会补写或覆盖该行。只有目标库完全缺失的合格文章进入候选集，因此重复运行是缺失补录，不是双向同步、修复既有行或重放全部 KB。

实际写入时，`admin/wechat_kb.py` 通过 `wechat_archive.py` 创建或收敛保留来源 `wx_ai_assistant_kb_archive`，并强制它保持 `kind=wechat`、`enabled=0`、`optional=1`、`wechat_only=1`。每篇候选同时插入 `items` 与 `wechat_interpretations`：`save_decision=1`、`kb_synced=1`、`model=ai-assistant-kb-archive`，原始 article/summary Markdown 留在本地 SQLite，`extra_json` 记录 origin、run id、KB slug、upstream canonical URL 与发布日期依据。保留来源参与 `/wechat` 可见性和微信跨源去重，但不进入公开 source API，也不成为 fetch 调度源。

一次非 dry-run 的所有写入共享一个事务。提交前 postcheck 要求本次 run 的 provenance item、保留来源 item、成功 interpretation、`/wechat` 可见行与 FTS 行数都等于 `imported`，同时公开来源计数仍为零；任何不一致整笔回滚。没有选中候选时不创建来源也不写库，receipt 的 `postcheck` 为 `not_needed`。选中过候选时会执行 postcheck，检查一致即为 `passed`；若所选文件在写入前的第二次校验中全部失效，`imported=0`、`changed=false` 与 `postcheck=passed` 可以同时成立。run id 是 provenance，不是成功批次的通用删除或回滚句柄。

## Interpret failure retry semantics

Each item's interpretation outcome is upserted into `wechat_interpretations`; failures record the message in `error` (the subprocess stderr when the script exited non-zero). On the fresh-summary path, the exact `summary JSON missing non-empty criteria_reason` subprocess error is retried immediately once with the same command; retrying, recovered, and immediate-retry-exhausted outcomes are identified in stdout. No other subprocess or schema error gets this immediate retry. If the item still fails, it follows the normal exponential backoff: the first failure becomes eligible again after 15 minutes, and each further failure doubles the wait (15m, 30m, 1h, ... tracked in `error_retry_count`). After 8 retries the item is skipped permanently until its row is deleted by hand. A successful interpretation clears `error` and resets the counter. `pipeline.sh` caps each run at `--limit 30` items so a large error backlog drains across runs instead of holding the pipeline lock for hours.

## Verifying interpret compatibility

Run these from the AI Radar checkout after your scripts are in place. Nothing here writes to the external KB unless step 3 finds an article that has not been summarized yet.

1. **Both scripts are discoverable and executable** — otherwise `interpret` silently skips instead of failing:

   ```bash
   test -x "$AI_ASSISTANT_ROOT/agents/summary-agent/summarize.sh"
   test -x "$AI_ASSISTANT_ROOT/agents/summary-agent/run.sh"
   ```

2. **`--check-url` returns a parseable JSON object** — stdout must be either one JSON object, or contain at least one line that is itself a complete JSON object; AI Radar scans non-empty lines from the end and accepts the last parseable object line, so surrounding non-JSON logs are fine, but pretty-printed multi-line JSON mixed with logs is not:

   ```bash
   cd "$AI_ASSISTANT_ROOT"
   output="$(
     ./agents/summary-agent/run.sh --check-url 'https://mp.weixin.qq.com/s/<token>' \
       --user "${AI_RADAR_INTERPRET_USER:-default}"
   )" || exit $?
   if parsed="$(printf '%s\n' "$output" | jq -e 'select(type == "object")' 2>/dev/null)"; then
     printf '%s\n' "$parsed"
   else
     printf '%s\n' "$output" | jq -Rsc '
       split("\n")
       | reverse
       | map(select(length > 0) | try fromjson catch empty | select(type == "object"))
       | first // error("no JSON object line")
     '
   fi
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
