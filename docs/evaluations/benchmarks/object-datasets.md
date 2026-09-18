# 逐对象独立建题与扩展

> [Developer] · 2026-09-18 用户认可并要求实现。替代“四对象必须共用一份完整原始窗口”的新建题默认；历史 v1 题库和成绩保持原样。

## 入口与范围

| 对象 | 独立规则 |
| --- | --- |
| 新闻准入 | [news-admission / aihot-all-members / object-specific-v2](news-admission/aihot-all-members/object-specific-v2/README.md) |
| 可见评分 | [visible-score / aihot-visible-score / object-specific-v2](visible-score/aihot-visible-score/object-specific-v2/README.md) |
| 内容富化 | [content-enrichment / aihot-enrichment / object-specific-v2](content-enrichment/aihot-enrichment/object-specific-v2/README.md) |
| 精选成员 | [featured-members / aihot-featured-members / object-specific-v2](featured-members/aihot-featured-members/object-specific-v2/README.md) |

共享采集档案，不共享入题门槛。来源契约与所给 AIHOT 参照的实际出现共同确定来源范围，排除暂停、禁用、非主时间线、微信专用源；不导入旧 T5，也不先用我方 prefilter 或 score 过滤原始输入。仅控制共同来源，不把原始池裁成两站新闻 URL 交集。

## 建题命令

在 AI Radar 仓库根运行，先 `uv sync --frozen`。全部离线，不读取 API 凭据、不调用模型、不改采集调度/网站 DB。

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --raw-root /absolute/path/to/data/raw-capture \
  --reference /absolute/path/to/aihot-reference/manifest.json \
  --reference /absolute/path/to/aihot/windows/START--END/manifest.json \
  --start 2026-09-15T14:45:00Z --end 2026-09-18T00:00:00Z \
  --version 20260918-object-v2
```

路径是占位示例，时间必须带时区。`--start/--end` 选择 Radar run 的 started_at，左闭右开，允许非连续段；不是“新闻发布日期必须落入此区间”。AIHOT 每份参照使用它自己已验证的新闻窗口，O1 另外读取完整 API 遍历判断逐新闻 ±12h。多份 `--reference` 可重复传入：支持小时 interval 根/manifest，或标准 `windows/<window>/manifest.json` 日窗 v1/v2/v3；整个 archive 根不是输入。参照损坏、抓取未完成、游标链/标签证据错误会拒绝建题，不静默跳过。不要把 staging 目录当完成窗口。

默认生成四个对象。只建一个或几个时重复 `--target visible-score` 等；可显式传 `--data-root` 与 `--contract-path`。版本 slug 只允许小写字母、数字、点、下划线、横杠。任一目标版本已存在就退出，不覆盖历史。

扩大题库：增加新的已完成 AIHOT manifest 参数、扩大 Radar 时间范围，使用新的 --version，重跑同一命令。它是从所选档案重算一个新版本，不是在旧题上原位追加；新增参照可能解决未知项，也可能暴露标签冲突，因此题数不保证单调增加。复制旧 manifest.rebuild 的输入参数，再加新范围即可；旧数据与旧成绩仍可复查。完全相同的输入重建为另一个版本，cases 内容应相同，生成时刻和版本元数据会不同。

## 输出与校验

```text
~/research/video-eval-arena/data/benchmarks/ai-radar/
  <target>/<benchmark>/<version>/
    manifest.json
    cases.jsonl
    excluded.jsonl
    recall-only.jsonl       # 仅 O1
    field-subsets.json     # 仅 O3，各字段的 case_id
    evidence/              # 本次第一个目标持有，其它叶子相对引用
      raw-inputs.jsonl
      raw-manifests.json
      inventory.json
      sources.json
      aihot/<reference-digest>/...
```

共享证据不依赖 O1 有题，也支持仅构建 O3。移机须一起移动本批所有叶子，或者保留 manifest.shared_evidence 指向的 owner；相对路径允许整个题库根一起移动。不要单独删 owner 叶子。

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py validate \
  ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-visible-score/20260918-object-v2
```

validate 校验问题集和冻结证据的字节摘要、对象/版本路径、题数与身份；不证明模型质量或采集连续性。退出非零表示未完成。零题版本允许保留，并明确显示 main=0，不称为可评测样本充分。部分输出后发生 I/O 失败时该版本无完成保证，使用新版本重建，别手工补一份 manifest。

每条 cases 包含 case_id、split、input、reference、provenance。input 是过滤前新闻与必要来源元数据，reference 才是 AIHOT 标签。按来源/URL 固定身份；同 URL 在各对象、各版本使用相同 split，不把重复新闻随机拆到两边。O3 用 field-subsets.json 选字段，不将缺值当错误或空值。排除记录按题/字段记录，因此 excluded 数不一定等于独立新闻数。

## 规模、版本与原始证据

每轮先通过现有 read_run 校验压缩原件及逐源计数，304 需验证并解析 payload_ref；失败来源/未完成轮次记录在 inventory，不让它们拖掉其它来源的已取得原始新闻。已完成轮次的损坏则中止，不静默删除。串行扫描共享本机磁盘，每次只展开一轮压缩包；内存随独立输入版本而不是重复抓取总行数增长。重复抓取只变 fetched_at 时保留最早观察的原始记录及次数，不复制每轮同一正文。原始采集档案本身不被修改。

冻结的是去重复后的原始输入版本、逐轮生产者 manifest、来源契约和必要 AIHOT 原页，不是完整 Radar 抓取档案副本。它足够复查这批题实际输入/参考，完整轮次重放仍使用原始 raw-capture。manifest 保留重建参数和 builder SHA；重建原始题时须使用相同代码/契约/采集档案，不能用现在的来源配置冒充旧配置。

配对对象 O2/O3/O4 取同来源、同 URL 且所选范围仅有一个原始内容版本；遇到多版本先排除，不能以标签更好匹配为由挑正文。AIHOT 不提供完整原文时，跨站正文一致性不可直接证明；manifest 明示此限制。多参照的同字段冲突不按“最新最好”择一；只排除冲突字段，避免用消歧假设污染参考。

## 执行边界与后续接线

schema_version=2 表示独立逐对象题集；旧 schema_version=1 的共享池 runner 只接受旧题库。当前新脚本负责建题与校验，不运行模型，也不实现网站逐条评分映射或更换精选规则。这些推理改造仍由后续评测/优化任务实施；不能把建题完成写成整套新推理链已跑通。

O1 可直接评价 prefilter；O2 需要 scorer + 固定逐条展示映射；O3 各字段独立，文本判官仍需用户校验；O4 使用我方固定预测执行逐条 threshold。全池 TopK/配额规则应另用完整候选组，不能消费这里的不完整配对子集。自动指标定义继续由各 evals 叶子的 metrics.json 维护，不增加新的治理分数或达标线。
