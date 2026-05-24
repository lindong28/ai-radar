# Deployment 经验

> Append-only. 部署和调度相关的坑点和 pattern.

## 2026-05-15 非交互调度不会继承 shell 环境变量

- Problem: cron/launchd 触发的 pipeline 不继承当前 shell session 中 `export` 的 API key。首次 launchd RunAtLoad 因缺少 `DEEPSEEK_API_KEY` 导致 enrich 阶段逐条报错。临时用 `launchctl setenv` 注入后才通过。
- Solution: 使用项目根目录 `.env` 或 `~/.claude/.env` 存放 API key，由 runtime env loader 在启动时加载（见 ADR-003）。不要依赖交互式 shell 的 `export`。
- Applies when: 配置任何非交互调度（cron、launchd、systemd）时——部署前确认 `.env` 文件包含所需 key，不要假设环境变量已存在。

## 2026-05-15 cron 与 launchd 不要同时启用

- Problem: 为绕过 macOS crontab TCC 阻塞，临时安装了 launchd fallback。之后 cron 恢复后如果不移除 launchd，会导致 pipeline 被双重触发（每 15 分钟执行两次）。
- Solution: 确保同一时间只启用一种调度方式。切换时先 bootout 旧的再安装新的。当前生产配置使用 cron。
- Applies when: 在调度方式之间切换时——检查是否有残留的 plist 或 crontab 条目。
