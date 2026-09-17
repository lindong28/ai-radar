# 持续采集评测原始数据

> 新采集配置已于 2026-09-15 获用户批准并启用；真实首轮读数见末节。旧 T5 实验不是新数据来源；过去未留原始输入的窗口不能据业务数据库补成完整候选全集。

> 2026-09-17 的恢复修复已获批准并安装调度：AIHOT 每小时第 7 分钟、健康检查每 5 分钟，Radar 保留每 15 分钟。首个新完整日窗、远端发布和连续性尚待验收；当前实际读数见本页末节。

## 两条采集链

### 独立采集与处理（2026-09-16 已获批启用）

本地验证：115 项定向测试通过，2 项真实微信公众号联网测试跳过；真实 loopback 双 RSS 在主库写锁占用时仍完成 raw 和后续导入，进程中断及重复投递对照通过。独立审查发现的 A4 告警入口遗漏已修复并复核，详细边界见 [采集解耦 ADR](../adr/20260916-e3a8-decouple-collection-from-processing.md)。本机启用及真实读数见末节；以下保留其他环境的启用前置条件。

新增 `collector.sh`（也可 `pipeline.sh --collect-only`）独立抓取入口，使用 `.collector.flock`；业务处理仍使用 `.pipeline.flock`。只有配置 `AI_RADAR_DECOUPLED_INGESTION=1` 后才切换：collector 执行原 egress/微信浏览器预检和 `collect`，不执行 AI；pipeline 执行 `ingest` 再跑原过滤、评分、enrich、精选和解读。直接 `./run.sh fetch` 在该模式拒绝运行，避免两个游标写者。

切换须获部署许可，在采集侧新增配置 `AI_RADAR_INGESTION_DB=<独立采集库路径>`，继续使用既有 `AI_RADAR_RAW_CAPTURE_DIR`。先在主 pipeline 和 collector 都没有写者时运行 `./run.sh ingestion-init`；命令自身获取两把对应写锁，主库繁忙时失败，不杀旧任务。它迁移必要表、绑定主库身份，并只读复制匹配来源的抓取游标和微信正文/头像缓存到新库；不导入 T5 或模型输出。之后启用开关并安装 `deploy/cron/ai-radar-collector` 的每 15 分钟入口，保留原处理调度；不得在别台已复制业务库的展示服务器上初始化第二个采集写者。

单源新闻与游标在采集库同事务写入压缩 outbox，即便整轮中断，已取得输入仍能交给主库。`ingest` 固定本轮待处理上界、顺序导入；主库新闻、该批 runtime 与确认记录同事务，提交后删除对应 outbox。中断后重试不会用旧批覆盖较新的主库新闻。原始评测资产仍走上文 `radar_raw_v1` 完整性规则，业务投递成功不代表中断 raw 轮可做完整评测。

已确认副本清理若遇 collector 的 SQLite 写锁，本轮延后清理并继续后续导入，下轮按 ack 处理，不让清理争锁中止 AI 处理。仅捕获该处 SQLITE_BUSY，其他错误仍失败；详见 [d362](../adr/20260916-d362-defer-busy-outbox-cleanup.md)。`pending_batches` 是尚未清理的队列记录数，可能含已导入并确认的批次，不等于未导入新闻数。

collector 的原始抓取日志在 `logs/collector/pipeline-*.log`；处理日志仍在 `logs/pipeline-*.log`，其中 `ingest applied_batches=... already_applied_batches=... pending_batches=...` 是投递结果，不是抓取成功率。A4 继续使用真实 fetch 完成轮（包含 collector 日志），A2/主处理心跳不拿 collector 成功代替。源异常、预检与原始留档失败仍通过现有 W1/A4 链观测；投递错误使处理轮失败，由原 A2 心跳链观测，不新增告警阈值或另一套状态机。W1 在全处理轮成功之外，还要求该 generation 实际消费了完整成功的采集轮，且其完成时间不早于当前 W1 故障；空队列不能关闭 W1。

