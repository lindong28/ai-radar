# 持续采集评测原始数据

> 新采集配置已于 2026-09-15 获用户批准并启用；真实首轮读数见末节。旧 T5 实验不是新数据来源；过去未留原始输入的窗口不能据业务数据库补成完整候选全集。

## 两条采集链

AIHOT 使用 `scripts/capture_aihot_dataset.py`，保留真实 API 与 SSR 页面、逐条标签观察。`capture --start ... --end ...` 保持两完整 UTC 日的 v1 行为；新增 `--fill-missing` 只发布缺失日的 window_v2，校验已有窗口而不覆盖。新日要求相邻 API pass 的目标 ID 集相同、时间窗口处于两遍共同覆盖、逐条 tags 可由原始 HTML 重放；站方不再提供的时间段明确失败。v1 capture/item 不改版，v2 window 的校验报告显式为 v2；同工具的 `slice` 支持本 capture 的连续 v2 窗口，不借其他 capture 的日期填洞。旧评分/判官消费者不能未经适配直接把它当 v1。

`scripts/capture_aihot_daily.sh` 默认仍使用旧路径。只有 `AIHOT_CAPTURE_FILL_MISSING=1` 才在最近六个完整 UTC 日内寻找缺窗；所有日期仍须由 API 的实际 response Date 验证，不以目录名证明覆盖。原有 30 次/分钟全局限流保留；ReadTimeout、连接错误采用原有限预算重试，认证/WAF 拒绝不重试。新模式需要工具工作树已含本次 clean commit；只设置环境变量而继续使用旧工具会失败。

Radar 仅在 `AI_RADAR_RAW_CAPTURE_DIR` 非空时留档。它挂在真实 fetch/apply、upsert 之前，微信在正文补全后保存；不截断正文，不复用旧模型 prompt 或 T5。每轮 `runs/<run-id>/manifest.json` 保存计划来源、配置摘要、代码 commit/dirty、开始结束时间及状态；`items.jsonl.gz` 保存完整 FetchedItem。配置只保存摘要，不把带 token 的来源 URL 配置打印到清单；新闻输入本身仍是私有数据，不提交到应用仓。

成功空集合、失败、未尝试、304 分开记录。304 必须匹配本次 ETag/Last-Modified 与已归档成功正文；找不到可证明版本时，在同一次计划请求中移除条件头以取得 200，可能增加该次传输体积。留档 I/O 失败使 fetch 非零退出；普通源抓取失败仍保留原产品行为，但覆盖审计不得把该轮当完整。归档目录使用独占写锁，第二个同时写者失败，不覆盖缓存索引。

## 完整性与冻结

以下命令只做本地原始资产检查，不调用 LLM。时间必须显式带时区；UTC 日窗口不等于 UTC+8 自然日，评测取本地日必须裁到两侧均完整的交集。

```sh
PYTHONPATH=src uv run python scripts/raw_capture.py --root data/raw-capture coverage \
  --start 2026-09-16T00:00:00+08:00 --end 2026-09-17T00:00:00+08:00 \
  --enabled-at 2026-09-16T00:00:00+08:00 --cadence-seconds 900 \
  --source SOURCE_ID --json
```

日期、启用时刻、来源和频次均为示例，必须替换为已批准调度及来源启用区间。每个来源配置/启用集合一致的区间分别审计。没有 started 的调度槽判缺轮；started 未完成、来源失败、正文哈希损坏或 304 依赖缺失均不完整。该结论只覆盖指定来源与系统抓取输入，不承诺源站所有曾发布文章。正常 pipeline 忙而跳过的槽同样不是完整原始采集，不能用网站运行正常替代完整性验收。

Radar 冻结：`scripts/raw_capture.py --root data/raw-capture freeze --run RUN_ID --destination data/frozen/SET_ID`，可重复 `--run`，复制并验证全部 304 依赖。该命令只保证所选 run 的依赖完整；必须先通过上面的窗口审计，才可将整窗称为完整评测集。冻结目录不得放在 rolling `runs/` 里面。

