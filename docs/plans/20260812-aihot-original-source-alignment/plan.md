> **Archive status**: 已归档，在一条独立 task 分支上执行完成；决策以 [ADR-047](../../adr/047-use-controlled-original-web-lists-for-aihot-source-alignment.md) 的形态落在 `main`，那是可定位的权威记录——原 task 分支的 commit 从 `main` 不可达。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> 当前行为与取证边界：见 README「信源」节、[docs/contracts/ux-contract.md](../../contracts/ux-contract.md) 的 TL-2/AB-1，以及 [docs/architecture.md](../../architecture.md) 的摄取与 API 两节。**正文里的 ADR 编号是集成前的旧号（ADR-024）**：落盘时当前 `main` 已把 ADR-023 分配出去、并发工作又占了 ADR-024–045，故最终编号为 ADR-047。以下为原 plan 正文，未修改。

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

# AIHOT original-source alignment plan

## Inputs and authority

- User objective: make the main AI Radar timelines read the same complete current source universe as AIHOT, while keeping AI Radar's paid `wx_mp2rss` source exclusively for the separate `/wechat` interpretation page.
- Comparison oracle: AIHOT v1 rolling timeline API. It is used only to discover and reconcile source membership; production must fetch each publisher's original RSS/Atom, website, platform API, or X API endpoint. A rolling traversal is an observation lower bound, not proof that an unobserved source has left AIHOT.
- Membership authority: the user chose the monotonic union of complete rolling observations. A later observation may add candidates but does not delete a quiet source. Retirement requires either official shutdown/migration evidence, or absence from daily rolling observations for at least 30 consecutive days followed by explicit review; a user decision may always retire a source.
- Frozen evidence: [`artifacts/target-baseline.md`](artifacts/target-baseline.md) records the 2026-08-12 traversal and long-term selected snapshot. Two later successful rolling traversals are summarized in the same tracked artifact together with hashes and union deltas; `/tmp` copies are disposable, not authority. [`artifacts/source-evidence.md`](artifacts/source-evidence.md) separately records every non-X endpoint's provenance, response identity, probe time, parser-facing structure, and unresolved implementation obligations.
- Code authority: `tests/fixtures/aihot_sources.json -> scripts/render_sources_from_contract.py -> data/sources.toml -> src/airadar/sources/loader.py -> src/airadar/sources/sync.py -> src/airadar/fetcher/runner.py`. Production config is explicitly schema v2 and uses `fetch_url`; versionless/v1 config continues to use legacy `url`. Existing X state ownership remains in `src/airadar/fetcher/x_api.py` and `src/airadar/sources/x_state.py`.
- Product contracts: `README.md`, `docs/architecture.md`, and `docs/contracts/ux-contract.md` describe the source inventory and user-visible source page.
- AIHOT audit interface: `scripts/audit_aihot_sources.py --output <persistent-json>` reads `GET https://aihot.virxact.com/api/v1/items?mode=all&by=timeline&window=7d&limit=100&cursor=<cursor>`, starts without a cursor, follows each response's `nextCursor` only while `hasMore=true`, and succeeds only after a page with `hasMore=false`. It records endpoint/query semantics, capture time, pages/items/sources, terminal status, source projection, and SHA-256 in the output artifact.
- Membership-transition authority: each main row in `tests/fixtures/aihot_sources.json` has a stable `derived_aihot_identity` and `aihot_aliases`. X identity is `x:<casefolded username>` derived from observed original X URLs; non-X identity is `feed:<slug>` or `web:<registry_key>` and an observation joins only through an explicit alias. Public display names are values, never join keys. The reconciliation command reports `matched`, `renamed`, `ambiguous`, and `unmapped`; only unambiguous matches may update the contract.
- Machine source contract: `tests/fixtures/aihot_sources.json` is the single editable current projection oracle. Every row contains `derived_aihot_identity`, alternate/historical `aihot_aliases`, `slug`, public `name`, `kind`, exact `tier`, `enabled`, internal `fetch_url`, human-readable `homepage_url`, `icon_url`, `ai_radar_main_timeline_member`, and `meta`; only the optional WeChat row adds `required_env`, `wechat_only`, `optional`, and `public_url_override`. Config is rendered byte-for-byte from this contract. The safe human/public projection is `/api/v2/sources`; the user chose to freeze `/api/v1/sources` compatibility, so v1 excludes `kind="web"` rows rather than introducing a new legacy enum value.

## Outcome and scope (L1)

The delivered product has one explicit main-timeline source universe and one explicit exception:

- Main-timeline target: the tracked monotonic observation union, currently 161 non-WeChat AIHOT sources: 109 X accounts and 52 other original sources. These numbers describe the current frozen source map; tests derive counts from that map rather than treating the prose constants as a second authority.
- Extra AI Radar surface: one `wx_mp2rss` source remains configured as `kind="wechat"` for `/wechat`; it is excluded from main-timeline alignment counts and behavior.
- Final checked-in config: one row per source-map member plus one excluded `wx_mp2rss` row. At the current frozen map that is 162 configured rows. When `MP2RSS_FEED_URL` is absent, loader/DB/API/About contain only the 161 main sources and `/wechat` has no configured feed. When it is set, all 162 load internally, but the public API/UI project the WeChat row through its safe public/homepage URL and never reveal the resolved paid-feed URL.
- Production never calls AIHOT to ingest content and never uses Mp2RSS to supply `/` or `/all`.
- This task aligns source membership and original-source retrieval. It does not claim equivalence for AIHOT's downstream cleaning, filtering, labels, ranking, scoring, summaries, or curated selection.

The source-set delta from the current worktree is fixed:

