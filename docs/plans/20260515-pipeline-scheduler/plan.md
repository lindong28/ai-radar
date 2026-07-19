> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

# Plan: Pipeline Scheduler — 自动化增量数据抓取流水线

## Before（当前状态）

- 每个流水线阶段（fetch / prefilter / score / enrich / curate）均通过 `./run.sh <stage>` 手动执行
- `--since 24h` 默认值 + `item_evaluations` 表的 `NOT EXISTS` 检查确保每个阶段只处理新增数据
- Web 服务通过 launchd 常驻运行（port 8000 + cloudflared tunnel），但数据不会自动更新
- 数据库最后更新时间为 2026-05-14（约一天前）

## Change（要做什么）

创建一个 **orchestrator shell 脚本** `pipeline.sh`，由 cron 每 15 分钟触发，按序执行 fetch → prefilter → score → enrich → curate。任何阶段失败时记录错误但继续执行后续阶段。下一次调度时，已完成的阶段通过 `NOT EXISTS` 检查自动跳过已处理条目，只处理增量部分。

### 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `pipeline.sh` | orchestrator 脚本，按序执行 5 阶段 + 错误处理 + 日志 |
| 新建 | `deploy/cron/ai-radar-pipeline` | crontab 条目文件（可被 `crontab` 加载） |
| 修改 | `README.md` | 新增「自动化调度」段落 |

## Design

### 1. `pipeline.sh` — orchestrator 脚本

```
#!/usr/bin/env bash
# ai-radar pipeline orchestrator
# Runs all stages sequentially, continues on failure.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline-$(date +%Y%m%d-%H%M%S).log"

log() { echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

FAILED=0

run_stage() {
  local stage=$1; shift
  log "=== $stage START ==="
  if ./run.sh "$stage" "$@" >> "$LOG_FILE" 2>&1; then
    log "=== $stage OK ==="
  else
    log "=== $stage FAIL (exit $?) ==="
    FAILED=1
  fi
}

run_stage fetch
run_stage prefilter --since 24h
run_stage score --since 24h
run_stage enrich --since 24h
run_stage curate

# Cleanup logs older than 7 days
find "$LOG_DIR" -name 'pipeline-*.log' -mtime +7 -delete

log "=== PIPELINE DONE (failed=$FAILED) ==="
exit $FAILED
```

关键设计决策：

- **`set -uo pipefail`**（不含 `set -e`）— 允许单阶段失败后继续
- **`run_stage` 函数**封装统一的错误捕获和日志记录
- **`--since 24h`** 只对 prefilter/score/enrich 生效（fetch 不接受 `--since`，它天然增量）
- **日志轮转** — 自动清理 7 天前的日志
- **exit code** — 任一阶段失败则整体返回非零，便于 cron 报告

### 2. crontab 配置

```
*/15 * * * * /path/to/ai-radar/pipeline.sh >/dev/null 2>&1
```

存为 `deploy/cron/ai-radar-pipeline`，用户可通过 `crontab deploy/cron/ai-radar-pipeline` 加载。

### 3. README 更新

在「快速开始」后新增「自动化调度」段落，说明如何配置 cron。

## 成本分析

用户要求「执行成本和新增数据成正比」。当前架构已满足：

| 阶段 | 增量机制 | 历史数据是否导致额外成本 |
|------|---------|------------------------|
| fetch | content_hash 去重 | 否 — RSS feed 返回新条目 |
| prefilter | `NOT EXISTS (item_evaluations WHERE stage='prefilter')` | 否 — 跳过已评估条目 |
| score | `NOT EXISTS (item_evaluations WHERE stage='scoring')` | 否 — 跳过已评分条目 |
| enrich | `NOT EXISTS (item_evaluations WHERE stage='enrich')` | 否 — 跳过已丰富化条目 |
| curate | 从已有评分重新筛选 | 否 — 不调 LLM，仅 SQL 查询 |

LLM API 调用仅发生在 prefilter / score / enrich 三个阶段，且仅对新增条目触发。cron 的 15 分钟调度本身无 LLM 成本。

## User-facing Verify (L2)

| # | 验证步骤 | 预期结果 | 人机 |
|---|---------|---------|------|
| V1 | `./pipeline.sh` 手动执行一次 | 日志显示 5 个阶段依次完成，exit code 为 0 | agent |
| V2 | `crontab -l` 确认 cron 条目已安装 | 显示 `*/15 * * * * ...pipeline.sh` | agent |
| V3 | 等待一个调度周期后查询 DB：`sqlite3 data/radar.db "SELECT COUNT(*), MAX(fetched_at) FROM items WHERE fetched_at > datetime('now', '-1 hour')"` | 有新增条目，fetched_at 为近期时间 | agent |
| V4 | 模拟阶段失败：设置无效 API key 后运行 `./pipeline.sh`，检查日志 | 失败阶段标记 FAIL，后续阶段仍执行并标记 OK/FAIL | agent |
| V5 | 检查日志轮转：`ls logs/pipeline-*.log` | 日志文件存在且命名格式正确 | agent |
| V6 | 访问 `http://localhost:8000` 确认网站展示新数据 | 页面包含 pipeline 执行后新增的条目 | agent |

## Internal Verify (L3)

| # | 检查项 | 方式 |
|---|-------|------|
| L1 | `pipeline.sh` 语法正确 | `bash -n pipeline.sh` 无输出 |
| L2 | `pipeline.sh` 可执行权限 | `test -x pipeline.sh && echo OK` |
| L3 | crontab 条目语法 | `crontab -l` 不报错 |
| L4 | 日志目录可创建 | 脚本首次运行后 `test -d logs` |
| L5 | 单阶段失败不终止 pipeline | V4 验证 |

## Defaulted Decisions

| 决策 | Default | 理由 |
|------|---------|------|
| 日志保留天数 | 7 天 | 平衡磁盘使用和调试需求 |
| cron 输出重定向 | `>/dev/null 2>&1` | 已有文件日志，cron email 是冗余的 |
| `--since` 值 | 24h | 与现有 CLI 默认值一致，覆盖跨调度窗口遗漏 |

## Risks

| 风险 | 接受理由 | 触发响应 |
|------|---------|---------|
| cron 调度重叠（单次 pipeline 超过 15 分钟） | 15 分钟内通常只处理少量新 RSS 条目 | 无需额外处理 — DB 去重保证幂等性 |
| LLM API 全局性故障导致所有 LLM 阶段连续失败 | `run_stage` 继续执行，curate 仍可用已有评分重新精选 | 日志中 FAIL 标记可被监控系统捕获 |

## 引用索引

| 路径 | 用途 |
|------|------|
| `run.sh` | 现有 CLI 入口，pipeline.sh 通过它调用各阶段 |
| `src/airadar/cli.py` | CLI 命令定义和 `--since` 参数处理 |
| `src/airadar/prefilter/runner.py` | prefilter 增量查询逻辑（`NOT EXISTS` 检查） |
| `src/airadar/scorer/runner.py` | scorer 增量查询逻辑 |
| `src/airadar/enrich/runner.py` | enrich 增量查询逻辑 |
| `data/radar.db` | SQLite 数据库，验证数据更新 |