AIHOT 冻结：`scripts/capture_aihot_dataset.py freeze --output-root benchmarks/aihot --window windows/START--END/manifest.json --destination data/frozen-aihot/SET_ID`，可重复 `--window`；每个窗口和其完整 capture 都复制并重新验证。冻结位置不能在 rolling captures/windows 内，现有目的地拒绝覆盖。冻结副本仍需备份；本地复制不等于已远端持久化。

## 保留、告警与启用边界

采用用户选择的滚动 30 天＋冻结集长期保留。Radar `retention-preview --days 30` 只列旧且不再被近期 run 引用的候选；`prune --days 30 --apply` 才删除。近期 304 引用的更早 payload 保留，实际体积可能超过严格 30 天。存在未完成 run 时先不清理，避免把不明确依赖删掉。冻结资产不在清理目标中。本轮未运行任何真实 prune。

AIHOT 现有 daily retention 仍可能删除老窗口引用的原始 capture；长期评测集必须先冻结完整依赖，不能只保留 items.jsonl。用户已批准启用留档与 AIHOT 新模式，原有频次不变；随后另行批准 Radar 每日 03:07 自动 prune，调度已安装，本轮不立即清理真实数据。

现场 AIHOT daily 已经由 `run-or-alert --key ai-radar-aihot-capture` 包裹；非零退出复用这条链。Radar 归档失败通过 fetch 非零退出进入现有 pipeline 失败面；缺轮由 coverage 判，不额外建立常驻监控。配置启用不等于已经取得连续完整窗口，须据实际归档与调度审计判定。

## 2026-09-15 恢复读数

AIHOT 9/14 的 daily 因旧两日窗口重叠而 skip；9/15 的 daily 发生 ReadTimeout。定时任务并未停用。随后获授权补收 `[2026-09-13T00:00:00Z, 2026-09-15T00:00:00Z)`，得到 206＋386 条，两日全部 592 条 tags 有真实观察（238 非空、354 明确空），三份 capture/window 落盘重放通过，损坏 raw 的对照输入被拒绝。私有数据仓远端 `captures/daily` 已实测为 `97f5ed0cb027a16d46fe9bedbeba49c55ea7b69e`。主仓 gitlink 未更新，不应把旧 pin 当远端最新值。

本地验证：AIHOT、Radar raw 与 fetch 测试 485 通过、1 个既有错误码断言排除；同一个断言在未改 main 上也失败（期望 output_root_invalid，实际 git_checkout_invalid，既有台账 `docs/issues/testing.md` 第 2 项）。raw 14 个样本覆盖 304 的五种状态、RSS/微信两条 apply 路径、失败/中断、冻结与保留；v1/v2 切片定向 15 个样本覆盖单日/双日/五日、缺日及跨 capture 拒绝。五日样本的较早三日为空，不代表已测真实较早日期非空补收。

daily shell 套件最初在新树与 main 都是 70 通过、5 个相同 retention 断言失败；单独 fixture 日志定位为系统 Python 不支持 `datetime.UTC`。本地改用等价的 `datetime.timezone.utc` 后原套件 75 通过、0 失败，覆盖保留/删除边界、成功/失败/no-request、持久化与限时行为；真实旧数据未清理。这些是离线替身验证，不是新模式生产启用证据。

独立代码审查最初发现 v2 slice 不兼容（HIGH、改动依附）；修复后原 reviewer 两问复核通过，无剩余 HIGH/CRITICAL、承重未核实项、独立 findings 或额外非功能加码。审查时生产启用与完整运行体积尚未核实；后续实测见末节。

系统 Python 兼容修复经同 reviewer 定向核对：UTC 截止语义与删除路径/比较方式未变。在 Python 3.9 环境运行新版会恢复原先失效的过期清理，但这不是当前 cron 的环境：现场 crontab 显式 PATH 为 `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`，按该环境直接执行得到 Homebrew Python 3.14.6，且两种 UTC 对象为同一对象；`/usr/bin/python3` 才是 3.9.6。因此该修复不改变当前 cron 的清理语义，本地 main 整合不启用新留档或补缺开关。