- Add seven X accounts observed after the frozen baseline: `openclaw`, `SpaceXAI`, `WorkBuddy_AI`, `PeterMcCrory`, `deepseek_ai`, `zhang_benita`, and `SiliconFlowAI`.
- Keep all 102 frozen-baseline X accounts, including temporarily quiet `HuaweiCloud1`, for 109 total X API accounts.
- Add or correct all 52 non-X sources listed below, including the later-observed DeepSeek API update log.
- Remove eight non-AIHOT feed sources from the main pool: `lilianweng`, `sebastianraschka`, `latent_space`, `importai`, `hn_ai`, `lobsters_ai`, `the_batch`, and `last_week_ai`.
- Remove the non-AIHOT `simonw_mastodon` source. Keep the target `simonw` blog feed.
- Keep `wx_mp2rss` unchanged except for count/inventory assertions that explicitly exclude it from AIHOT equivalence.

## Tradeoff and rigor contract

- Priority order already confirmed by the user: completeness and stable original-source retrieval first; low X API cost second; implementation simplicity third. A source is not considered aligned merely because its name is present in config.
- R/G stakes: `R=standard` because the work is locally reversible but changes the production ingestion set and persisted source identity; `G=standard` because missed or misparsed sources affect real users but do not create an irreversible security or data-integrity event.
- Assurance vector: `(A1,V1)`, user-confirmed as standard production validation. Bind edits to this source-alignment worktree, test every behavior-changing adapter family, run full regression/static checks at the milestone, and obtain one independent review. Live X validation is limited to one account and a 20-minute window.
- No per-source full-history backfill. New X sources use the existing 20-minute cold-start contract and then persistent incremental cursors.
- No production DB mutation during development or validation. Every fetch sweep creates a unique temporary SQLite path, prints the resolved path, sets `AI_RADAR_DB`, and asserts the live connection resolves to that exact path before any write.
- Production-upgrade convergence preserves historical rows for audit/history but changes public membership semantics: after `sync_to_db` disables a removed source, `/api/v2/sources`, `/about`, `/`, `/all`, search, counts, and selected/main timeline queries exclude `sources.enabled=0`. Historical rows remain in SQLite and may be inspected administratively, but are not part of the current public source universe. The separate `/wechat` route likewise requires a currently enabled `kind=wechat` source. `/api/v1/sources` retains its published compatibility behavior and excludes `kind="web"` rows; `/api/v2/sources` is the complete enabled public inventory.

## Runtime cost audit

| Runtime cost | Decision and reason |
|---|---|
| X API identity and timeline reads | Keep because no reliable free original-source path supplies the target X accounts. Normal operation is one bounded page per enabled account per fetch round; validation is one account only. X resource reads are paid and must never be exercised as a full-pool test. |
| 52 non-X HTTP reads | Keep because they are the original publisher inputs. The existing fetch round already performs independent I/O with bounded concurrency; the new web adapters share that execution model rather than adding a second scheduler. |
| HTML/API parsing | Keep deterministic and local. No browser and no LLM is introduced into ingestion. |
| AIHOT comparison traversal | Development/audit only, never a production dependency or recurring runtime cost. |

## Source map

### Direct original RSS/Atom sources (34)

All use `kind="feed"` and the existing HTTP cache/dedup path. `feed_rules.py` may apply only deterministic source-specific normalization or inclusion rules; rules never enter public `meta`.

| Slug | AIHOT source | Original feed | Special rule |
|---|---|---|---|
| `ai_normal_technology` | AI as Normal Technology | `https://www.normaltech.ai/feed` | none |
| `apple_ml` | Apple Machine Learning Research | `https://machinelearning.apple.com/rss.xml` | none |
| `ars_ai` | Ars Technica: AI | `https://feeds.arstechnica.com/arstechnica/technology-lab` | accept publisher's closest official feed; downstream topic filtering is out of scope |
| `ai_news` | Artificial Intelligence News | `https://www.artificialintelligence-news.com/feed/` | none |
| `bytebytego` | ByteByteGo | `https://blog.bytebytego.com/feed` | none |
| `cmu_ml` | CMU Machine Learning Blog | `https://blog.ml.cmu.edu/feed/` | none |
| `claude_code_releases` | Claude Code GitHub Releases | `https://github.com/anthropics/claude-code/releases.atom` | existing source |
| `claude_youtube` | Claude YouTube | `https://www.youtube.com/feeds/videos.xml?channel_id=UCV03SRZXJEz-hchIAogeJOg` | correct official channel; reject the previously tested wrong channel ID |
| `databricks` | Databricks Blog | `https://www.databricks.com/feed` | none |
| `dwarkesh` | Dwarkesh Patel | `https://www.dwarkesh.com/feed` | none |
| `gary_marcus` | Gary Marcus | `https://garymarcus.substack.com/feed` | none |
| `github_blog` | GitHub Blog | `https://github.blog/feed/` | none |
| `google_ai` | Google Blog: AI | `https://blog.google/innovation-and-ai/technology/ai/rss/` | none |
| `google_cloud_databases` | Google Cloud: Databases | `https://cloudblog.withgoogle.com/rss/` | retain only entries whose category/tag is `Databases` or whose canonical path is `/products/databases/` |
| `google_deepmind` | Google DeepMind Blog | `https://deepmind.google/blog/rss.xml` | none |
| `google_developers` | Google Developers Blog | `https://developers.googleblog.com/feeds/posts/default` | none |
| `buzzing_hn` | Hacker News popular via buzzing.cc | `https://www.buzzing.cc/feed.xml` | none; source is the aggregator itself |
| `huggingface_blog` | Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | existing source |
| `ithome` | IT Home | `https://www.ithome.com/rss/` | existing source |
| `linear_now` | Linear Now | `https://linear.app/rss/now.xml` | none |
| `marktechpost` | MarkTechPost | `https://www.marktechpost.com/feed/` | none |
| `meta_engineering` | Meta Engineering | `https://engineering.fb.com/feed/` | none |
| `nvidia` | NVIDIA Blog | `https://blogs.nvidia.com/feed/` | none |
| `interconnects` | Nathan Lambert: Interconnects | `https://www.interconnects.ai/feed` | existing source |
| `openai_blog` | OpenAI official updates | `https://openai.com/news/rss.xml` | do not invent a customer-case filter: current AIHOT observations include enterprise/customer posts despite the historical display label |
| `openrouter_announcements` | OpenRouter Announcements | `https://openrouter.ai/blog/feed.xml` | accept all entries in the official feed: tracked AIHOT observations include announcements, insights, and tutorials despite the historical display label |
| `sakana_blog` | Sakana AI Blog | `https://sakana.ai/feed.xml` | resolve relative entry URLs against `https://sakana.ai/` |
| `simonw` | Simon Willison Blog | `https://simonwillison.net/atom/everything/` | existing source |
| `techcrunch_ai` | TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` | none |
| `the_decoder` | The Decoder | `https://the-decoder.com/feed/` | none |
| `the_verge_ai` | The Verge AI | `https://www.theverge.com/rss/ai-artificial-intelligence/index.xml` | none |
| `tomer_tunguz` | Tomer Tunguz | `https://tomtunguz.com/index.xml` | existing source |
| `a16z_news` | a16z News | `https://www.a16z.news/feed` | none |
| `elsewhere` | elsewhere articles | `https://elsewhere.news/feed.xml` | none |

