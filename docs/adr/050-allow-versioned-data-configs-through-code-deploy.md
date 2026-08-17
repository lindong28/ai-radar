# ADR-050：代码部署仅放行已核验的版本化 data 配置

- Status: accepted
- Date: 2026-08-17

## Context

生产 post-receive 部署在 materialize commit 前拒绝所有 runtime-owned 路径，避免 `checkout-index -f` 覆盖服务器本地状态。`data/sources.toml` 已作为精确例外；提交 `3fb2adb` 新增的两个版本化输入 `data/aihot_retirements.json` 与 `data/wechat-discovery.toml` 仍被默认 `data/**` 规则拦截，部署在触碰 live tree 前安全失败，旧版本继续提供服务。

当前生产目标的 live tree 已核验这两个路径均不存在，因此首次把它们作为 tracked config materialize 不会覆盖同名服务器本地状态。这个证据只覆盖当前 production post-receive target，不覆盖其他主机或部署目标。

## Decision

在代码部署守卫的精确 allowlist 中加入 `data/aihot_retirements.json` 与 `data/wechat-discovery.toml`，与既有 `data/sources.toml` 一起作为版本化配置部署。三个例外都必须是 mode `100644` 的普通 Git blob；symlink、可执行文件或其他 object type 继续被拒绝。所有其他 `data/**`、`.env`、`.venv/**` 与 `logs/**` 继续被拒绝。

部署守卫在首次把任一例外路径加入 live tree 时重新检查实际目标：若 base commit 未跟踪该路径而 live tree 已存在同名文件或 symlink，则当场拒绝，不依赖较早的人工核验。该分类只对已经核验上述两个目标路径均不存在的当前生产部署目标成立；其他 host 或 target 即使通过相同的部署时检查，也不能从本 ADR 推出其本地内容可被覆盖。

## Alternatives

- 把两个文件移出 `data/`：会同时迁移现有默认路径、文档和消费者，扩大当前生产恢复的影响面。
- 从 Git 删除两个文件并恢复为 runtime-owned：会撤销已经建立的版本化默认配置与来源退休契约，破坏 clean checkout 的自包含性。
- 手工绕过部署守卫：会跳过部署事务的 fail-closed 边界，且不能形成后续 commit 可重复使用的修复。
- 放行全部 `data/**`：会让数据库、锁、部署 journal 等 live state 面临被 Git 覆盖的风险。

## Consequences

当前生产目标可部署这三个精确的版本化 data 配置，同时保留其余 runtime state 的拒绝行为。守卫测试必须同时证明普通配置文件可经真实 Git materialize、同名 pre-existing untracked 路径与 symlink 被拒绝，以及任意其他 `data/**`、`.env`、`.venv/**`、`logs/**` 仍被拒绝。若部署后发现分类不符合预期，可移除新增 allowlist 项，使守卫再次 fail closed；这不会自动恢复被覆盖的同名文件。

当前 post-receive 固定执行 live tree 中的部署器，因此仍运行旧 allowlist 的生产目标无法通过普通 push 自举本次守卫修复。该目标允许一次性 bootstrap：目标 commit 已进入 bare repo 的 `main` 后，从 exact pushed SHA 提取 `deploy/sync/deploy_code.py` 到临时文件，核对其 SHA-256 与同一 commit blob，并用 system Python 对同一 SHA 执行完整 candidate、schema、materialize、restart、health、journal 与 rollback 事务，成功后删除临时文件。执行前还必须确认相对当前 live 部署器的控制面差异只包含本 ADR 的 guard/allowlist、首次路径碰撞与 Git object type 检查；不得借 bootstrap 修改 journal/reconcile、环境 effect sinks、restart/health 顺序或 release commit 语义。永久改成执行任意 incoming commit 的部署器、手改 live tree 部署器、临时关闭守卫和拆分 `main` 中间态均不采用。

## Scope and unverified items

本决策不改变配置内容、数据库 schema、服务端 secret、部署槽位或 health-check 行为，也不授权 Git push。记录决策时，重新部署、生产数据库同步与公开入口验收尚未执行；这些结果必须由后续真实运行取得，不能从本 ADR 推出。一次性 bootstrap 只覆盖当前 live tree 仍为 `aeeccfdf69f81051ac6131d9e60c8d427d58edbf`、remote `main` 已前进而旧 allowlist 阻塞部署的生产目标，不成为未来常规发布入口。
