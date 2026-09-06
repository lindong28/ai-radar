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

## 突变检验：等长替换会被陈旧 `.pyc` 悄悄吃掉

用突变检验判「这条测试有没有区分力」时，**改动前先清 `__pycache__`**：

```sh
T=$(mktemp -d); cp -R src tests "$T"/
find "$T" -name __pycache__ -type d -exec rm -rf {} +
# ...在 $T 里施加突变...
PYTHONPATH="$T/src" uv run pytest "$T/tests/<file>" -q -p no:cacheprovider
```

CPython 判 `.pyc` 是否过期看的是源文件的 **mtime（秒）+ 文件大小**。所以**等长替换**——`0.50` → `0.40`、`min_length=1` → `min_length=2`、`>=` → `<=`——如果发生在同一秒内，字节码缓存不会失效，跑的仍是突变前的代码。

**这个坑的失败形态是「突变存活」，与「测试真的没有覆盖它」完全同形**：两种情况下你都看到一片绿，然后据此得出「这里缺测试」并去补一条其实已经存在的测试；或者反过来，把一条真的没覆盖的地方误判成已覆盖。2026-09-06 一次独立评审里有两个突变因此被判为存活，清缓存后复验，其中一个变红。

非等长的突变（删一行、加一句）通常改变文件大小，不受影响——所以这个坑只在你做最小、最精确的那类突变时出现，而那正是区分力最强的那类。
