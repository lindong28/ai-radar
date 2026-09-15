# 最近 14 完整日数据盘点

本轮范围：2026-09-01 至 2026-09-14，Asia/Singapore（UTC+8），对应 UTC `[2026-08-31T16:00:00Z, 2026-09-14T16:00:00Z)`。只读本机数据与 Git 对象，未运行模型、抓取、fetch、切分支或修改数据库。以下是供新评测设计使用的数据存在性与字段读数，不判断历史质量达标。

## 结论与身份

最近 14 天不完整。已验证窗仅覆盖 AIHOT timeline 的 UTC 9/5–9/12；按 discovered_at 换成本地日期，完整内日只有 9/6–9/12，9/5 从 08:00 开始、9/13 截止 08:00。更早 T5 快照可补部分发布日期样本，却不能补成逐日完整捕获证明。9/14 在本次明确盘点的数据源中无 AIHOT 记录。

- 主仓 HEAD 的 `benchmarks/aihot` gitlink 是 `76f62cfb61b887f81d7e06cbab49b7dc8ab10cf5`，不能拿它代表日采集分支。
- 实际读取 Git 对象的仓：`/Users/lindong/research/ai-radar-worktrees/t3-aihot-recapture-20260907/benchmarks/aihot`。`git ls-remote origin refs/heads/captures/daily` 当轮返回 `623728b2e0c9f24087add34e3cf96c0b68f80f47`，exit 0；该工作树 status 为空，HEAD 同为 623728b。本报告固定读此 commit 的 `windows/*/items.jsonl`，没有读取可变分支名来汇总。
- 所有本地 refs 的 `git log --all --format= --name-only -- windows` 只列出同样 10 个 items.jsonl 路径：8/19、8/20 与 9/5–9/12；没有发现另一个最近窗。此结论限本地 Git 对象及已核远端分支，不是远端所有不可达历史穷举。
- 补充源：主仓 `.label-serve/round45-human/t5/r2_raw/aihot_items_raw_r2.json`，2,284 行，SHA-256 `77445e1ca787eccea981e213b7921ee663c86872dcc7a9836f96eb277a7c174f`，published_at 范围 `2026-08-29T05:36:14.000Z` 至 `2026-09-05T05:48:21.000Z`，目标日内 1,760 行。它没有 tags、discovered_at、来源名称字段，不与验证窗等同。
- Radar：主仓 `data/radar.db`，通过 SQLite URI `file:data/radar.db?mode=ro` 读取。主盘点读事务内为 123,684 个 items；全库 max published_at=`2026-09-15T08:32:08Z`，max fetched_at=`2026-09-15T09:03:05Z`。这是本地库当前快照，未证明等于线上数据库；未生成冻结备份，后续正式评测须另冻结当时输入。
- 明确副本检查：`data/radar.db.snapshot` 与 `.shipping` 各 118,149 行，max fetched_at 均为 `2026-09-13T08:17:19Z`；`data/ai_radar.db` 的 items 为 0。副本不能补最新日期。未全盘遍历。

## 逐日：按条目 published_at 转成本地日期

A 为固定 Git 验证窗语料，T5 为补充快照。唯一 URL 使用项目 `airadar.eval.aihot_fit.build.normalize_url` 的键；A 匹配数按当前 Radar 全库寻找同键条目，因此不是同日一对一配对数。A 的窗归属本身使用 discovered_at，故 9/3、9/4 有少量旧发布日期记录并不表示采集窗完整覆盖这两天。

| 本地日 | A 行/唯一 URL | A 精选 true | A 匹配且正文非空 URL | T5 行/唯一 URL | Radar 行/唯一 URL | Radar 正文非空 | 已评分/已 enrich | 曾精选 | 当日 fetched | 当日 curation runs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 09-01 | 0/0 | 0 | 0 | 331/331 | 2358/2357 | 2358 | 636/635 | 68 | 2045 | 11 |
| 09-02 | 0/0 | 0 | 0 | 424/423 | 2942/2940 | 2942 | 810/796 | 89 | 3042 | 33 |
| 09-03 | 1/1 | 1 | 1 | 416/415 | 3596/3593 | 3596 | 866/811 | 108 | 3554 | 42 |
| 09-04 | 4/4 | 0 | 3 | 394/394 | 2884/2883 | 2884 | 787/743 | 28 | 2983 | 44 |
| 09-05 | 132/132 | 9 | 128 | 195/194 | 2245/2242 | 2245 | 595/577 | 61 | 2207 | 25 |
| 09-06 | 169/169 | 6 | 160 | 0/0 | 1674/1674 | 1674 | 376/376 | 50 | 1592 | 35 |
| 09-07 | 234/234 | 4 | 215 | 0/0 | 2451/2451 | 2451 | 498/497 | 60 | 2471 | 40 |
| 09-08 | 296/295 | 8 | 272 | 0/0 | 2422/2421 | 2422 | 532/508 | 71 | 2951 | 33 |
| 09-09 | 491/483 | 23 | 452 | 0/0 | 2857/2853 | 2857 | 855/854 | 63 | 2457 | 14 |
| 09-10 | 431/424 | 27 | 388 | 0/0 | 3897/3895 | 3897 | 792/792 | 99 | 3786 | 48 |
| 09-11 | 385/380 | 8 | 344 | 0/0 | 3001/3000 | 3001 | 688/687 | 84 | 3030 | 36 |
| 09-12 | 320/315 | 6 | 281 | 0/0 | 2570/2569 | 2570 | 544/544 | 64 | 2579 | 42 |
| 09-13 | 76/76 | 6 | 67 | 0/0 | 1746/1746 | 1746 | 393/393 | 39 | 1741 | 37 |
| 09-14 | 0/0 | 0 | 0 | 0/0 | 2840/2840 | 2840 | 651/650 | 77 | 2865 | 59 |

