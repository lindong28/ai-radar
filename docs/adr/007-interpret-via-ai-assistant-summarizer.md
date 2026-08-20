# ADR-007: 微信文章解读复用 ai-assistant summarizer，save_decision 作单一闸门

- Status: accepted
- Date: 2026-06-06

> 回溯 ADR——记录 commit 2f82f0d（`interpret` stage、migration 009 `wechat_interpretations` 表）上线时未落档的设计决策。架构现状见 architecture.md，运维细节见 docs/operations/wechat-ingestion.md。

## Context

`interpret` 是 pipeline 的末位阶段（`fetch → prefilter → score → enrich → curate → interpret`），只处理启用的微信公众号源 item，为每篇文章生成结构化中文解读（推荐等级、摘要、标签、原文回链），展示在 `/wechat` tab。

设计这个阶段时有一个先决约束：解读结果不只是给 ai-radar 自己的 `/wechat` 页面用，还要进入 **ai-assistant 的知识库（KB）**——含 embedding，让跨项目的 `search-knowledgebase` 能检索到这些微信文章解读。ai-assistant 已经有一套成熟的 `summarize-article` 逻辑（`agents/summary-agent/summarize.sh` + `run.sh`），它产出推荐等级、摘要、标签，并自带一个 `save_decision`（这篇是否值得存入 KB）的质量判断。

围绕"如何生成解读、如何决定展示、如何与 ai-assistant 协作"，有几个未来 agent 不知情就可能做出矛盾改动的选择点：

1. 解读用谁生成——ai-radar 内建原生 summarizer，还是复用 ai-assistant 的？
2. `/wechat` 展示和 KB 写入由谁判定——ai-radar 自有规则，还是 ai-assistant 的 `save_decision`？
3. ai-assistant 依赖缺失时 pipeline 怎么办——阻断，还是降级跳过？
4. 回填 / 处理能否并发？

## Options Considered

### 解读生成：内建原生 summarizer vs 复用 ai-assistant

**方案 A——ai-radar 内建原生 summarizer + 自有展示判定**
- Pros: 不依赖 ai-assistant 这个外部 repo，preflight 更简单；单 repo 自洽，无 subprocess 调用开销
- Cons（否决理由）: 解读结果终归要进 ai-assistant KB（embedding + 跨项目检索是设计目标）。若 ai-radar 自己生成解读、再单独想办法写 KB，等于**分裂两套 summarizer 与两套质量判定**——同一篇文章在 `/wechat` 看到的推荐等级/摘要和 KB 检索到的可能不一致，且要在 ai-radar 侧重复维护一份与 ai-assistant 等价的评分逻辑。口径分裂 + 重复维护，收益不抵成本。

**方案 B——零拷贝复用 ai-assistant summarizer（subprocess）**
- Pros: 解读产物天然落进同一个 ai-assistant KB，`/wechat` tab、KB、`search-knowledgebase` 三者**同口径**；不重复造评分轮子；对 ai-assistant 是零拷贝复用，不 fork、不改其代码
- Cons: 引入跨 repo subprocess 依赖（`AI_ASSISTANT_ROOT`），需要 preflight 守护；受制于 ai-assistant 工具的接口与并发约束（见下文串行限制）

### 展示 / KB 写入的闸门：ai-radar 自有判定 vs ai-assistant 的 save_decision

**方案 A——ai-radar 侧另立展示判定**
- Cons（否决理由）: 会出现"`/wechat` 展示了但 KB 里没有"或"KB 里有但 `/wechat` 不展示"的口径漂移，破坏与方案 B 一脉相承的一致性目标。

**方案 B——用 ai-assistant summarizer 的 `save_decision` 作单一闸门**
- Pros: 同一个布尔同时决定**是否展示在 `/wechat`** 和**是否写 KB**，保证「`/wechat` 展示的就是 KB 里有的」恒等式，永不漂移；信任 ai-assistant 的质量判断，ai-radar 不重复评分

## Decision

选择两个方案 B：

