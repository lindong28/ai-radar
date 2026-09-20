# 新闻准入候选 prompt

本目录保存跨准入 benchmark 复用的离线候选；使用 `--prompt evals/news-admission/prompts/<name>.json`。JSON 的 `system` 与 `user_template` 是模型实际输入模板，不含 gold。候选名称不代表已采纳或已达标，适用的输入依赖、逐轮结果及人评优先口径见[对象状态](../../../docs/evaluations/news-admission/status.md)与[人评说明](../../../docs/evaluations/human-labels.md)。省略 `--prompt` 时仍使用生产源码默认模板。

2026-09-20 从 `evals/news-admission/aihot-prefilter/prompts/` 移至此处，JSON 字节不变。旧目录是指向此目录的兼容链接，保留旧命令和历史引用；新文档使用本路径。冻结 run 中的请求与 prompt 身份不追改，也不因目录迁移重算成绩。