### Original HTML/API list sources (18)

All use `kind="web"`. Each registry entry declares the fetch URL, allowed item host/path, parser, and `minimum_items=1`. These are cumulative archive/list pages, so zero accepted items means the current response is not semantically usable; the source fails for that round while historical DB items remain untouched.

| Slug | AIHOT source | Original entry | Parser contract |
|---|---|---|---|
| `anthropic_news` | Anthropic Newsroom | `https://www.anthropic.com/news` | structured cards/anchors under `/news/<slug>` |
| `anthropic_research` | Anthropic Research | `https://www.anthropic.com/research` | `/research/<slug>`, excluding team/profile paths |
| `claude_platform_releases` | Claude Platform release notes | `https://platform.claude.com/docs/en/release-notes/overview` | dated release-note headings; item URL is the page plus heading fragment |
| `claude_blog` | Claude Blog | `https://claude.com/blog` | structured blog cards under `/blog/<slug>` |
| `cursor_blog` | Cursor Blog | `https://cursor.com/blog` | article cards under `/blog/<slug>`, excluding index/topic roots |
| `every_latest` | Every latest articles | `https://every.to/` | article cards only; exclude author, publication root, newsletter, and navigation URLs |
| `google_research` | Google Research Blog | `https://research.google/blog/` | article cards under `/blog/<slug>/`, excluding archive pages |
| `hf_daily_papers` | Hugging Face Daily Papers | `https://huggingface.co/papers` | official paper list; canonical output URLs are `https://arxiv.org/abs/<id>` |
| `lmsys_blog` | LMSYS Blog | `https://www.lmsys.org/blog/` | structured Next payload or cards with `/blog/<dated-slug>` |
| `langchain_blog` | LangChain Blog | `https://www.langchain.com/blog` | `.blog-item` card, nested article anchor, title, and date |
| `microsoft_ai` | Microsoft AI News | `https://microsoft.ai/news/` | request redirects to the official `https://microsoft.ai/blog/` archive; accept official article cards under current Microsoft AI article paths |
| `mistral_news` | Mistral AI News | `https://mistral.ai/news` | article cards under `/news/<slug>` |
| `runway_news` | Runway News | `https://runwayml.com/news/` | request redirects to `https://runway.com/news`; structured payload with slug/title/date; accept nested news paths including customer stories because AIHOT currently emits them |
| `sierra_blog` | Sierra Blog | `https://sierra.ai/blog` | parent blog cards under `/blog/<slug>` with title/date |
| `suno_blog` | Suno Blog | `https://suno.com/blog` | parent blog cards under `/blog/<slug>` with title/date |
| `xai_news` | xAI News | `https://x.ai/news` | rich anchors/cards under `/news/<slug>` |
| `inclusionai_models` | inclusionAI Hugging Face models | `https://huggingface.co/api/models?author=inclusionAI&sort=lastModified&direction=-1&limit=50` | official Hub API list; canonical item URL `https://huggingface.co/<model-id>` and `lastModified` as publish time |
| `deepseek_api_updates` | DeepSeek API updates | `https://api-docs.deepseek.com/zh-cn/updates` | dated headings inside the official updates article; canonical item URL is the page plus its date-heading fragment |

### Public projection rules

- The exact public name, kind, tier, enabled state, fetch URL, and human landing page live in the machine source contract; rendered schema-v2 config and `/api/v2/sources` must match its appropriate private/public projections row-for-row after excluding approved runtime-only fields.
- `homepage_url` always names the most specific stable official human page for that source rather than a raw RSS/API endpoint. For feeds it is the response's official publication home link after provenance validation; for web entries it is the canonical archive/list page.
- The three redirect/API edge cases are fixed: Microsoft AI uses `https://microsoft.ai/blog/`, Runway News uses `https://runway.com/news`, and inclusionAI Models uses `https://huggingface.co/inclusionAI` as `homepage_url`; their separate `fetch_url` values remain the entries listed above.
- X `fetch_url` remains the official identity endpoint and `homepage_url` remains `https://x.com/<username>`. The username is internal fetch identity and is not duplicated in public `meta`.

### Membership transition rules