outbox 自包含、不依赖即将滚动清理的 raw 文件，因此未投递新闻不受 raw 30 天清理影响。代价是采集库新增正文缓存和确认前 payload：payload 确认后释放可复用页，SQLite 文件不会自动缩小；正文缓存及确认账本仍占用磁盘。初始化/采集库错误、哈希损坏或来源配置身份变化均失败并保留批次，不能删队列来“解锁”。来源发生变更时先核对待投递批次与当前 source 配置，再决定兼容导入，不静默套用旧游标。

回退不能只关开关：先停独立采集的后续调度，待现有 collector 完成，保持解耦处理直到 pending 为零且主库已取得最后游标，再关闭开关恢复旧 fetch。若队列有错误未排空，停止切换并保留数据。回退同样需要相应部署许可，本次没有回退或清空队列。

### 原始归档与 AIHOT

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

AIHOT 冻结：`scripts/capture_aihot_dataset.py freeze --output-root "$AIHOT_CAPTURE_WORKTREE/benchmarks/aihot" --window windows/START--END/manifest.json --destination data/frozen-aihot/SET_ID`，可重复 `--window`；`AIHOT_CAPTURE_WORKTREE` 必须显式配置为与当前 cron 相同的运行树，不能默认取主树 submodule。每个窗口和其完整 capture 都复制并重新验证。冻结位置不能在 rolling captures/windows 内，现有目的地拒绝覆盖。冻结副本仍需备份；本地复制不等于已远端持久化。

## 保留、告警与启用边界

本节关于 AIHOT 频次不变、`run-or-alert` 包裹及“不额外建立常驻监控”的描述是 2026-09-15 启用时的历史快照；调度与监控现况已由本页「2026-09-17 持续采集恢复修复（已安装调度）」替代。原有保留与冻结规则继续适用。

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

## 2026-09-16 独立采集启用与并发验证

用户另行批准「启用并验证」。macmini / lindong 于 09:19:52 +08:00 完成切换：等待旧 pipeline 自然结束后持主锁初始化 `data/ingestion.db`，设置 `AI_RADAR_INGESTION_DB=<项目根>/data/ingestion.db` 与 `AI_RADAR_DECOUPLED_INGESTION=1`，保留 raw 路径。用户 crontab 新增每 15 分钟 `collector.sh`；原 pipeline、AIHOT 09:37 和 raw 清理 03:07 调度保留，安装前后整表回读一致。没有终止旧任务，没有推送应用代码。

两轮手动真实 collector 均退出 0：`20260916T012035.396466Z-da8d698d` 为 161 来源 success、4,253 条过滤前输入；`20260916T012306.044143Z-97719c19` 为 161 来源 success、4,250 条。两者均经 `read_run` 内容哈希、行数和依赖闭包校验，均无 304 依赖；跨轮有重复，不能相加当独立新闻数。第二轮启动时实际主写锁被导入占用，采集仍完成。第一轮包含 222 条超过 4,000 字符的正文，gzip 共 3,279,009 字节。

自动调度也取得直接读数：`logs/pipeline-20260916-093000.log` 于 09:30:02 报主锁忙而 SKIP；独立的 `logs/collector/pipeline-20260916-093000.log` 于 09:30:04 开始 fetch，09:33:04 fetch OK、PIPELINE DONE failed=0。这证明处理锁不再挡住采集入口，不等于已取得连续七天。

真实导入暴露了确认后的队列清理争锁：主库成功提交 ack，`_discard` 的 DELETE 遇 SQLITE_BUSY 使旧版退出；重试曾推进至 190 个 ack（含首轮完整确认），没有删除未确认数据。修正及失败证据见 [d362](../adr/20260916-d362-defer-busy-outbox-cleanup.md)。定向隔离测试 24 项通过，覆盖队列写锁持有/释放、确认后重试及调度互斥。

