# AIHOT 已观察收录 · v1

> [Developer] · 标签设计已经用户批准；构建、核验及实验的当前读数见 [status](../../status.md)。本目录记录消费者契约，不承载题目正文。

## 输入与 gold

候选来自 Radar 未过滤原始新闻，按共同来源和规范 URL 去重，不添加 AIHOT-only 正例。原始材料与题目存于 `~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-observed-membership/v1/`。`cases.jsonl` 是主集，`recall-only.jsonl` 是资格不足但存在同身份收录见证的补充集，`excluded.jsonl` 保留未知或冲突，不把它们当负例。三者不合并计算 precision。

`reference.member=true` 表示冻结 AIHOT 原始 API 批次中观察到同来源、规范 URL 新闻；`false` 仅表示在这些批次中未观察到，不是显式拒绝、永不收录或某一瞬间完整快照。已有见证不再因 ±12h 时间差失效。完整规则见 [时间标注设计](../../time-label-design.md)，实现为 `evals/_shared/admission_labels.py`。

主集资格先于正负判定，要求可靠原文历史下界到共同右界 H 的参照覆盖、末两遍成员稳定、原始输入可用且无实质版本冲突。H 取已验证 API 遍历共同覆盖右界，不能用遍历结束时间代替。多个覆盖区间可以拼接，但不跨越缺口。发布时间、HTML、标题标点或来源显示标签单独变化不是实质版本变化。

当前日期证据读取 X API 的原始 `created_at` 派生字段并核对帖子身份；Claude release 标题日期、DeepSeek release URL 日期按日精度保留，未知时区用最早可能的 UTC+14 日界作保守下界。RSS 的 published/updated/fallback 分支及普通 Web cards 的日期来源未保存，不能仅凭非空 `published_at` 得到可靠下界。HF papers 的抓取时补值、模型列表 lastModified 均不能作原文发布时间。缺日期时正负同样不能进入主集。

## 重建与扩展

首版主集 1,450 题（727 正、723 负），仅召回 880，排除 14,128；主集含 94 来源，X 1,444 / Web 6。共同右界 H=`2026-09-19T00:07:06Z`。manifest SHA256 为 `f33fb137752f4a6d45611b0e70b59d2fd355d3f96e8c6d812aaaef2f4c9eb954`；详细排除与旧版去向见[库存](../../../benchmarks/inventory.md)。历史 raw manifests 均声明 code_dirty，字段来源核对依据归档结构与对应 producer 实现，不构成当时运行代码逐字节可还原的证明。

在 ai-radar checkout 中运行；输出版本不可覆盖。当前扩题从本 benchmark 的上一版合并，追加新参照／原始输入参数即可：

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --base ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-observed-membership/v1 \
  --target news-admission --admission-benchmark aihot-observed-membership --version v2
```

追加已验证 `--reference`，以及成组的 `--raw-root / --start / --end` 参数，使用下一个未存在版本。底层仍是合并原始证据＋去重＋按当前设计重验全部资格与标签，不是累加旧 cases。日期缺失的数据不会因扩题被自动删除，仍保存在 evidence 中；取得新的独立证据后可以再建下一版。

历史 v1 最初从 `aihot-prefilter/v1` 冻结证据重建，不继承旧 gold；该父库现位于 `~/research/video-eval-arena/data/benchmark-archives/ai-radar/20260920-cleanup/news-admission/aihot-prefilter/v1`。复现首次构建须使用独立 `--data-root`，不能覆盖已发布的 v1。

每版 `manifest.json` 固定输入、代码哈希、参照批次、来源计数；`changes.jsonl` / `merge-summary.json` 对比旧题去向。`provenance.admission` 保留发布时间依据、H、覆盖参照及匹配见证。旧版本只用于复现，不与本版累计题数或混算成绩。

## 执行与结果

```bash
PYTHONPATH=src:. uv run python evals/news-admission/aihot-observed-membership/evaluate.py validate \
  --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-observed-membership/v1
PYTHONPATH=src:. uv run python evals/news-admission/aihot-observed-membership/evaluate.py run \
  --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-observed-membership/v1 \
  --config <已核验模型配置.json> --env-file <本机凭据文件> \
  --split dev --limit <预算内题数> --seed <固定seed> --label <轮次名>
```

候选可加 `--prompt`；未见回归须用 `--exclude-run` 排除已见轮次，换 benchmark 不会洗掉旧题暴露。实际调用之前核验模型/配置/题目身份与剩余预算。失败响应保留为缺失，不能当 false。③⑤没有 LLM 判官；④仍为主集 TP/(TP+FP)、TP/(TP+FN)，两者目标分别 >90%。

旧响应只有逐题实际输入完全一致才可零调用重评分：`PYTHONPATH=src:. uv run python -m evals._shared.admission_rescore --source-run <旧轮目录> --dataset <新库> --label <迁移诊断名>`。旧对象身份原样保留，输入变更不复用，失败仍是失败。这只测 gold/主集变化，不能宣称模型提升。

L2 逐轮产物在 `runs/news-admission/aihot-observed-membership/v1/<UTC日期>/<时间>/`；元数据与累计指标在 `experiments/news-admission/aihot-observed-membership/`。L3 保持不同 benchmark 的语义、分母和历史成绩隔离，主集范围以实际来源覆盖为准，不外推全部来源或生产效果。
