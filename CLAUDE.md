# Repository Guidelines

## Project Overview

AI Radar is a Python 3.12 FastAPI application for collecting AI-related RSS, X-compatible RSS, and WeChat sources, scoring and curating items, and serving a public read-only web UI.

## Working Rules

- Keep runtime secrets and local deployment paths out of git. Use `.env`, environment variables, or gitignored generated config files.
- Prefer configuration with neutral defaults over hardcoded maintainer identity, domains, or local filesystem paths.
- Optional external integrations must fail closed or skip cleanly when disabled or unconfigured.
- Use `uv run` for Python commands so tools run inside the project environment.
- When running focused pytest commands that touch the database, set `AI_RADAR_DB` to a temporary path to avoid collisions with local services.

## 对齐 AIHOT：达标线与迭代机制 (BINDING)

本仓的长期目标之一是**在用户可见的指标上足够接近 AIHOT**。判据与回路在
[docs/references/aihot-approximation-metrics.md](docs/references/aihot-approximation-metrics.md)（897 行）。
**不必整份读**——按你要做的事取：

| 要做什么 | 读哪一节 |
|---|---|
| 判达没达标 | 「达标线」 |
| 换/改方案 | 「方案状态表」**加**「五条轴的处置」——**只读前者会漏掉各轴的重开条件** |
| 取任何读数前 | 「量具纪律」 |
| 不确定哪个面权威 | 「⚠️ 权威观察面 2026-09-11 换了」 |
| 下一步做什么 | 末节「2026-09-11 收口」 |

此处只放不可省的那几条（不写条数——写死会静默过时）：

- **选题范围与编辑口味上，我方定位与 AIHOT 冲突时一律取能对齐的那条**（用户 2026-09-11 常设指示，
  原话：「记住我们的目标是对齐 AIHOT，所以以后这类情况一律按照能对齐的路线去做」）。
  它管的是**选题范围与编辑口味**这一类：AIHOT 收某类内容而我方出于自己的定位不收时，**改我方去接它**。
  首个实例：AIHOT 收「大厂重要硬件/科技发布」（实测那 2 条给了全篇新闻稿后我方仍判非 AI，
  **而且判得对**——是我方口径窄，不是判错），用户裁定放宽。
  **`方法忠实 vs 结果逼近` 这个分岔上，只有「用 AIHOT 自己的逐类精选率派生排序系数」这一处
  已于 2026-09-12 由用户裁决为允许，为它不必再停轮**（此前这里写着整个分岔"仍然悬着、要用时先问用户"，
  那句作废；**但分岔的其余部分没有被裁决，遇到仍要问**）。
  **同一次裁决的否决项**：「直接拟合我方读数」（以我方 TV / 达标数为目标函数反解系数），
  **「按目标比例硬配额」仍被禁**。允许的是**改判据本身**——以输入为自变量的规则
  （区别见 `~/.claude/references/prompt-distribution-fitting.md`）。**别外推成"这类分岔以后都不用问"。**
- **达标线**（用户 2026-09-10 裁定）= **逐类占比落进 AIHOT 该类的 95% CI**，不是整页 TV
  （TV 的绝对值**指不出是哪一类**——相互抵消的偏差在它上面看不见）。
  **权威观察面自 2026-09-11 起是「归档面」**（用户裁定，见下）：
  `uv run python scripts/eval/measure_archive_composition.py --record`。
  **看 `P(5/5)` 那一行再看 `k/5`**：判据自己的零假设，归档面 0.899——`3/5` 在它下面是真信号。
- **首页是「归档面」不是单轮 40 条**（[ADR-006](docs/adr/006-curated-archive-mode.md)：跨 run 并集按
  `published_at` 取前 40，**不看我方排序分**；约 48 run/天）。三条后果：
  **`measure_curated_composition.py` 是代理面、只用于 A/B**；`composition-history.jsonl` 带
  `surface: "archive"` 的才是新口径、**两代不可比**；**后置删减型机制在重放面上的效应系统性偏高**
  （本轮丢一条，不撤销它在更早 run 的归档成员资格）。机制细节与两面出界类的对照见指标档同名节。
- **每完成一轮迭代，给那次读数补一个 `--record`**：它追加到 `scripts/eval/composition-history.jsonl`，
  那是"随迭代逐步逼近"这条期望**唯一**的观测面。不 record，趋势就不存在——探索性对照别 record。
- **归因先于干预**：动 prompt / 权重 / 系数之前，先在
  [docs/issues/aihot-fit-eval.md](docs/issues/aihot-fit-eval.md) 写下带
  `differential_prediction` 的假设（取数**之前**写），再取对照读数。本仓已有多次"先取数后解释"
  导致结论自撤的记录，那一节列着。
- **五条轴 2026-09-10 都测过一遍，重开各有条件**（类别乘数 · enrich 分类边界 · 打分聚合 · 流/信源 ·
  深度）。逐条状态与重开条件在指标档「五条轴的处置」节，**别把它读成五条都已证伪**：深度那条是
  「效应未建立」（训练/留出方向相反），不是测出没有。两条要点：**`CATEGORY_MULTIPLIERS = {"paper": 0.95}`
  是当前生产、经用户裁决上线的，不要去撤**（被否的是在它之上再加系数）；**用 AIHOT 自己的真分数当排序键
  会让达标从 3/5 掉到 0/5**，所以打分轴封顶低于现状。
