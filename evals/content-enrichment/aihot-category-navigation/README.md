# aihot-category-navigation

O3 的六类网站分类：模型、产品、行业、论文、教程、观点。AIHOT 网页 category 过滤以新闻类型分组；旧 `/api/v1/items` 把教程和观点合并为 tip，故其金标不能复用为同一消费者契约。本 benchmark 从实际分类页面成员建单类题，不从 tags、旧 category 或模型猜 gold；多类观测冲突排除、未观测不作为负例。它与 `aihot-enrichment-fields` 分开，后者保留其它富化字段与旧 API 口径。

## 题集与复用

只使用已通过原始输入建题规则的原标题、正文。参考是六个网页过滤入口实际返回的同 AIHOT item_id；保留页面原件、哈希、请求 URL、观察时间及原输入来源。按原新闻 case_id 去重，保留原 dev/regression split。新增材料用 `--base` 合并、按实质正文检查冲突，不覆盖旧 vN。大资产在本机 video-eval-arena，版本说明在 `docs/evaluations/content-enrichment/aihot-category-navigation/vN/README.md`。

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/capture.py \
  --output /path/to/new-frozen-category-responses --pages 3
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/build.py \
  --inputs ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v2 \
  --capture /path/to/frozen-category-responses --version v1
# 以后扩展：增加 --base .../aihot-category-navigation/v1，给新 inputs/capture，输出未占用的 v2。
```

capture 是只读公网 GET，六个类别并发、每类游标顺序翻页；默认每类最多 3 页，不代表抓到网站全部历史。`capture-summary.json.end_reached=false` 明示受页数限制。按需要增加 `--pages`（上限 50），每次使用新目录；未实际观察到的历史分类仍未知。既有 all-page/API 五类采集不等于此六类标签采集，本命令不擅自变更常驻调度。

捕获目录的 `*.response.json` 记录 url/status/finished_at/sha256，与同名 `*.body` 绑定；仅消费 `https://aihot.news/all?category=...` 和 `/api/public/feed?category=...` 的成功响应。HTML 读取真实新闻 RSC 对象，不把导航链接当成员。题库内 evidence 是这些观测与输入 cases/manifest 的冻结副本；原始输入的完整档案仍由 source_datasets 定位，不因此删除旧档案。

## 真实模型评测

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/evaluate.py \
  --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-category-navigation/v1 \
  --config evals/content-enrichment/configs/category-flash.json \
  --env-file /path/to/project/.env --split dev --limit 8 --smoke --label category-smoke --workers 8
```

smoke 验证链路，不代表质量。随后固定 seed/开发题比较 rubric（`--rubric path.txt`）；冻结候选后使用 regression，不把开发或反复选型结果称盲测。模型输入严格只有 title 与前 5,000 字正文，参考、tags、AIHOT 摘要不进入 prompt；每题一个 Flash 调用，输出 reason 后 primary_category。共享实现位于 `src/airadar/enrich/category.py`，runner 为 `evals/_shared/category_eval.py`；独立调用成绩不代表完整 enrich 或生产已上线。

主指标 `category_accuracy` 复用确定性 O3 scorer，无需 LLM 判官；调用/格式失败仍占分母并使该轮 incomplete。diagnostics 保存逐类分母、混淆矩阵和多数类基线，不额外改变验收阈值。人评从稳定 `human-evals/content-enrichment/reviews.json` 按输入身份和字段优先覆盖，只作用本轮计分视图，不改原始参考。

逐题 prompt/response/reason、attempts、scores、diagnostics、conclusion 保存在 `runs/content-enrichment/aihot-category-navigation/vN/<UTC-date>/<UTC-time>/`；metadata 与指标在同分区 experiments，统一索引由 assets.rebuild_index 重建。`--output-root` 可指定主 checkout 归档而在隔离 worktree 执行代码。失败/无参考类别不从分母静默丢弃；不自动 fallback、重试、改 gold 或部署。