Radar 目标日共 37,483 行。已评分/已 enrich 是当前库存在成功 output_json 的条目数，取最新 id 的 enrich 输出；不是当日完成量，也不证明生产选择时已可见。曾精选按 curated_items 成员存在性计，有理由的目标日条目共 961；不能把未进入 curated_items 自动变成一次明确 negative 标注。

## 字段非空数

0 分与 false 都算已观测值；空字符串、null、空列表不算非空。tags 空列表也可能是明确“无标签”，不能与缺字段混为一谈。

| 本地日 | A 标题/摘要/分数 | A 分类/tags/理由 | T5 标题/摘要/分数 | T5 分类/精选 true/理由 |
|---|---:|---:|---:|---:|
| 09-01 | 0/0/0 | 0/0/0 | 331/324/331 | 331/6/6 |
| 09-02 | 0/0/0 | 0/0/0 | 424/412/424 | 424/13/13 |
| 09-03 | 1/1/1 | 1/1/1 | 416/397/416 | 416/22/22 |
| 09-04 | 4/2/4 | 2/2/0 | 394/380/394 | 394/17/17 |
| 09-05 | 132/98/132 | 102/99/9 | 195/186/195 | 195/9/9 |
| 09-06 | 169/62/169 | 62/60/6 | 0/0/0 | 0/0/0 |
| 09-07 | 234/145/234 | 91/87/4 | 0/0/0 | 0/0/0 |
| 09-08 | 296/284/296 | 106/103/8 | 0/0/0 | 0/0/0 |
| 09-09 | 491/481/491 | 210/209/23 | 0/0/0 | 0/0/0 |
| 09-10 | 431/425/431 | 183/181/27 | 0/0/0 | 0/0/0 |
| 09-11 | 385/377/383 | 153/149/8 | 0/0/0 | 0/0/0 |
| 09-12 | 320/313/315 | 132/131/6 | 0/0/0 | 0/0/0 |
| 09-13 | 76/74/76 | 29/28/6 | 0/0/0 | 0/0/0 |
| 09-14 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |

A 目标日共 2,539 行：标题 2,539，摘要 2,262，分数 2,532，分类 1,071，非空 tags 1,050，selected 有值 2,539（true 98 / false 2,441），理由 98。T5 目标日 1,760 行：标题/分数/分类/selected 有值均 1,760，摘要 1,699，true 与理由各 67，tags 字段完全不存在。分类非空不保证有效类别语义，本盘点未把枚举值规范化或重判。

Radar 目标日 37,483 行中，最新成功 enrich 的 title_zh、summary_zh、tags、why_recommend 各非空 8,863，primary_category 非空 7,918。这些字段不能外推到未 enrich 的候选，也不能把当前最新输出当历史生成时状态。

## URL 匹配及原文边界

| 目标日集合 | 行数 | normalize 后唯一 URL | 当前库匹配 URL | 至少一条正文非空 URL | 对应多个 Radar item 的 URL | 匹配 Radar item 数 |
|---|---:|---:|---:|---:|---:|---:|
| A | 2539 | 2504 | 2302 | 2302 | 18 | 2442 |
| T5 | 1760 | 1753 | 1622 | 1622 | 9 | 1734 |
| A ∪ T5 | 4299 | 4193 | 3864 | 3864 | 25 | 4011 |

合并只是集合盘点，不是直接可用的评测集：同 URL 可能有多来源、多个内容版本与冲突参照输出。4193−3864=329 个 URL 本机当前库未匹配。命中不证明原文完整、同一时点或同一来源；配对构建要保留全部候选再按明确规则确定 input，而不是 `rows[0]`。本次没有替设计者选择配对规则。