- **量具五条最贵的纪律**——完整清单在指标档「量具纪律」节，每条都来自一次实测失败
  （2026-09-11 数过：表格 10 条 + 展开小节 10 条；**这个数会长，引用前重数一遍**）：
  **① 逐日归属用 `_shanghai_date`，不用 `published_at[:10]`（UTC 日）**——错开一天，已作废过读数；
  **② 比较窗口必须夹进参照语料的时间覆盖**；**③ 比两套打分器用同一池的百分位、不用绝对分**；
  **④ 代理指标不算验收——权威指标要直接量，且改善要跨 split 同向才算效应**（POOLED 的改善不算）。
  ⚠️ **这一条会把逐类干预带进死胡同，而出口不是给它开口子**（2026-09-12 走过一遍）：权威量具自陈
  **不能 A/B**，能 A/B 的模拟器**逐类误差 5.92pp 已大于要追的位移**（model 约 5.2pp / industry 约 2.4pp）
  ⇒ **当前没有任何仪器能在权威面上评估一个逐类干预**。**正解是补那把尺子**——指标档末节已点名两个候选
  并写明「下一轮第一件事是补这个仪器，不是再试一个系数」。
  **跨 split 两处窄化**（细则见指标档同名展开条）：归档是累积的，故 `--until` 的几个点是**嵌套序列、
  不是独立观测**，**不得当 split 证据**——归档面要配对就用**逐日**；且**参照物本身非平稳**
  （它的 model 占比一周 42.0%→17.9%、CI 不相交），"不同向"要先排除是它在动。
  **⑤ 机制读的标签必须与计分器读的分开，且默认取生产那一刻拿得到的那份**——2026-09-11 实测：闭环的反馈与封顶都读了 AIHOT 自己的标签，读数 `5/5 · TV 0.090` 全是 oracle，换成我方 enrich 后是 `3/5 · 0.131`。**它不报错、不异常，只是让数字变好**，时间劈与负控制都拦不住。
- **本 program 的评审循环默认 2 轮，第 3 轮起要先自证没在空转**（2026-09-12 新增，项目级上限）。
  判据是机械的：**逐条列出「这条新 finding ← 我方上一轮的哪一项修复」**，不写百分比。
  过半追得到上一轮修复即**不再起下一轮评审**，按现有证据交用户裁决「继续还是收口」。
  ⚠️ **它停的是评审轮次，不是"停止修我已经认同的错"**（2026-09-12 首次使用时就踩到这个歧义）：
  评审指出的、你看一眼就同意且改动是**把范围改小**的措辞错，照改不误——只是改完不再回去要第三轮背书。
  依据是两次同形失控：H11（plan 审查 4 轮 49 条、第 5 轮 owner 手动终止）与 2026-09-12
  的 decision-review（3 轮 gate + 2 轮复核、末轮 8/8 可追溯），见 `docs/issues/harness-issues.md` H11。
  **这条只约束本 program 的决策评审轮次，不改 user-scope `decision-review` 的通用契约。**

**CI 会随数据积累变窄，所以判据会变严**：同一个系统的某一类可能从 IN 变成 OUT。那不是回归，
是分辨力提高——读到这种翻转先看 `n_reference` 有没有变大。

## Frontend Asset Cache Busting (BINDING)

改完 `web/static/app.js` 或 `web/static/style.css`，**跑一次** `uv run python scripts/bump_frontend_assets.py`——它按内容摘要重算 `?v=` 版本串、改写全部 HTML 引用并更新 `web/asset-pins.json`。EdgeOne 对这两个精确路径强制节点缓存 7 天（[ADR-039](docs/adr/039-route-news-through-edgeone-dns-only-cname.md)「决策」节），漏 bump 就是**部署了但线上不生效**。

- 版本串必须与资源内容一起进**同一发布单元**——不是本地 commit：按 [ADR-042](docs/adr/042-isolate-production-deploy-commit-from-local-main.md) 生产部署 commit 是在 `tencent/main` 上复放出来的，复放时挑漏 HTML 则本地 commit 再完整也破契约。
- `uv run pytest tests/test_frontend_asset_versions.py` 只保证**仓内**一致；**已上线的边缘陈旧只能从真实公网观测**——origin 已是新代码而部分边缘节点仍吐旧副本，本地与 curl 都可能看不出来。
- 改 style.css 时 `/wechat` **要多做一件事、不是少做**：它把 style.css 内联进 SSR HTML，故没有 `style.css?v=` 可 bump，改为按 ADR-039「决策」节里那条内联契约，在约 120 秒缓存窗口后从真实公网 `/wechat` 验证内联内容。（它照常引用 `app.js?v=`，改 app.js 时不例外。）

- 部署前跑 `./run.sh admin edgeone check`：强制缓存规则住在腾讯云控制台，是仓外权威，控制台多出一条路径时仓内测试全绿也看不见。**exit 2 表示未核实、不等于通过**（0=无漂移，1=有漂移，2=未核实）。

bump 范围为何是"全部 HTML"、以及已上线后的补救，见 [docs/experiences/frontend.md](docs/experiences/frontend.md)。

## Useful Commands

```bash
uv sync
./run.sh admin db migrate
./run.sh admin sources reload
./run.sh fetch
./run.sh serve --host 127.0.0.1 --port 8000
uv run pytest
uv run ruff check src tests
uv run mypy src
```

## Verification Notes

- For source-snapshot checks, use `git grep` rather than broad filesystem scans so generated, ignored, and local-only files do not produce false positives.
- For open-source readiness comparisons, use the immutable `opensource-baseline` tag when a before/current comparison is required.
- Do not rewrite repository history from this checkout; any history cleansing must happen in a disposable clone.