- `scripts/audit_aihot_sources.py` reconciles each observed source against stable contract identity. For X it derives the normalized username from original post URLs and verifies the display-name handle when present. For non-X it requires exactly one explicit alias match; a new display name for an otherwise mapped identity becomes a proposed alias/rename, not a new source.
- Ambiguous alias matches, conflicting X username/display identity, and unmapped observations fail the audit and produce a review packet; they never auto-add or auto-delete a contract row.
- `scripts/check_aihot_membership_transition.py --previous <contract-or-frozen-baseline> --next <contract> --retirements <ledger>` asserts every previously accepted active `derived_aihot_identity` remains active. A deletion passes only when the ledger cites official shutdown/migration evidence, thirty daily observation records plus explicit review, or a user decision. The initial previous set is reconstructed from the immutable frozen baseline plus the two tracked observation projections; later changes compare against the prior committed contract.
- `wx_mp2rss` is outside AIHOT membership but inside the preservation contract. Tests compare every literal schema-v2 config field (`slug/name/fetch_url/tier/enabled/kind/homepage_url/icon_url/meta`) against its frozen contract row before and after convergence; only the existing environment substitution/skip behavior may vary at load time.

## Consumer acceptance (L2)

The black-box acceptance command is fixed and uses the repository's native Chromium fixture: `AI_RADAR_ALIGNMENT_EVIDENCE_DIR=<persistent-dir> uv run pytest tests/playwright/test_aihot_source_alignment.py`. The new test module owns its complete environment: it creates and prints a unique temp DB, verifies `PRAGMA database_list`, starts a loopback Mp2RSS fixture server and checks its health, and preloads an old production state. That state includes the nine removed sources plus items, a disabled non-WeChat item joined through `curation_runs + curated_items` in both default-latest and explicit/precomputed selected paths, and a previously configured WeChat item joined through `wechat_interpretations(save_decision=1)`. It first proves these relations exercise the affected consumers, then runs the real final `sources.toml` sync, starts the real app via the existing isolated-port harness, and saves browser traces/screenshots/API snapshots to `AI_RADAR_ALIGNMENT_EVIDENCE_DIR`. A second fixture branch omits `MP2RSS_FEED_URL`. `uv run pytest --collect-only tests/playwright/test_aihot_source_alignment.py` must pass before the live browser run; no unregistered pytest CLI options are used.

The consumer outcome is incomplete until the browser observes this matrix:

| Page | Required black-box observations |
|---|---|
| `/` | Feed, web-HTML, web-JSON, and X representative cards show the exact configured public source name and link to the original article/post. No card sourced from `wx_mp2rss` appears. |
| `/all` | The same four carrier representatives appear with the same source names/original links; `web` is not mislabeled as RSS; search/filtering can locate each representative. No `wx_mp2rss` card appears. |
| `/about` with Mp2RSS configured | All 162 contract rows appear exactly once with name/tier/status/kind and safe homepage link. The WeChat row visibly says `仅用于微信文章解读，不属于主时间线`; its link is `https://mp.weixin.qq.com/`, and neither DOM, page text, network log, nor `/api/v2/sources` contains the resolved paid-feed URL. |
| `/about` without Mp2RSS | Exactly the 161 main rows appear; the optional WeChat row is absent because it was not loaded. All 161 row projections match the source contract's public values. |
| `/wechat` with Mp2RSS configured | Only interpreted `kind=wechat` content appears and the page identifies the separate WeChat interpretation surface. Main feed/web/X representatives do not appear. |
| `/wechat` without Mp2RSS | The page displays its existing unavailable/empty state without affecting `/` or `/all`. |

In both branches the preloaded disabled sources, items, curation relations, and WeChat interpretation relations remain queryable in SQLite but are absent from `/api/v2/sources`, `/api/v1/selected` (default and explicit/precomputed branches), `/about`, `/`, `/all`, `/wechat` list/detail, search results, and visible counts. This upgrade-state observation—not a fresh-only database—is the production convergence proof.

The same acceptance run saves the complete `/api/v2/sources` JSON and proves every loaded contract identity appears exactly once with its public fields; the browser observations, not DB/checkpoint internals, determine the visible PASS. Documentation acceptance checks rendered README/About copy says only “source membership configured”; it must not claim live retrieval equivalence before the non-X and X success receipts exist, nor claim equivalence for AIHOT cleaning, filtering, labels, ranking, scoring, summaries, or curation.

Live X consumer validation is a separate bounded prerequisite: only `x_openai`, at most one identity lookup and one timeline request for at most five original posts from the preceding 20 minutes; no full-pool test or history backfill. A live 401 remains unresolved rather than passing.

## Implementation and evidence gates

These gates support the consumer promises above without substituting internal state for user-facing acceptance.

