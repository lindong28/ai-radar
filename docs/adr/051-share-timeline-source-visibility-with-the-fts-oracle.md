# ADR-051: 由 timeline 单一持有 source visibility 谓词，FTS oracle 复用它

- Status: Accepted
- Date: 2026-08-17
- Context: 2026-08-17 生产 DB 同步在 `phase=candidate-http` 被服务器 quarantine

## Context

`deploy/sync/build_fts_manifest.py` 为每个搜索字段产出一条 `timeline_http_matches` 期望集合，服务器 `apply_db_update.py` 用它对 candidate 槽的 `/api/v1/timeline?q=<term>` 做精确比较。2026-08-17 那轮的失败读数：

```text
failure_category=deterministic-gate
phase=candidate-http
message=candidate-slot consumer probe title differs:
        expected={'count': 1, 'item_ids': ['000553f3075cface']} actual={'count': 0, 'item_ids': []}
automatic_retry_disposition=not-eligible
```

item `000553f3075cface` 属于 source `hn_ai`，`sources.enabled=0`。

根因是 producer 与应用之间的谓词漂移，git 可直接证明：

- `git log -L 185,186:src/airadar/web/routes/timeline.py` 显示 `ab9442d`（AIHOT 来源对齐）把 timeline 的 where 起始值从 `[]` 改为 `["s.enabled=1", "COALESCE(s.kind, 'feed') != 'wechat'"]`。
- `git log -L 423,429:deploy/sync/build_fts_manifest.py` 显示 producer 的平行可见性列表自建档 commit `cd32f78` 起从未变更。

该模块 docstring 当时已声明 probe 用的是 app-owned 谓词而非复制的 SQL，且它确实 import 了 `deduped_item_clause` 与 `_PREFILTER_SCORING_CLAUSE`——**唯独 source visibility 是以省略的形式硬编码的**。省略没有陈旧副本可供察觉，因此读起来与合规完全一致。

规模不是个例：生产 DB 45,636 条 item 中 20,751 条（约 45%）对主时间线不可见（`enabled=0/feed` 13,662、`enabled=0/x` 3,976、`enabled=1/wechat` 3,113）。probe 命中不可见项是高概率事件，此前几轮通过才是偶然。

`search_id_subquery()` 进一步保证了这一点：`len(q) >= 3` 走纯 FTS `MATCH` 分支，不带任何 source 过滤；只有 `len(q) < 3` 的 LIKE 兜底分支自带 `s2.enabled=1 AND COALESCE(s2.kind, 'feed') != 'wechat'`。而 `_candidate_terms()` 只产出长度 ≥ 3 的词，因此 probe 必然落在无 source 过滤的那一支。

## Decision

把 public timeline 的 source visibility 谓词提为 `src/airadar/web/routes/timeline.py` 的具名常量 `TIMELINE_SOURCE_VISIBILITY_CLAUSES`，由该模块单一持有；`timeline()` 消费它，`build_fts_manifest._timeline_http_match_ids()` import 它。

同一改动内把 `VERIFIER_VERSION` 由 `fts-apply-v4` bump 到 `fts-apply-v5`：`timeline_http_matches` 是 candidate/public HTTP probe verifier 的直接契约输入，语义变化按 [ADR-014](014-ship-base-only-db-and-rebuild-fts.md) 必须显式 bump。

## Alternatives considered

- **放宽 `/api/v1/timeline` 以匹配旧 manifest。** 否决：用户已验收 disabled sources 从公共消费面消失的 upgrade contract，这等于为迎合陈旧 oracle 把内容重新暴露到公网。
- **移除或弱化 `timeline_http_matches` gate。** 否决：它是"服务器重建出的 FTS 确实能服务应用公开查询路径"的唯一端到端证明。为把红闸变绿而删闸就是 fail-open，而服务器这次的 fail-closed 是正确行为。
- **在 producer 内复制该谓词字面量。** 否决：这正是本次事故的形态。同一业务规则目前已有多个形态不同的副本（timeline 的 `s.` 别名、search LIKE 分支的 `s2.` 别名），再加第四个字面副本只是把下一次事故往后推。共享常量让下一次可见性变更在 import/测试期就断，而不是在生产 apply 期。
- **不 bump `VERIFIER_VERSION`。** 否决：会让已绑定 artifact/manifest 的 `rebuilding` / `prepared` checkpoint 在判定输入已变的情况下静默继承那一次自动重试权限。
- **顺带重构 `curated_archive.py` / `items.py` / `wechat.py` / `curated_digest.py` 中的同一谓词。** 否决：它们不是 manifest verifier 的契约输入，改动无法追溯到本次任务。

## Scope

本决策只使 manifest 的 HTTP 期望与 timeline 的 **source visibility** 规则一致。它不声称 `_timeline_http_match_ids()` 与 `timeline()` 完整等价。

在该作用域内已做过一次全量分歧审计：按 verifier 的确切调用形态（无 channel、无 category、无 cursor），两侧的 `search_id_subquery`、`deduped_item_clause`、`_PREFILTER_SCORING_CLAUSE` 均来自同一 owner，source visibility 是**唯一**分歧。count 路径（`_count_timeline_items_with_prefilter`，CTE + JOIN 形态）与 rows 路径（`EXISTS` 子句形态）也逐支比对过、语义等价，因此 verifier 的 `len(item_ids) == total` 断言不会引入第二处失败面。