启用代码基线为本地 main `31b3362`（已含 `67ad563` 独立采集）；原有 WIP 原样保留，raw 记录 `code_dirty=true`，不是 clean 评测基线。初始化侧库约 496 MiB，两轮后约 672 MiB；只属启动期读数，不外推 30 天增长。初始化发现一条既有微信 `about:blank` 无法跨源识别，未改删原数据。现场配置备份与 crontab 前后副本在 ignored `logs/ingestion-*20260916*`，不提交凭据或原始数据。

修正 `08353d4` 已快进整合本地 main。提交导出树的 13 项导入测试通过；把导出副本的 SQLITE_BUSY 分支改回抛出后，真实写锁回归为红，恢复后变绿。覆盖采集/投递两个角色、主库锁/队列锁、持有/释放、源批/整轮、首次/重复、中断和三类无效投递，不代表长期吞吐验证。

新版真实 `./run.sh ingest` 退出 0，generation 为 `activation-busyfix-20260916`：`applied_batches=296 already_applied_batches=1 pending_batches=0`。09:45:00 +08:00 只读核对主库有连续批号 1—486 共 486 个 ack、3 个完整轮确认，分别对应上述 09:20、09:23、09:30 采集；队列为 0，主/侧库身份绑定一致。第三轮 raw `20260916T013031.585205Z-9eeb9659` 为 161 来源 success、4,251 条输入，内容/行数/依赖校验通过。抽查三个不同来源正文（62,946、118,286、143,045 字符），主库与首轮 raw 逐字一致。这些只覆盖本次交接，不声称既有正文由本轮首次写入，也不把主库当不可变 raw。

此时持续运行机制为 macOS cron daemon 下 lindong 的用户 crontab，不依赖本 session 子进程；不是另起一个 agent 后台任务。原始输入位于主树 `data/raw-capture/runs/`，投递队列位于 `data/ingestion.db`。本次没有额外手动启动评分/精选调用，AIHOT 调度未在本次改动；完整评测窗口仍须未来按两侧交集审计、来源核对并冻结，不能承诺等七天就自动得到七天合格集。

## 2026-09-17 持续采集恢复修复（已安装调度）

用户要求修复后续持续采集，不回填历史缺口。Radar 留档继续保存完整 prefilter 输入，不以 prefilter、评分、AIHOT 是否出现或精选结果预筛。评测只取共同来源；`wx_wechat2rss` 属微信专用来源，不计入共同来源健康检查，但不因此停止网站采集或删除其数据。

| 本地入口 | 行为与启用边界 |
|---|---|
| `collector.sh` → `scripts/collection_supervisor.py` | 总预算 14 分钟，最多尝试 3 次，间隔 15 秒；网络仍并发，trafilatura 提取互斥，既有 pipeline/outbox 双锁不变 |
| `scripts/collect_aihot_supervised.sh` | 已安装每小时第 7 分钟调度；独立 job 锁、40 分钟预算；从已初始化的 `capture_start` 起保留欠交付日，有通过校验的窗口不重抓，继续未完成发布 |
| `scripts/check_collection_health.sh` | 已安装每 5 分钟调度；Radar 超过 20 分钟无完成采集或共同来源失败、AIHOT 完整 UTC 日到期 2 小时未交付时告警，复用 im-notify 状态去重/恢复 |

后两个脚本要求显式 `AIHOT_CAPTURE_WORKTREE` 指向获准的干净运行树；supervisor 状态位于 `data/collection-state`，日志在 `logs/collection-supervisor/aihot.log` 与 `health.log`。新状态通过 `scripts/collection_supervisor.py --state-dir <状态目录> init --capture-start <启用日T00:00:00+00:00>` 初始化；启用日由生产切换确定，不借初始化回填旧缺口。已有状态拒绝重新初始化，以免忘记欠交付日。本机已获批准并初始化，实际起点与调度见下文；其他环境的生产 cron/env 修改仍须相应审批。

新格式为 `capture_v2` / `window_v3` / `report_v3`，RSS/OpenAPI 补充探针失败保留诊断，不阻断可用 API/SSR。校验和 freeze 支持新格式；旧 `slice` 明确拒绝 `capture_v2`，本次不扩展它。旧格式语义保持不变；已交付窗口合法达到 30 天保留期不作假缺口，每次健康检查不逐窗扫描全树 hash。