1. The source-contract test compares all contract fields against rendered schema-v2 config, the synchronized database inventory, and the full enabled-only `/api/v2/sources` projection; it derives class/total counts from the contract. Transition tests cover display rename, ambiguous alias, conflicting X identity, unmapped member, unauthorized deletion, authorized retirement, and field drift in `wx_mp2rss`. Upgrade-state tests preload removed sources/items plus curation and WeChat interpretation relations, prove the fixtures hit default/explicit selected and WeChat list/detail paths, sync the new contract, and assert every public query filters disabled sources while all SQLite rows/relations remain stored. Both WeChat environment branches are explicit: absent means no enabled DB/API/About row after sync; configured means internal fetch uses the resolved value while public API/About use only the safe public URL. Baseline artifact, contract, config, count assertions, README/UX numbers, and completion audit are updated atomically whenever a later observation adds a member.
2. The AIHOT audit command validates page shape, requires a nonempty new cursor whenever `hasMore=true`, rejects repeated cursors or premature termination, and writes its raw observation plus source projection to a persistent artifact only after reaching `hasMore=false`.
3. Every DB-writing verification prints its unique temporary DB path and asserts `PRAGMA database_list` resolves the active connection to that same path before synchronization or fetch.
4. Fixture/HTTP tests cover structural 200 responses with missing structure or zero accepted items, invalid host/path/date/payload, per-source failure isolation, preservation of prior rows, allowed runtime request targets, and absence of hidden fallback requests.
5. For each non-X source the live audit records `independent expected response set == production fetch result set == persisted set`; the independent oracle is forbidden from calling the production parser, parser helpers, registry parser, inclusion predicate, or feed-rule module. Differential tests deliberately remove one legal production result and prove the expected set does not change and the gate fails. Dedup is tested by replaying the first round's immutable response bytes through the production path into the same DB and requiring `inserted=0` with an unchanged persisted set. A separate second live freshness sweep permits new publisher items: first persisted set must be a subset of the second, `inserted` must equal the new canonical delta, and no previously seen item may be reinserted; 304 preserves the first set.
6. X fixture tests cover identity resolution, 20-minute cold start, `max_results=5`, original-post exclusions, pagination/checkpoints, failure/CAS behavior, and public-state projection. Live evidence is limited to the two requests stated in consumer acceptance.
7. Focused tests, full `uv run pytest`, `uv run ruff check src tests`, and `uv run mypy src` pass on the final worktree. The stable diff then passes `$custom-review-schema` and the generated-code review gate; findings are fixed and the same gates rerun.

The X cost gate additionally counts transport calls for each production invocation: `identity_pending` performs exactly one identity request and returns without a timeline request; every resolved invocation performs at most one timeline request; a returned `next_token` is persisted and is never followed inside the same invocation. Runner tests assert the same bound through the public per-source fetch path.

## Design and implementation (L3)

### Phase 1 — Freeze the target and executable inventory contract

Files:

- Add `scripts/audit_aihot_sources.py`, a comparison-only CLI that calls the documented rolling endpoint, validates each page/cursor transition, terminates only at `hasMore=false`, derives stable observed identities, reconciles aliases, emits a deterministic source projection and hashes, and atomically writes a caller-selected artifact path. Every successful observation is append-only under `artifacts/observations/<uuid>.json`; `artifacts/observations/index.json` is the final commit marker and each exact entry contains only the artifact path relative to the artifacts directory and its SHA-256. Capture time, terminal state, and content summaries remain authoritative only in the referenced receipt; the caller-selected output is a same-shape copy, not a second latest pointer schema. Add focused tests for terminal, repeated-cursor, missing-cursor, malformed-page, interrupted-write at each write boundary, UUID append, exact index schema/integrity, rename, ambiguous/unmapped, and conflicting-identity cases. This script is not called by production ingestion.
- Add `scripts/check_aihot_membership_transition.py` and a versioned retirement ledger. It rejects removal of a previously accepted identity unless the ledger includes an allowed evidence class and review reference; a 30-day absence entry must cite 30 distinct successful daily index records and their hashes. Seed the previous identities from the frozen/tracked observations and test authorized versus unauthorized transitions.
- Update `plans/20260812-aihot-original-source-alignment/artifacts/target-baseline.md` with all post-baseline rolling traversals, hashes, the user-selected monotonic union rule, current 161-source snapshot, seven added X accounts, and the fact that current activity alone temporarily omits quiet sources. Preserve failed reconciliation diagnostics separately and write a fresh successful audit artifact only after the updated contract passes.
- Add the machine source contract under `tests/fixtures/aihot_sources.json` with every field defined above, including stable identity/aliases and the full excluded WeChat row. This is the exact test/audit projection oracle, not a second runtime source registry.
- Add `tests/test_sources_pool_completeness.py` to compare `data/sources.toml` with the exact map, derive class/total counts, and assert exactly one excluded WeChat source.

Internal verify:

- Prove the new contract test fails on the current 122-source config before changing it.
- Assert no duplicate slug, normalized human homepage, or X username; public names may repeat only when the source contract explicitly carries distinct identities and the UI remains unambiguous.
- Assert every target class count and every exact identity; do not rely on count-only equality. Treat baseline, source map, config, count-bearing docs, and the completion audit as one evolution unit whenever a new observed member is accepted.

### Phase 2 — Add the deterministic web-list carrier and feed rules

Files:

- Extend `src/airadar/sources/loader.py` schema v2 with `kind="web"` and authoritative `fetch_url`. Versionless/v1 compatibility remains unchanged and continues to accept legacy `url`; v2 validates that every web slug exists in the internal registry and rejects runtime parser/selector keys in config meta.
- Add `src/airadar/fetcher/web.py` with a typed registry and shared fetch/parse entrypoint. Keep source-specific structure in small parser functions but share HTTP, date normalization, URL validation, item construction, and semantic failure handling.
- Add `src/airadar/fetcher/feed_rules.py` for the two justified feed-only rules: Google Cloud databases inclusion and relative URL resolution (used by Sakana and safe generically). Do not filter the official OpenRouter feed: observation positives include `/blog/announcements/`, `/blog/insights/governing-team-ai-spend`, `/blog/tutorials/team-spend-controls-setup`, and `/blog/tutorials/tool-calling`.
- Refactor `src/airadar/fetcher/http_client.py` only enough to expose one cached HTTP document fetch primitive with caller-selected `Accept`; preserve feed ETag/Last-Modified behavior and existing public interfaces.
- Route only `kind="web"` through `fetch_web_source` in `src/airadar/fetcher/runner.py`; keep X, RSS, and WeChat paths behaviorally separate.
- Update source display logic in both `src/airadar/web/app.py` and `web/static/app.js` so `web` uses its configured source name without an RSS suffix. Keep X and WeChat rendering unchanged.

