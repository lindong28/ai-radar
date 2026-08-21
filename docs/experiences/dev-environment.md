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

## 2026-08-20 按环境变量名硬编码的特例让两个测试给出矛盾期望

- Problem: 微信 feed loader 曾按环境变量**名字**（`MP2RSS_FEED_URL`）硬编码「占位符未设置则跳过」的特例，于是两个既有测试对同一情形（v1 配置、占位符未设置）给出互相矛盾的期望——一个要报错、一个要跳过，差别只在变量叫什么。双跑接入第二个变量时矛盾暴露。
- Solution: 把「可缺省」显式建模为来源属性（`optional = true` 才静默跳过，其余报错中断加载），删除按名字分支。见 ADR-059 与 CHANGELOG 2026-08-20。
- Applies when: 想给某个具体实例开特例时——按「名字」分支的行为无法被通用测试一致覆盖，矛盾期望是这类硬编码的信号。