实现审查首轮两条 HIGH（同轮重试前重查已有窗口、补充 OpenAPI Date 倒序只记诊断）已修复，独立 reviewer 两问复核放行，无新增 findings。最终本地定向结果为 7 个测试文件 537 passed、1 deselected（54.38 秒），shell 78 项断言通过；覆盖有限重试成功/耗尽/超时子进程组终止、HTTP 暂时与永久错误、补充探针 404/异常日期降级及 API/SSR 失败严格拒绝、8 worker 提取互斥与 7 类真实文本输出等价。ruff 指出的单项 `Callable` import 已机械移至 `collections.abc`。唯一排除项在基线 `371d21a` 复现，错误码期望 `output_root_invalid`、实际 `git_checkout_invalid`，去向见 [testing.md](../issues/testing.md)。这些是本地验证，不能外推为真实 cron、新格式远端发布或通知已启用及验收，也不证明未来永不中断。

主线程 09-17 只读取数：`claude_youtube` 于 03:15Z 返回 200/15 条，03:30Z 与 03:45Z 返回 404。这是上游间歇失败，不删除来源；重试是否恢复及连续完整窗口仍须据后续 raw 实测。共享 parser 的 native 崩溃归因仍为假设，没有确定性 native 复现。

主线程独立统计 09-17 截至 06:01Z 开始的 25 个完成轮：`claude_youtube` HTTP 200 × 10、404 × 11、500 × 4，该时点最新成功为 03:15Z 的 15 条。独立只读诊断中，无条件直接 GET RSS 仍返回 404，同一 channel 页面返回 200 且包含 Claude 标题（读取于 15 秒截断）；未取得经验证的同身份替代 RSS。该来源仍间歇失败（后续 06:15 轮成功、随后两轮再失败，见末节）：保留 source ID 和来源，每 15 分钟新轮继续尝试；5xx、429、transport 故障执行单请求重试，404 不即时重试。不把来源失败当作完整数据，也不声称全源无缺口。

数据发布曾被 Git 安全扫描误报阻塞。用户授权后，harness 精确修复 `4c9be51f` 已合入其 main，新 runtime 的 pre-commit 使用该 canonical hook。实际旧 capture 暂存数据验证为 `rawFindingCount=52`、`publicCursorCount=52`、`effectiveFindingCount=0`、`indexUnchanged=true`：52 项均为公开分页 `canonical_query.cursor`（base64 JSON 字段键 `a,c,i,k,v`），未改 raw、未使用 `--no-verify`、未关闭 scanner。worker 测试 19/19、main cursor 测试 7/7 是局部修复读数，不能替代新日窗发布验收。决策在 harness 仓 `docs/adr/20260917-2f6a-grafana-public-cursor-classification.md`。`captures/daily` 数据 push 只沿用 [c7d4](../adr/20260913-c7d4-let-the-daily-capture-push-its-own-data.md) 授权，应用 push 未获授权。

### 本机启用与首轮实际读数

用户明确「批准启用」后，本地 main 快进到 `6eeb9dc`。AIHOT 自有运行树为 `/Users/lindong/research/ai-radar-worktrees/continuous-capture-runtime-20260917`，源码 `6eeb9dc`、独立 parent pin `ee9ac71`、数据来自远端 `97f5ed0`。macmini/lindong 实际 crontab 已更新为 AIHOT `7 * * * *`、health `*/5`，Radar 保留 `*/15`；其他 cron 未变。前后备份在 `.local/capture-reliability-20260917/crontab.before` 与 `crontab.after`。原 main 的 precompute WIP 哈希前后一致。

`data/collection-state/aihot.json` 已初始化为 `capture_start=2026-09-17T00:00:00+00:00`、`delivered_days=[]`。手动真实入口从 06:10:46Z 到 06:10:56Z，exit 0；只覆盖无欠账及发布核验路径，未请求新 raw，不作为新日窗通过。首个激活 UTC 日 09-17 在本地 09-18 08:00 结束，08:07 小时任务可采集，10:00 起仍未交付则进入晚到告警条件；须由实际到期运行取得新窗口及发布读数。