Registry invariants:

- Entry keys exactly equal the 18 web slugs in the source map.
- Each entry declares allowed fetch host, allowed item host/path predicate, parser, and `minimum_items=1`.
- Redirects may be followed only by the shared HTTP client; validate the final response host against the registered host before parsing.
- Reject missing title, non-absolute canonical URL, out-of-scope host/path, missing/invalid timezone-aware date when the source exposes dates, duplicate canonical URLs in one response, and zero accepted items.
- A failed parse produces no `meta_update` and no item writes; existing items remain.
- The adapter is deterministic: no browser, LLM, AIHOT call, or heuristic extraction of arbitrary anchors.
- A transport spy records the actual final request URL for each production fetch. Tests fail if any runtime target falls outside the source's config/registry authority or if code attempts an AIHOT, Mp2RSS, mirror, or undeclared fallback request.
- Transport/audit tests record simultaneous request entry/exit and assert `max_in_flight <= 8` for the non-X sweep.

Tests:

- Add one minimal structure fixture per web source in `tests/fixtures/web_sources/` and `tests/test_web_sources.py`; every fixture carries at least two accepted items plus one excluded/navigation item when the page has exclusions.
- Add negative fixtures for structural 200/zero items, wrong host/path, invalid date, malformed JSON/Next payload, and one source failing while a peer succeeds.
- Extend `tests/test_fetcher.py`, `tests/test_sources_schema.py`, `tests/test_web.py`/route tests, and frontend static/render tests for routing, compatibility, public inventory, and display names.
- Extend RSS tests for Google Cloud filtering, OpenRouter's announcements/insights/tutorials positive examples, relative URLs, and unchanged default feed behavior.
- Extend X tests with request-count assertions at both adapter and runner boundaries: identity resolution is one request/round, timeline is one page/request/round, and pagination resumes only in a later invocation.

### Phase 3 — Converge the source configuration

Files:

- Rewrite only the source entries in `data/sources.toml` needed to match the exact fixture map: correct target names/homepages, replace third-party Anthropic/Runway feeds with original web entries, add the initially missing 41 non-X targets plus the later-observed DeepSeek update log and seven post-baseline X accounts, and remove the nine extra non-AIHOT main sources.
- Update the X allowlist comment and tests from 102 to 109. Keep the identity lookup URL form `https://api.x.com/2/users/by/username/<username>` and `meta.adapter="x_api"`.
- Preserve `wx_mp2rss`, its environment placeholder, and loader skip behavior.

Internal verify:

- Load with and without `MP2RSS_FEED_URL`; assert 162 and 161 respectively.
- Compare full normalized source identities with the fixture map, not just totals.
- Assert no AIHOT/Mp2RSS/third-party-mirror host occurs in any main source fetch URL, and use the runtime transport-target test from Phase 2 to cover hidden requests.
- Assert the nine removed slugs are absent and all seven post-baseline X usernames are present.

### Phase 4 — Real retrieval, deduplication, and user-facing flow

- Add `scripts/audit_non_x_retrieval.py --config <sources.toml> --db <new-path> --output <persistent-json>`. It refuses an existing DB path, creates/migrates that exact path, proves the active connection with `PRAGMA database_list`, loads only the contract's 52 non-X main sources, and invokes the production `runner` fetch/parse/dedup/apply functions rather than a parallel audit parser. It uses bounded concurrency 8 and retries once only for classified transport timeout/connect/5xx failures; HTTP 4xx and semantic parser failures are final for that run.
- The audit driver captures each authorized response once through the production transport and passes the immutable response bytes to two isolated consumers, so it does not issue an extra comparison request or compare observations taken at different times. Production uses its normal parser/rules. The independent completeness-oracle package has no import path to production parser/registry/rule modules and uses source-specific candidate enumeration written from the upstream response contract (for feeds: raw XML item/entry enumeration plus an independently encoded approved scope; for web/API: independently chosen structured records/anchors and allowed identity facts). For each source it records actual request/final URL, response status, independent expected set, production fetch-result set, persisted set, counts, and error classification. It fails unless all three canonical URL/title sets are equal; if no independent oracle can be established, that source is `unverifiable` and the run fails rather than reporting success.
- After the first live round, the driver replays those exact immutable response bytes through the production path and requires an unchanged persisted set with `inserted=0`. It then performs a second live freshness round: first persisted set must be a subset, `inserted` equals the new canonical delta, previously seen items are not reinserted, and 304 preserves the first set. It atomically writes `artifacts/non-x-retrieval-final.json` only after all 52 sources pass and includes `passed=true`, exact config/contract hashes, DB path, source count, failed-source list, per-source evidence, and content hashes of every relevant production parser/registry/feed-rule/runner/dedup module, independent oracle module, and audit-driver file. Any hash change invalidates the receipt and requires a rerun; generated-review fixes touching those paths automatically reopen this gate. Tests cover refusal of an existing/default DB, retry classification, semantic no-retry, incomplete source membership, set mismatch, a deliberately weakened production parser against an unchanged independent oracle, unavailable oracle, immutable replay, live new-item delta, 304 preservation, partial failure, stale code hash, and no-success-artifact on failure.
- Run the narrow X probe only after the credential passes identity lookup. It ends after at most one identity request and one 20-minute timeline request; later checkpoint behavior is verified offline rather than spending a third live request. Record request count, state transitions, and non-sensitive result counts; never record the token or raw Authorization header.
- Run the exact Playwright command and four-page matrix defined in L2 in both Mp2RSS branches; preserve its output directory as the consumer evidence artifact.
- Add upgrade-state route tests and browser setup that preload the current pre-alignment config and historical items, run `sync_to_db`, and prove all public inventory/timeline/search/count queries exclude disabled sources while the database rows remain intact.