超出该作用域——带 channel / category / cursor 的调用形态、排序、分页边界——本决策不给结论。

## Consequences

- 新增两条独立回归：`enabled=0` 与 `kind='wechat'` 是谓词的两个合取项，任一单独测试覆盖不了另一项。两条都做过阴性对照：在未打补丁的 builder 上以正确形态失败（不可见 item 出现在 `timeline_http_matches` 中），打补丁后通过。
- 两份 `sources` 夹具此前都只有 `(id, name)`，与生产 14 列 schema 不符——本次缺陷在这些测试面上**根本没有可被断言的对象**，这是它能长期存活的原因。`tests/test_fts_manifest.py` 与 `tests/test_sync_db_producer.py` 均已补入带生产默认值的 `enabled` 与 `kind`。
- 第二份夹具是被全仓测试炸出来的，不是被分析找出来的：决策评审的 blast-radius 论证只追了**直接调用 builder 的单测**，没有沿 `deploy/sync/sync-db-to-server.sh:315` 的 subprocess 调用链追到 producer 集成夹具，于是首次全仓跑出 13 个 `no such column: s.enabled`。教训是方法而非疏漏——找一个共享 schema 假设的影响面时，直接 import 关系不是完整的调用面，subprocess 边界会把它切断。复核确认当前仓内无第三处：其余 manifest 相关测试走生产 migration，其它简化 `sources` 表不经过 manifest builder。
- 既有测试 `test_manifest_http_expectations_filter_nonvisible_raw_fts_matches` 名为"过滤不可见项"，实际只覆盖 prefilter 一维。命名宽于覆盖面的绿灯测试会伪装成已测。
- `VERIFIER_VERSION` 的语义闭包大于 `git grep` 得到的字符串闭包：还包括测试函数名、失败断言文案，以及旧 checkpoint parametrize 参数集合。记录在 `docs/issues/deploy.md` 对应 issue 下，该 issue 保持 open——本次 bump 是它描述的现象本身，不是它的解决证据。

## Waived at review

本决策的 review-gate 报出两条 HIGH，用户于 2026-08-17 显式 waive、带着它们原样落地，两条各自的完整记录与修法方向在 `docs/issues/deploy.md`：

- **manifest 不携带 oracle 语义版本**（INDEPENDENT）：v5 consumer 无法拒绝旧语义 sidecar。属本 ADR 之前既有的契约缺口，本 ADR 既未造成也未闭合。
- **同一 snapshot 无法发布语义修正后的 manifest**（DEPENDENT）：sidecar 名只由 `snapshot_id` 决定，语义修正会改 `manifest_sha256` 而不改名，`mv -n` 冲突后 `exit 42`。

waive 的理由：

- 2026-08-17 实测服务器 `data/` 下无残留 sidecar、无 `incoming` / `upload`，quarantine 已把上一轮搬走。这排除了 Finding 2 的触发前提，以及 Finding 1 的「已 staging 未 apply」那一支。**排除不了** Finding 1 的另一支——Mac 侧 in-flight 的旧 producer 尚未发布其 sidecar；那一支只能靠 Mac 工作树的 producer 代码已含本修复来排除，服务器读数对它没有区分度。
- 两者都 fail-closed，不会把错误数据推上线，但**失败阶段不同**：Finding 1 在 DB 与 sidecar 均已传输、candidate 已启动之后，于 candidate HTTP gate quarantine；Finding 2 在 sidecar 已传输之后、DB commit marker 发布之前停止。两者都不是「停在传输前」。
- 不发布则每一轮 5 小时 cron sync 都会继续 quarantine。

## Known unverified

- 每轮同步都要求五个字段各自找到"raw 独占且 HTTP 可见"的 probe 词。收紧可见性后该候选池变小（在 2026-08-17 那份 44,993-item 快照上，HTTP 可见项 4,549 条，其中非空 author 仅 209 条），找不到会让 producer 在 `_find_probe()` 抛 `ManifestError` 而中止构建。

  该项已用**本 ADR 的实际代码**在同一份快照上实跑闭合（不是模拟）：`build_fts_manifest.py` exit 0，五字段均找到 probe——`title` `'Planet:'` raw=1/http=1、`content_text` `'URL:'` raw=13395/http=3、`source_name` `'Hugging Face Blog'` raw=880/http=806、`author` `'十字路口Crossing'` raw=16/http=1、`title_zh` `'Expanse：多语言'` raw=1/http=1。触发本次 quarantine 的 item `000553f3075cface` 不再出现在任何字段的 `timeline_http_matches` 中。

  但该读数只对那一份 immutable 快照成立，**不对未来快照成立**：`_find_probe()` 每轮重新遍历候选词，选中哪个词、以及该词有几个 HTTP 可见匹配，都随数据变化。上面 `title` 与 `title_zh` 的 `raw=1` 只说明**最终选中的那个词**命中一个 item，不说明该字段只有一个候选词可用——候选空间未测量。收紧可见性确实缩小了可接受词的集合，但"还剩多少"没有读数，因此下一轮能否找到 probe 仍是未验证项。

- 本 ADR 落盘时，修复尚未经过一轮真实的生产同步验收。