体积抽样：本机可用约 43 GiB；本次 AIHOT capture 磁盘占用约 15.8 MiB。Radar 当前可变业务库只读抽取最近 1000 行（12 来源，fetched_at 为 13:32:13Z—13:32:39Z），完整 JSONL 3,039,035 字节，按来源分别 gzip 共 797,563 字节，其中 26 条正文超过 4000 字。此样本仅用于体积量级估计，不是原始评测数据，不代表完整一轮或日增长；30 天容量仍须启用后测完整轮次再外推。

## 2026-09-15 启用与首轮验证

用户通过启用问题明确答复「启用并验证」。macmini / lindong 的项目 `.env` 于 22:39:13 +08:00 加入 `AI_RADAR_RAW_CAPTURE_DIR=<项目根>/data/raw-capture`，运行时读取已确认生效。Radar cron 仍为每 15 分钟，22:30 开始的旧轮次在配置变更前已启动，不计为新留档首轮。

AIHOT cron 仍为每日 09:37，明确设置 `AIHOT_CAPTURE_FILL_MISSING=1`，转到专用 `continuous-raw-capture-20260915` 工作树并使用主树 venv。应用代码来自本地 main `5347b81`；专用树仅初始化数据 pin 后 clean tool commit 为 `4a40fb59436eca9d7cb92412de1a481a58172521`，不把其运行时数据指针写回主仓。原 t3 树保留、不再由此 cron 采集。数据仓远端仍为 `97f5ed0`，应用代码未推送。

新模式真实运行从 14:41:17Z 到 14:43:29Z：最近六个完整 UTC 日均已有可验证窗口，退出 0，明确报告未发布新 capture；没有覆盖旧窗口。本次验证显式 `AIHOT_CAPTURE_RETAIN_DAYS=0`，未立即清理真实数据。它验证了真实 API 路径与已有窗口校验，不能充当真实新缺日 v2 发布的读数。

Radar 首轮在旧 pipeline 结束后持同一 pipeline 排他锁运行真实 `./run.sh fetch`，未触发后续 LLM 阶段。run `20260915T144949.975393Z-a884ef09` 于 14:49:49.976700Z 开始、14:53:43.881521Z 完成，退出 0；计划 161 个来源均记录 success，其中 56 个非空、105 个明确为空，共 4,254 条输入，223 条正文超过 4,000 字符。JSONL 为 14,210,093 字节，gzip 为 3,222,179 字节。`read_run` 的哈希、逐源/总行数、行结构校验及依赖闭包校验通过；首轮无 304 依赖。这只是完整一轮，不是连续完整日或源站绝对全集，也不能据首轮体积固定外推 30 天容量。

该轮记录 `code_commit=5347b819c0e296a1a2cc9cc1ad74104d698f214d`、`code_dirty=true`：主树原有 WIP 与未跟踪文件被保留，不能冒称 clean 评测基线。将审计起点设为启用前 14:30Z 时，coverage 明确返回 `complete=false` / `before_archive_enabled`，没有把旧时段补成完整。

此后自动调度又产生 run `20260915T150217.020549Z-f52edcbf`，开始时刻为 15:02:17Z。23:05 +08:00 观察到它仍为 started、尚无 completed_at；该读数证明后续运行已进入归档入口，不计为另一轮完整数据。

用户另答「每日自动清理」后，安装每日 03:07 的 `run-or-alert --key ai-radar-raw-retention` 调度，以项目 venv Python 和脚本绝对路径执行 `raw_capture.py --root <项目根>/data/raw-capture prune --days 30 --apply`，日志为 `logs/raw-retention.log`。仅清理 rolling runs 的已验证、过期且无保留依赖候选，冻结集排除。crontab 安装后逐字回读一致；同入口的 `retention-preview --days 30` 为 0 候选，本轮未执行真实 prune，未验证积累 30 天后的扫描耗时。

原始留档位于 `data/raw-capture/runs/`。本机证据为 `logs/continuous-capture-first-run-validation-20260915.json`、`logs/continuous-capture-first-fetch-20260915.log`、`logs/aihot-capture-20260915-144117.log` 及 `logs/continuous-capture-crontab-final-20260915.txt`；这些运行数据和配置副本不提交到应用仓。未来评测仍需选两侧完整交集、核对来源并冻结；旧 T5 不补历史缺口。
