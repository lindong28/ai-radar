# O3 · 分类研究方案履历

> [Developer] · 2026-09-27 起登记 O 阶段；实验标记不等于生产正式版本。历史 A–K 谱系见[分类图谱](../../../evals/content-enrichment/aihot-category-navigation/atlas/README.md)，M/N 记录见[状态](status.md)。本文件不批量重建历史身份。

本轮 `comparison_baseline=null`：没有已取得用户生产认可的正式分类基线；M2/O1 都只是具名研究比较对象。`formal_version=null`，生产认可来源为空，所有 O 节点未部署、不切默认。用户授权依据为 [ADR-8ea1](../../adr/20260927-8ea1-repair-category-source-material.md)。源节点 M2 的实现、来源和三批原件由[状态中的 M 阶段](status.md#2026-09-27m-阶段条件类别冲突已终态)定位，不能把它登记为无父根节点。

| id | parents | inspiration | 实际改动与 runs（UTC，2026-09-27） | 当前结论 |
|---|---|---|---|---|
| O1 | `[M2]` | `[]` | rubric仍M2，仅修输入读取与补全；中间材料窗口 `13-39-24`，final材料窗口 `13-48-38`、同配置重复 `13-57-42` | 中间35/48，final两次36/48、33/48；输入窗口分别保留，不将净增或重复波动归因为单一补全组件；保留为研究控制 |
| O2 | `[O1]` | `[]` | final输入不变，`category-o2.txt`重写主体与条件冲突；`13-51-06` | 34/48，相对final O1修5/退7；局部修复与回退并存，不采用整套替换 |
| O3 | `[O1]` | `[O2]` | 回到M2骨架，只改四条冲突条件，借鉴O2的工程讲解、交互工具、研究报告与部署运行边界；`13-53-09` | 34/48，未超过final O1，未证实稳定收益 |
| O4 | `[O3]` | `[]` | 原规则上加每题证据分析与第二调用复核；`category-o4-routing.txt` / `category-o4-review.txt`，`13-55-15` | 95次调用，初判33/48、最终34/48，复核修1/退0；相对final O1修4/退6，不晋级 |

各 run 的完整路径为 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-27/<UTC>/`，实验登记为 `experiments/content-enrichment/aihot-category-navigation/v1/2026-09-27/<UTC>/metadata.json`。原件中的 `started.json`、`identity-frozen.json` 和 metadata 绑定逐文件 code SHA256、实际 rubric/调用参数、cases/prompts/body-context/human-reviews 摘要。这几份原件本身没有Git commit字段；主线程提供的运行前后Git观察为 `894e80244b10a73a1f65fc77de6afa9b5cf183d5` 加未提交改动，该commit只是基底，不是完整被测实现。实际实现仍以运行source哈希为准，最终提交仅作post-run provenance映射，不能冒充运行时干净commit。

O1 final同配置重复不分配新实验身份；两次均值71.875%只来自2次，O2/O3/O4单次34/48落在该观察范围内，无稳定优胜证据。全部运行使用同48道人评开发题，47单答案用于六类P/R，1多答案按集合命中参加总体；失败仍在分母，原 `scores.json` 不是本轮人评口径。具体读数、修退原因和采用边界归[状态](status.md)，准备/复用参数归[v1入口](aihot-category-navigation/v1/README.md#2026-09-27o-阶段输入材料修复)。后续补证据注明日期，不覆写两个 O1 输入窗口的历史身份。
