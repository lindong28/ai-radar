# 内容富化 · aihot-enrichment-fields / v2

> [Developer] · 2026-09-21 发布的完整快照；schema_version=2、evaluation_mode=pointwise。继承 v1，增加 AIHOT 9/19、9/20 UTC 日窗；没有修改历史 gold。

本版 3,866 条新闻、13,331 道字段题：category 1,673、tags 3,866、title 3,866、summary 3,810、reason 116。相对 v1 新增 1,325 道字段题，保留 12,006 道，updated/removed 均为 0。输入来源为 Radar raw 1,137、AIHOT 原标题及绑定原文 2,729。不同字段按各自子集消费，不将字段题数当成独立新闻数，不将 v1/v2 相加。

## 分类参考的适用边界

本版 category 原样保存 AIHOT API 字段：tip 697、industry 389、ai-products 312、ai-models 167、paper 108；另外 2,193 条没有 category，不参与该字段计分。有分类的 dev 1,324、regression 349。**这不是已经对齐六个网页分类入口的新 gold**：网页有 opinion 导航，而本版 API 仍没有 opinion；旧 tip 含观点文章。不能将 tip 无条件解释为新六类中的“教程”，也不能用这个五类分母声称六类效果。

## 构建与扩展

数据在本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v2/`，不放 DGX。按[统一建题规则](../../../benchmarks/object-datasets.md)合并、去重、重验，精确参数与冻结来源在 manifest.rebuild、reference_manifests 和 aihot_input_references。原始输入、字段标签、来源与证据的含义沿 [v1](../v1/README.md)。

本次执行（在项目根）：

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --base ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v1 \
  --reference /path/to/aihot/windows/2026-09-19T000000Z--2026-09-20T000000Z/manifest.json \
  --reference /path/to/aihot/windows/2026-09-20T000000Z--2026-09-21T000000Z/manifest.json \
  --aihot-inputs --target content-enrichment --version v2
```

后续以 v2 为 base，加入新原件，使用未占用的 v3；不覆盖本版。构建不调用模型、不代表已经评测。实际 loader 已核文件和冻结证据摘要、并读出上述 3,866 行；集合有五个非空 API 分类取值，不能据此验证第六类。模型实验和六类网页语义核验进展见[对象状态](../../status.md)。