最新采集资产的实际读取入口是上述 runtime 的 `benchmarks/aihot`，远端发布权威是 `origin/captures/daily`，远端状态须实查 exact ref。主线程只读核对 `/Users/lindong/research/ai-radar/benchmarks/aihot` 仍为 `76f62cf`；主树 gitlink 是代码 pin，不是实时数据指针，不会随新运行树自动更新。后续评测、校验与冻结须使用实际采集根并核对发布状态；本次不移动他人主树数据 submodule HEAD，也不建立自动覆盖 main 指针的机制。

06:10:30Z 真实健康检查对 `claude_youtube` 告警，im-notify 返回 `alert sent via feishu`。06:11:43Z 使用独立演练 key 验证故障发送、重复不改变 `sent_at`、恢复发送；这证明该次发送端与去重/恢复路径，不代表用户手机已收到，手机收件未确认。

### 真实自动周期、有限重试与健康状态变化

06:15 周期来自 macOS cron 的实际进程链 `PID 352 → 66144 → sh → venv python supervisor`，`radar.json` 于 06:15:02.643Z 启动，源码为 `6eeb9dc`。同周期三次尝试均保存 raw，后一次不覆盖前一次：

| 尝试 | raw 与完成读数 | 来源边界 |
|---|---|---|
| 1 | `20260917T061551.881479Z-059ce4b0`，06:15:51.882Z—06:19:06.312Z，4,199 条 / 161 源；主线程 `read_run` 哈希与行数验证通过 | 仅 `wx_wechat2rss`（WechatOnly）失败，共同评测来源该轮全成功；collect 仍 exit 1，不能称整轮成功 |
| 2 | 06:20:00Z 开始的 raw，4,183 条 | `claude_youtube` 与 `wx_wechat2rss` 失败 |
| 3 | `20260917T062451.632059Z-af4203ec`，06:28:08.593133Z 完成，4,183 条；主线程 `read_run` 完整性验证通过 | 同上两源失败；文件完整性不等于所有来源成功 |

supervisor 于 06:28:09Z 耗尽三次尝试、退出 1；下一自动周期于 06:30:00.844320Z 启动并进入 fetch，证明此次锁已释放、跨轮仍可继续。没有把第三轮失败吞成成功，也没有因重试丢掉第一份 raw。health 在 06:20:01Z 依据当时共同来源恢复更新 `incident=false` 并自动发送恢复通知，06:25 再次发现来源失败并真实发送告警；手机收件仍未确认。

YouTube RSS 间歇 404/500 仍是实际外部故障，继续按既定频次及错误分类重试、告警，不改 source ID、不删除来源、不把失败当完整。以上覆盖一个自动周期的三次尝试、下一周期启动及健康状态变化，不是全源正常或连续多日验收。真实 AIHOT 新格式完整日发布与长期连续性仍待到期运行；没有历史回填、网站数据删除或应用 push。决定及取舍见 [b8e2](../adr/20260917-b8e2-recover-continuous-raw-capture.md)。

### 09-17 19:11 +08:00 只读复查与清理竞态修复

固定快照为 19:11:35。Radar 共 198 个 manifest，195 个完成、3 个 started；完成归档共 819,476 条抓取记录、10,815 个不同 exact URL。首轮开始于 09-15 22:49:49，最新完成于 09-17 19:08:41，跨度约 44 小时 19 分钟，不代表全程连续。195 个完成归档均以 read_run 回读验证哈希、行数及结构；无 payload_ref 依赖。