items 保存当前 title/content_text/published_at/fetched_at/content_hash；item_evaluations 另保存 stage、完整 input_json、output_json、numeric_json、ruleset_version、model_id、evaluated_at、error；curation_runs 保存 input_eval_ids/output_curated_ids/weights_json/threshold/created_at，curated_items 保存 rank/weighted_score/reason_json/summary_json。因此有历史调用与选择线索可用，但本次未逐条证明每个 run 引用与历史输入均可闭合。input_json 中实际 Title/Content 才是“当时模型看到的输入”候选证据，当前 items 正文不能代替它。fetched_at 也不能证明其后从未被覆盖。

## 两条真实配对展示样本

以下文字仅转述已存数据，并未重新核实新闻事实。用于展示输入与参照结构，不能算随机样本或质量结论。

1. 原始 URL `https://www.ithome.com/0/998/661.htm`；AIHOT id `cmtnphe3t07vfroqsvrbd9enn`，来源“IT之家（RSS）”，published_at `2026-09-05T00:42:24.000Z`，discovered_at `2026-09-05T01:30:18.743Z`。AIHOT 标题“奥尔特曼致歉 GPT-6 Astra 发布混乱，现已面向所有 Plus / Pro 等用户推出”，score 82，category `ai-models`，selected=true，tags `[OpenAI, 模型发布]`；摘要涉及发布时间、访问先后争议及补偿机制。Radar id `9416b4bd62bef15c`，source_id `ithome`，published_at `2026-09-05T00:42:24Z`，fetched_at `2026-09-05T07:20:46Z`，当前正文 558 字符，最新 enrich category=`product`，tags `[OpenAI, 模型发布, 产品更新]`。这是可展示的同 URL、同来源时间接近案例，仍需冻结真实 input_json 后用于正式评测。
2. 原始 URL `https://openai.com/index/gpt-6-astra`；AIHOT id `cmtnt5cb50b8mroqs7x4pukfm`，来源“OpenAI：官网动态（RSS · 排除企业/客户案例）”，published_at `2026-09-03T11:00:00.000Z`，discovered_at `2026-09-05T03:12:56.001Z`，score 88，category=`ai-models`，selected=true。匹配到的一条 Radar 记录 id `72ace77c327bbe69`，source_id=`buzzing_hn`，published_at `2026-09-04T00:07:48Z`，fetched_at `2026-09-04T19:16:58Z`，正文仅 67 字符；其 enrich 明说除名称、域名和 HN 点数外没有模型细节。这个例子直接说明“URL 匹配且正文非空”不能证明拥有 AIHOT 编辑时的输入。

## 可复算证据与取证修正

主要命令退出码：`git ls-tree HEAD benchmarks/aihot`、`git ls-remote origin refs/heads/captures/daily`、固定 SHA 的 `git ls-tree -r --name-only 623728b windows` 与 `git show 623728b:<path>`、`git log --all --format= --name-only -- windows` 均 exit 0。SQLite 先 `.schema items` 以及查询 sqlite_master 中 `item_evaluations/curation_runs/curated_items`，exit 0。汇总运行形态为 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --no-sync python -`，以 `sqlite3.connect('file:data/radar.db?mode=ro', uri=True)`、`begin` 和上述固定 Git 对象聚合，主汇总 exit 0；补充汇总最终 exit 0。

UTC 窗行数（所有窗文件字节 SHA 均与各 manifest 的 items.sha256 相符，10 个文件、10 个不同 SHA）：8/19=348，8/20=330，9/5=209，9/6=175，9/7=229，9/8=392，9/9=467，9/10=416，9/11=384，9/12=268。9月窗总计 2,540 行，与目标 published_at 窗的 2,539 不同，正是两个时间轴不同；不可照抄窗口行数替代日期过滤结果。

对照读数：同 status id 的 `x.com/a/status/123` 与 `twitter.com/b/status/123` 规范化键相同；`https://a.com/?id=1` 与 `?id=2` 不同；UTC `2026-09-01T16:00:00Z`→本地 9/2，前一秒→9/1；合成不存在的 `.invalid` URL 查询为 false，实际非空索引有 123,283 个键。对照仅核此处匹配与时区逻辑，不是 normalize_url 全输入面的测试结论。

发生并已纠正的取证问题：一次补充汇总遇旧8月行 discovered_at=null，exit 1，增加缺失值分支后重跑；一次中间脚本用 defaultdict 读取未匹配键，污染随后 membership 统计，T5 与并集匹配数不可信，已用普通 dict + `.get()` 全量重算，上表仅使用最终读数。一条诊断组合命令因猜测不存在的 settings.py/config.py 路径而 exit 2，改查实际 db.py 后确认默认路径；此失败不作为数据缺失依据。

未核实边界：AIHOT 上游过去每天的绝对全集、早期 T5 完整度、当前数据库是否等于生产、被匹配正文是否原文完整、逐条历史输入与当时版本的闭合、未发现 URL 的其他私有副本。以上均不在本轮设计盘点中补采或修复，交由主报告明确为后续评测准入或数据补齐事项。
