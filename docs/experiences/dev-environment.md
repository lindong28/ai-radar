# Dev Environment 经验

> Append-only. 开发环境相关的坑点和 pattern.

## 2026-05-24 使用 `uv run python` 代替裸 `python` 命令

- Problem: 当前开发环境中 bare `python` 命令不存在（仅有 `python3`），直接运行 `python -m pytest` 会失败。
- Solution: 使用 `uv run python -m pytest tests/ -x` 代替，uv 会自动定位 virtualenv 中的 Python 解释器。
- Applies when: 在此项目中运行 pytest 或其他 Python 模块时——始终通过 `uv run` 调用以避免 Python 版本和路径问题。

## 2026-05-15 macOS crontab 写入被 TCC 阻塞

- Problem: `crontab -l` 可正常读取，但所有写入方式（`crontab <file>`、`crontab -` stdin、甚至写回未修改的当前 crontab）都会 hang/超时。系统日志显示 `com.apple.crontab` 触发 TCC authorization request，responsible app 为当前终端（如 Ghostty）。
- Solution: 在 macOS System Settings > Privacy & Security > Full Disk Access 中授予终端应用（Ghostty / iTerm2 / Terminal）权限，然后重试。授权后 merged crontab install 立即成功。
- Applies when: 在 macOS 上首次通过某个终端应用安装 crontab 时——如果 `crontab` 写入 hang，检查终端的 TCC/Full Disk Access 权限。
