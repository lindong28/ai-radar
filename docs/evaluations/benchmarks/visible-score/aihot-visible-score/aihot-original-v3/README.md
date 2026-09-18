# 可见评分建题 · aihot-original-v3

> [Developer] · 2026-09-18 用户批准；只替代 [object-specific-v2](../object-specific-v2/README.md) 的 Radar 输入必需条件，不改变指标或旧题库。规则名 v3 不等于 manifest schema 升版。

## L1：对象、题目与计分

2026-09-18 修订：同来源 URL 配对及输入版本按[实质性规则](../../../object-datasets.md#实质内容版本与-url-身份2026-09-18-用户修订)。仅发布时间、HTML、来源标签或标题排版变化不再排题；评分仍核原标题、正文与作者的实质差异。旧版本保持原样，当前数据版本见库存。

对象仍是 scorer 及其明确固定的展示分映射，目标为 AIHOT 可见 AI 分数，主指标 MAE，无 LLM 判官。只取观测到有限 0–100 数值分数的新闻；缺分数不按 0，不从排名或精选成员反推分数。

输入先取同来源、同 URL 的 Radar 过滤前 raw；完全没有该 raw 时，显式批准的 AIHOT 参照可提供 `original_title` 与绑定详情页的文章/推文正文。原标题、正文须可用且无内容/身份歧义；仅含缺正文提示的非空外壳不算原文，AIHOT 生成标题/摘要也不能代替。原始多版本排除、字段参考冲突只排该字段、同 URL 固定 split 均保留；不要求准入通过、精选资格、其它富化字段齐全或全日采集连续。

每题 input 是原始新闻，reference.score 才是可见分，二者不能混用。新来源扩大可建题范围，不证明跨站原文字节一致或原文未截断。当前全池可见分映射与逐条 scorer 分仍须区分；本轮只扩题，不把中间分冒充网站显示分，也不新增模型成绩。

## L2：资产与复用

- [统一建题与扩展说明](../../../object-datasets.md#仅扩充评分与富化aihot-原文输入) 给出 `--aihot-inputs`、`--base`、`--reference`、`--target visible-score` 的完整命令、授权继承及校验方式。
- 权威代码为 [object_datasets.py](../../../../../../evals/_shared/object_datasets.py)，指标及执行适用边界见 [evals 叶子](../../../../../../evals/visible-score/aihot-visible-score/README.md)。
- 数据保存在本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-visible-score/<version>/`，不用 DGX；实际题数与版本见 [inventory](../../../inventory.md)。
- cases/provenance 保留输入来源，evidence 冻结 AIHOT 原页，manifest 的 `aihot_input_references` 固定获准作为输入的参照。未来 base 扩展重读冻结原件、合并去重并重验，不拼 cases。

## L3：最小可信边界

这项授权只属于 O2/O3；输入不能并入 Radar raw 池或扩大 O1/O4。已有 Radar 输入优先；以后新 raw 替换 fallback 时记 updated，旧版本不覆盖。只在相同题库身份上比较模型候选；新增新闻不是拟合提升。独立题库仍用 schema_version=2，旧 v1 全池 runner 不接受它；推理适配和文本判官校验状态沿对象文档接续，不由建题完成冒称解决。
