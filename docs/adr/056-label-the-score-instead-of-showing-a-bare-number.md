# ADR-056: 评分显示语义标签，且不写死分母

- Status: Accepted
- Date: 2026-08-17

## Context

与 aihot.virxact.com 的同条件成对对比中，本站卡片右上角显示的是裸数字 `● 89`，语义只存在于 `title` tooltip 里；参照站显示 `AI 评分 73/100`。tooltip 在移动端不可达，而移动端与桌面共用同一套 DOM（ADR-012）。

## Decision

评分渲染为 `AI 评分 89`（CSR `app.js` 的 `scorePill()` 与 SSR `web/templates/_prepaint_list.html` 两条路径同形）。

**不写分母。** `src/airadar/curator/score.py:8` 的 `TIER_MULTIPLIERS = {"T1": 1.25, "T1.5": 1.0, "T2": 0.75}` 直接乘在加权分上（`score.py:29`），T1 信源的 `weighted_score` 因此可以超过 10。生产实况读数：`GET /api/v1/timeline?limit=100` 的 100 条里 **5 条 > 10、最大 10.75**，tier 分布 T1=94 / T1.5=3 / T2=3——写 `/100` 会渲染出 `108/100`。裸数字只是含混，写死的分母是**假值**；标签本身已经回答了"这个数字是什么"。

**窄屏只留「AI」。** 390px、最长信源名（`Hacker News popular via buzzing.cc`）下实测：

| 标签 | pill 宽 | 信源名宽 | 信源名截断 | 顶栏高 |
|---|---|---|---|---|
| `AI 评分` | 78 | 217 | **是** | 27 |
| `AI` | 47 | 230 | 否 | 19 |
| 无标签 | 33 | 230 | 否 | 19 |

（该信源名完整渲染需 230px。上表用本仓真实类名在 390×844 下量得。）

窄屏用**视觉隐藏**而非 `display: none` 隐去「评分」二字：后者会把它一并移出可访问性树，读屏器只剩「AI 108」，而这段文字正是用来解释那个数字的。实测视觉隐藏后 `rest` 盒宽 1px、不占布局（pill 仍为 47px、信源名不截断），而 `.timeline-score` 的可访问文本仍是完整的 `AI 评分 108`。

不整体隐藏标签——移动端正是 tooltip 够不到的地方，裸数字在那里最没法读。

**不隐藏 0 分。** 原方案含"未评分不显示"，已撤：`src/airadar/presentation/summary.py:145` 会给任何有 scoring 数据的条目算出真分，真实低分四舍五入后同样是 0，按 `score === 0` 隐藏会误藏真实低分条目。要正确区分需把 `timeline.py:53` 的 `scored_any` 投影成新字段，而 `FeedItem` 是 timeline 与 curated 归档的共享模型、且 `docs/contracts/ux-contract.md` 有两条明文要求"所有条目显示分数标签"——那是一个独立单元，不在本轮。

## Scope

- 只覆盖列表卡片的评分元素（`itemCard` + `_prepaint_list`）。
- 未改 API schema、未改 `weighted_score` 语义、未改 curator 的 tier 乘数。
- UX 契约的「分数标签」两条（L1 时间线页、L2 HP-1）**仍然成立**——标签依然逐条存在，只是从 `89` 变成 `AI 评分 89`。

## 已知未验证 / 待办

- 评分元素的 `title` 仍写「满分 10，阈值 6.5 进精选」，对 T1 条目是假的。改这句要先决定真正的口径（上界 12.5？分 tier 说明？还是把 tier 乘数移出展示分），属独立决策——已记入 `docs/issues/ux-issues.md`。
- 窄屏读数取自 390×844 单档与三个真实信源名；未覆盖其它断点与更长的名字。