14:15 新调度起的完成归档为 58 轮、242,501 条记录、4,630 个不同 URL。14:15—19:00 共 19 个已关闭的十五分钟时段都有尝试，但 14:30、14:45 的 claude_youtube 均未成功，构成该来源 30 分钟缺口；15:00—19:00 的 16 个时段各至少有一次 160 个启用、未暂停、非微信专用来源均成功的归档。该四小时段共 47 个完成归档、196,433 条记录、4,492 个不同 URL；不以评分或 AIHOT 收录结果预筛。160 来源的健康覆盖不等于本次逐来源重新确认了 AIHOT 映射。

AIHOT 实际运行树仍有 12 个历史日窗、3,810 条参照记录，远端 exact captures/daily 为 97f5ed0cb027a16d46fe9bedbeba49c55ea7b69e。新启用日尚未结束，delivered_days 为空，19:07 最近一次监督 exit 0；新启用后可冻结的双侧完整日窗仍为零，不表示 Radar 没有继续积累。首日到期时间及采集安排沿用上一节。

另发现 16:07、17:07 的 AIHOT 子脚本退出成功后，监督进程在 SIGKILL 清理时抛 PermissionError；18:07、19:07 后续调度正常结束。旧 health 未检查这类监督异常。修复见 [8b86](../adr/20260917-8b86-wait-for-collector-process-groups.md)：有界等待全组消失，只有 ESRCH 才消解 EPERM；内部异常终态记为 70；last_exit 保留上次已完成结果，重试启动不冒充恢复；未收尾超过既有预算加五分钟也报异常。复用原通知 key，不另建通知通道。

本节修复先在隔离分支验证，未改正在运行的 cron。实际 cron 从主树读取 supervisor，合入主树会让后续周期自动使用新代码，须获本次启用许可。历史缺口依用户要求不回填；上游失败继续按现有重试与告警处理，不删除来源掩盖缺口。

本地定向测试 30 项通过（主线程 4.40 秒，独立 reviewer 4.49 秒），覆盖 macOS 单平台、正常/忽略 TERM 两种真实后代、TERM/KILL 两种权限竞态、两种 collector、持续权限拒绝、handler 恢复、失败→重试→完成及超预算未收尾。旧代码上的新增定向集为 12 failed / 2 passed，能检出相关缺陷；ruff 与 diff 检查通过。单轮独立实现审查放行，无遗留 findings。本轮用新脚本只读检查实际归档和状态，exit 0；未带 --notify、未改状态，也不等于已启用修复。

### 同日批准启用及 19:59 最新快照

用户随后回复「批准启」。53a638c 已快进合入本机 main，cron 下次执行直接读取新版；频次、运行树及原始数据范围未改。precompute 原有 WIP 文件及 diff 哈希前后相同，没有应用 push。提交导出树的反向变异使两项权限竞态测试变红，恢复原字节后 30 项通过（4.42 秒）。真实 AIHOT 入口于 19:58:30—19:58:39 +08:00 完整结束，last_exit=0；实际健康脚本也 exit 0，没有新故障转换，因此没有新建 AIHOT 通知状态文件。原 Radar 通知状态仍为 incident=false。此时仍无到期新日窗，不能将无欠账的执行成功计为新日窗发布验收。

19:59:56 +08:00 再次固定快照：207 个 manifest 中 205 个完成、2 个 started；205 个完成归档均经 read_run 回读验证，无校验错误。累计 861,513 条抓取记录、10,831 个不同 URL；最新完成 19:57:53，与首轮 09-15 22:49:49 相距约 45 小时 8 分钟。启用后 68 个完成归档、284,538 条记录、4,646 个不同 URL。

只计算已经结束的十五分钟时段，14:15—19:45 共 22 段均有归档尝试；仍只有 14:30、14:45 两段缺少 claude_youtube 的成功归档。15:00—19:45 连续 4 小时 45 分钟（19 段）各有一次 160 个非微信专用启用来源均成功的轮次；该时间段内 56 个完成归档共 234,259 条记录、4,511 个不同 URL。19:45—20:00 在快照时尚未结束，不提前计为完整段。AIHOT 新启用后的完整日窗仍为零；这些 Radar 数量不是已冻结的双侧评测题数，也不按 prefilter/评分/AIHOT 成员筛减。