1. **解读复用 ai-assistant summarizer（零拷贝）**——`interpret/runner.py` 通过 subprocess 调 ai-assistant 的 `summarize.sh` / `run.sh`（cwd=ai-assistant），喂库里的 `items.content_text`（绕开微信重新抓取），patch meta 注入真实 url / source / publish_date / title 后由 `run.sh --save-from-batch` 写 KB（index + embedding）。`run.sh --check-url` 命中已有 KB 条目时不重复调 LLM，直接复用已有 summary。核心理由是 **KB 回写 + 跨项目检索一致性**：产物必须进同一个 ai-assistant KB，既然如此就复用其 summarizer，让 tab / KB / 检索同口径；"避免重复造评分轮子"是次要收益。

2. **`save_decision` 作单一闸门**——summarizer 返回的 `save_decision` 同时控制 `/wechat` 是否展示与是否写 KB。`save_decision=1` 的条目展示 + 回写 KB；`save_decision=0` 只在 `wechat_interpretations` 留处理记录（不展示、不写 KB、避免每轮重复消耗 LLM）。ai-radar 不在自己这侧另立判定。`/wechat` 详情页从本库 `summary_md` 渲染，请求时不读 ai-assistant 文件系统（本地副本独立于 KB 文件，但内容口径由同一闸门保证一致）。

3. **interpret 作为末位 fail-safe stage**——preflight 检查 `AI_ASSISTANT_ROOT` 与 `summarize.sh` / `run.sh` 存在且可执行；缺失时打印 `skip interpret...` 并正常返回（exit 0），**不阻断**前置的抓取 / 精选。每篇文章的处理也是 per-item fail-safe：单篇异常记 error 行后继续下一篇，不让一篇失败拖垮整轮。

4. **interpret 串行（并发 1）**——`run_interpret` 逐篇串行处理（`for row in rows`），不并发。约束来自复用的 ai-assistant KB 写入器不是并发安全的（详见下文 Consequences）。

## Consequences

- `/wechat` 与 ai-assistant KB 永远同口径——展示的就是 KB 里有的。未来要改"展示哪些文章"，应改 ai-assistant 的 `save_decision` 逻辑或在其之上叠加过滤，**不要**在 ai-radar 侧另立一套展示判定，否则会重新引入口径漂移。
- `interpret` 对 ai-assistant 是硬依赖但非阻塞依赖：ai-assistant 不可用时整条 pipeline 仍能抓取 / 精选，只是 `/wechat` 不更新。跨 repo 路径与 preflight 契约见 docs/operations/wechat-ingestion.md。
- **不要并行化 interpret**：复用的 ai-assistant KB 写入器对 `index.json` + `vectors.npy`（顺序严格对应）是整文件读-改-写、非原子，并发 save 会互相覆盖导致 index/vectors 错位或损坏；且对 ai-assistant 是零拷贝复用（不 fork 不改其代码），故 runner 只能串行。稳态 cron 每轮只增量处理新增几篇，串行足够。安全的提速形态、上游修复方向详见 docs/issues/general.md（"interpret 回填无法并发"）。
- 解读质量与推荐口径的演化绑定在 ai-assistant 的 summarizer 上——调整推荐评级 / 摘要风格应去 ai-assistant 改 prompt，ai-radar 侧只做展示渲染（markdown-it-py 渲染 + nh3 sanitize）。
- `wechat_interpretations` 表（migration 009）在 `radar.db` 内保留 `summary_md` / `abstract` / `tags_json` 独立副本，Web 请求不读 ai-assistant 文件系统——KB 是写入目标与一致性来源，不是 Web 读路径。

## 修订记录

**2026-08-20 — 「interpret 回填无法并发」的 issue 指针已迁移。** 上面 Consequences 段指向 `docs/issues/general.md` 的那条 issue 已于 2026-06-15 判为 resolved（结论是**维持串行、wontfix-by-decision**：一次性历史回填已完成，稳态下 cron 每轮只增量处理新增几篇，串行足够，并发改造零长期收益；上游修复属 ai-assistant 侧），并已移入 [docs/issues/archive/closed.md](../issues/archive/closed.md)「interpret 回填无法并发——复用的 ai-assistant KB 写入器非并发安全」。按此路径去 `general.md` 找不到它。本决策的「不要并行化 interpret」结论不变，且已被那次裁决确认。
