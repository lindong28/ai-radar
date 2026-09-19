# ADR-003: Runtime env loader 读取双层 .env 文件

- Status: accepted
- Date: 2026-05-15

## Context

Pipeline 自动调度（cron/launchd）运行时不继承交互式 shell 的环境变量。LLM API key 等敏感配置原先依赖用户在 shell 中 `export`，导致非交互调度首次运行时因缺少 key 而失败。

需要一个不依赖交互式 shell 的环境变量加载机制，同时支持多台机器/多个项目共享通用 key。

## Options Considered

### Option A: 仅项目根目录 `.env`
- Pros: 简单，单一来源
- Cons: 多个项目共享同一个 API key 时需要在每个项目各维护一份

### Option B: 仅共享 `~/.claude/.env`
- Pros: 跨项目共享
- Cons: 项目级配置无法覆盖共享值；与常见的项目根 `.env` 惯例不符

### Option C: 双层加载（共享 + 项目）
- Pros: 兼顾共享和项目级覆盖；优先级链清晰
- Cons: 两个文件增加少量认知负担

## Decision

选择 Option C。加载顺序和优先级：

1. `~/.claude/.env` — 共享 key（最低优先级）
2. 项目根目录 `.env` — 项目级覆盖
3. 已有进程环境变量 — 最高优先级（`override=False` 行为不变）

## Consequences

- 非交互调度只需确保至少一个 `.env` 文件包含所需 key，无需额外的 `launchctl setenv` 或 shell profile 配置
- 项目 `.env` 可以覆盖共享值，支持针对特定项目使用不同 key/模型
- 维护者需要知道有两层 `.env`，但这比要求每个调度方式都配置环境变量更可预测

## 修订（2026-09-19，[20260919-b7e2](20260919-b7e2-second-security-round-deploy-chain-credentials-csp.md)）

加载顺序与优先级不变，但**不再把两层文件里的全部键都写进 `os.environ`**：只加载 `AI_RADAR_*` 前缀的键与 `runtime_env.RUNTIME_ENV_KEYS` 列出的非前缀键（与 `.env.example` 声明一致，有测试守着）。`~/.claude/.env` 是跨项目共享文件，实测本机有 126 个键、其中绝大多数与本项目无关；原语义让任何一处 RCE 或子进程 env 泄露都等于整串密钥外泄。`read_value(key)` 仍可读任意键，因为调用方显式点名。