### Bounded live X interface

- Add `scripts/probe_x_source.py --source x_openai --db <new-path> --output <persistent-json>`. It refuses any other source unless explicitly changed in a later user-approved run, refuses an existing/default DB, and makes a boolean-only token preflight (`present`, never value/prefix/header). Missing token writes `artifacts/diagnostics/x-probe-<timestamp>-missing-token.json` before network and fails. Server 401 writes a distinct non-sensitive diagnostic with source, endpoint class, status, request count, and DB state, then stops.
- A bounded run performs at most one identity lookup and one timeline request and persists the resulting legal state in the isolated DB. A terminal timeline 200 writes a `state_scope=terminal_checkpoint` success receipt. A timeline 200 with `next_token` writes a `state_scope=draining_connectivity` success receipt: it proves live identity/timeline compatibility and a legal persisted draining cursor, but explicitly sets `terminal_checkpoint_verified=false` and never calls the cursor a committed checkpoint. A zero-item terminal 200 passes connectivity plus terminal-state validation when it commits the empty-window time checkpoint; it is not live post-retrieval evidence. Each receipt records request count, HTTP/status classes, state transitions, `fetched/inserted`, time window, `max_results`, config/code hashes, and no raw post bodies or authorization data.
- Offline fixture tests separately prove nonempty response parsing/item persistence and that later invocations resume and fully drain pagination before committing the checkpoint. The final report states live connectivity, live terminal checkpoint (true/false), live post retrieval (true/false), and offline pagination/checkpoint proof as four separate fields. A draining live receipt satisfies the bounded live connectivity prerequisite while offline tests carry the across-round terminal-checkpoint guarantee; it does not authorize a second paid timeline request in this validation run.
- Tests cover collection/interface, missing token before HTTP, 401 stop after one request, identity-only first invocation, bounded timeline invocation, empty-terminal checkpoint PASS, `next_token` draining-connectivity PASS with terminal=false, nonempty fixture parsing and later drain, secret redaction, refusal of default/existing DB, and atomic failure/success artifacts. The actual low-cost run remains one account with no backfill.

### Phase 5 — Documentation, reviews, and completion audit

Files:

- `README.md`: document `kind="web"`, exact current source counts, original-source behavior, bounded X startup/cost, and the WeChat exclusion. Its claim must be explicitly limited to source-set membership and original-source retrieval, not AIHOT cleaning/filtering/ranking/scoring/summaries/curation.
- `CHANGELOG.md`: add one user-visible entry for source-set alignment and original-source adapters using the same limited claim; do not say the sites are wholly content-equivalent.
- `docs/architecture.md`: document feed/web/X/WeChat routing and the internal registry boundary.
- Extend the existing source-maintenance workflow in `README.md` or `docs/architecture.md`: adding/checking/removing a web source requires its TOML identity, registry/parser entry, positive and negative fixture, focused test, real probe, and synchronized removal/update of source-map/baseline/count anchors. Link this workflow from the primary source-pool documentation entry.
- The primary source-maintenance workflow must list all four authority/verification commands and their inputs/outputs: `audit_aihot_sources.py`, `check_aihot_membership_transition.py`, `audit_non_x_retrieval.py`, and `probe_x_source.py`; explain the append-only observation index, stable identity/alias reconciliation, retirement evidence ledger, upgrade-state disabled-source behavior, and which code-hash changes invalidate/reopen the retrieval receipt. Adding, renaming, retiring, or repairing a source cannot be documented as a TOML-only edit.
- `docs/contracts/ux-contract.md`: update the source-inventory/About and timeline source-label sections so feed/web/X/WeChat types, exact alignment exception, the four-page black-box matrix, and the limited source/retrieval claim are covered.
- Add `docs/adr/024-use-controlled-original-web-lists-for-aihot-source-alignment.md`, update `docs/adr/README.md`, and update `docs/CLAUDE.md` because a docs asset is added. The ADR records the reviewed choice of `kind=web` plus code-owned parsers over per-site fetch stacks, arbitrary generic scraping, third-party RSS, or AIHOT mirroring.

Gates:

- Run focused tests after each behavior-changing phase with a temporary `AI_RADAR_DB`.
- Run the full regression/static commands and the user-facing browser flow after all source/config changes.
- Run `$custom-review-schema` because `kind="web"`, schema-v2 `fetch_url`, and `/api/v2/sources` are human-visible data-contract changes; resolve all blocking findings and rerun the same gate. The user-approved v1 compatibility boundary is regression-tested: `/api/v1/sources` excludes `kind="web"` rows while `/api/v2/sources` retains the complete enabled inventory.
- Run the required generated-code review gate on the final stable diff. Do not declare completion or create a commit until it passes.
- Perform a requirement-by-requirement audit against all six consumer-acceptance items, all implementation/evidence gates, and every row derived from the final source map. Any missing live X proof, nonzero source failure, unexplained per-source shortfall, stale evolution anchor, test failure, or unclosed review finding keeps the task active.

## Risks and trigger responses

| Risk | Acceptance | Trigger response |
|---|---|---|
| A publisher changes markup while returning HTTP 200 | Website layouts are outside repository control. | Registry invariants make the source fail visibly and preserve history. Repair that source's parser and fixture, then rerun both real sweeps and the same review gate. Do not fall back to arbitrary anchors, AIHOT, or a third-party mirror. |
| A documented feed is transiently unavailable | One network failure is not evidence the source is invalid. | Retry once with the production timeout. Persistent failure blocks completion for that source; investigate the official publisher entry rather than silently disabling it. |
| AIHOT rolling membership changes during implementation | Rolling observations are lower bounds and the user selected a monotonic union. | Run the repository audit immediately before delivery. Add a genuinely new non-WeChat source by atomically updating the observation artifact, baseline, source map, config, derived counts, docs, tests, adapter, and completion audit. Never remove a quiet source from one rolling miss; use the retirement authority defined above. |
| AIHOT audit is rate-limited, repeatedly times out, or cannot reach `hasMore=false` | Freshness is necessary to claim current alignment; an old successful observation cannot masquerade as a fresh terminal run. | Retry at most twice after the initial attempt with bounded exponential delays of 2s and 5s, only for 429/5xx/connect/timeout failures. Preserve the latest successful union and make no deletions. On failure atomically write `artifacts/diagnostics/aihot-audit-<timestamp>-failed.json` with `complete=false`, all attempts/status/error, last page/cursor, endpoint/query, and prior-success reference; never update the success index/final artifact. Preflight the diagnostic path before asking whether to wait or explicitly change the freshness promise. |
| A newly observed member has no verified original entry, requires a new paid/authenticated service, or does not fit feed/web/X carriers | The monotonic union requires intake, but original-source and cost constraints cannot be silently relaxed. | Perform bounded original-entry discovery and existing-carrier fit checks. If no path preserves the existing promises, do not use AIHOT, Mp2RSS, a mirror, or an undeclared scraper; stop before implementation and present the observed identity, failed candidates/evidence, runtime cost, and carrier options for user decision. |
| A source lacks an independent completeness oracle | Equality against production cannot prove completeness when both sides share parser logic. | Spend a bounded investigation of one source-specific response capture plus official response/schema documentation and at most two independent enumeration approaches. Persist `artifacts/diagnostics/non-x-oracle-<slug>-failed.json` with attempted oracles and why independence/completeness could not be established. Keep completion blocked; after preflight, offer only `preserve the full assurance and wait/investigate further` (recommended) or an explicit user-approved reduction of assurance. Never mark the source passed from production output alone. |
| X Bearer Token is missing or returns 401 | Authentication is an external prerequisite and paid calls must stay bounded. | The probe distinguishes missing-before-network from one failed server identity lookup, writes the corresponding diagnostic, and stops. Ask the user to configure or regenerate the App Bearer Token. Continue independent non-X work, but do not mark the full goal complete until the one-account connectivity/state gate passes. |
| A full non-X sweep is slow | 52 independent HTTP reads are material but free. | Use the existing bounded concurrency with cap 8 for the audit; do not create an unbounded parallel crawler or serialize by omission. |

## Human decisions and delivery

- No product decision remains pending. The user chose full source-set alignment, original sources, the monotonic observation union, Mp2RSS isolation, no X backfill, standard production validation, repairing all plan violations rather than shrinking the promise, and later freezing `/api/v1/sources` so the new `web` kind is exposed only through `/api/v2/sources`.
- Ordinary discovery, implementation, tests, real probes, browser verification, and review are agent-owned. Four external conditions can require user action: configure/regenerate a missing or rejected X credential; decide whether to wait after the bounded fresh-AIHOT audit is exhausted; choose a new authorized carrier/cost boundary when a newly observed source cannot satisfy the existing original-source contract; or explicitly decide whether to preserve versus lower completeness assurance after bounded independent-oracle investigation fails. No condition permits silent scope reduction.
- Before asking on any conditional product decision, the agent prepares one decision packet containing: the exact decision target; observed source/endpoint/status/cursor evidence paths; all feasible options; each option's effect on completeness, assurance, runtime cost, and delivery time; a recommended option and why; the exact user reply that selects it; and a preflight confirming every referenced file/link is readable and all independent work is complete. For freshness failure, options are `wait and retry later` (recommended while the endpoint is plausibly transient) or explicitly amend the fresh-audit promise. For carrier failure, options enumerate only technically verified carriers plus `wait/defer this delivery`; any new paid carrier includes its request/cost model. For oracle failure, options are `preserve completeness assurance and continue investigation/wait` (recommended) or explicitly name the source and the exact completeness claim/gate being waived; until that explicit waiver, the source and task remain blocked. Until the user selects an option, the completion gate remains blocked and the last accepted contract/union/assurance is unchanged.
- Work remains in the isolated `worktree-aihot-sources-20260812` branch. A local commit may be proposed only after all gates pass. Integrating it into local `main` and any `git push` each require separate explicit user permission.

## Bounded implementation TODOs

- In Phase 2, remove the proposed OpenRouter announcements-only rule. Add positive fixture/test coverage for the observed `announcements`, `insights`, and `tutorials` paths listed above, and update any source description that implied all current examples were announcements. This is a fixed factual correction, not a new product choice.

## Defaulted decisions for reviewer scrutiny

| Decision | Default and reason |
|---|---|
| Membership policy | User-selected monotonic union of successful rolling observations, not latest-only and not the 209-source historical selected archive. Rolling data may add candidates but does not itself authorize deletion; retirement follows the authority rule above. |
| Web carrier | One human-readable `kind="web"` for HTML and official list APIs. It keeps RSS honest, avoids exposing selectors in meta, and does not need separate public `html` and `api` kinds that users cannot act on differently. |
| Empty list behavior | `minimum_items=1` for all 18 cumulative archive/list entries. Zero items is semantically unusable and fails only that source while preserving history. This decision passed an independent decision review. |
| Tier assignment | Use the existing contract only: first-party publisher sources T1, expert/professional analysis T1.5, media/community aggregators T2. Do not infer or claim AIHOT ranking equivalence from tiers. |
| Real non-X validation | Two complete temp-DB sweeps rather than one. The second sweep cheaply proves deduplication and repeatability without paid API cost. |
