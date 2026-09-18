# 执行与迭代

> [Developer] · 新体系运行手册。统一 CLI：`PYTHONPATH=src:. uv run python -m evals._shared.cli --help`。后续 agent 使用 `eval-workflows iterate-eval-system` 接续，不重复建设体系。

## 新数据默认：逐对象独立建题

先按[按对象判断数据充分性](benchmarks/object-datasets.md#data-sufficiency)确定给定时间段的数据足够哪些用途。采集 coverage 用于定位过程缺口，不是四对象统一入题门；恢复、补采的实际内容及其证据决定可用范围。

执行 [scripts/build_eval_datasets.py 的操作说明](benchmarks/object-datasets.md)，支持多参照、独立对象、任意带时区 raw 时间范围和不可覆盖的新版本。O2/O3 无全日连续性条件，并可显式用 `--aihot-inputs` 接入 AIHOT 原标题与绑定原文；O1 沿原候选池规则，不补这种仅有正例的数据。O4 本次不扩题，完整池规则继续要求连续窗口的完整候选组；已有 v2 pointwise-threshold 仅作局部用途。独立题库暂不接下文旧全池 run，不能把新建题命令与旧运行命令直接串接。

扩展已有题库必须显式传 `--base <旧版任一对象叶子>`（多版可重复），再给新增原始范围/参照，使用新 `--version`。脚本合并证据、去重并按当前规则重建，不原地追加。完成后逐对象查 `merge-summary.json`、`changes.jsonl` 和 manifest.counts，并运行 validate；说明本版总题数与其中 added，而不是称整版为新增。更换题库版本不迁移旧模型成绩，比较仍须同题同尺。

## 历史 v1：共同窗口与全池运行

1. `capture --start <UTC> --end <UTC> --output <new-directory>` 捕获已关闭窗口。API 连续两次终态遍历须稳定，SSR 标签须有证据。失败产物保留，不冒充成功快照；不修改已有每日采集调度。
2. `build --raw-root <raw-capture> --reference <capture-directory> --version <new-slug>` 冻结共同来源、完整采集窗和逐题参考。缺 cadence/source 拒绝冻结；O1 按新闻时间 ±12 小时单独选题，未确定新闻不当负例。版本存在即拒绝覆盖。
3. 配置固定模型、provider/base_url，不含凭据；`--env-file` 指定项目 dotenv。先 `run --dataset <any-target-version> --config <json> --env-file <dotenv> --smoke 3`，检查三条新闻的真实请求模型、返回模型、输出 schema 和日志。
4. smoke 成功后移除 `--smoke`，运行全池。成功阶段按输入、源码、模型请求及 transport 身份缓存，失败不缓存。API 隐式重试关闭；重跑只补未成功阶段，不自动切换供应商。
5. `index` 重建查询投影；沿 metric row 的 source/pointer 回到原 scorer，不直接编辑成绩 JSON。

对象叶子 `evaluate.py` 转发同一 CLI；共享一批推理同时服务四对象，但每个对象有自己的题集、分母和分区。O1 只计可确定的 raw 题；O2 不因 O1 拒绝而丢失有分参考题；O3 各字段独立有参考才计题；O4 不依据 AIHOT 的成员数设 top-k。

并发参数 `--workers` 默认 8，可设 1–32；单位是 raw 新闻（一条新闻内阶段串行），文本判官单位为字段。实际峰值见运行 metadata；配置上限不是观测值。共享 provider 的限额由操作者按现场容量约束，本体系不自行提升远端配额。

## 文本判官与用户票

`judge <content-enrichment experiment> --config <json>` 复用现有预测，仅新增 title/summary/reason 单字段判词；默认模型 `deepseek-v4-flash-ga-260731`，0/1/2 及理由，没有校准则仅为诊断。

`export-calibration <experiment> --output <human-evals/new-folder> --config <json>` 从实际输入/参考/候选导出十二题及空标签模板。没有四条同时具备三文本参考的新闻就明确失败，不用编造的理由补齐。用户亲自给 0/1/2 标签并确认文件 SHA 后，运行 `calibrate <material.json> <labels.json> --user-confirmed-sha256 <confirmed> --config <json>`。先开发六题，再独立验证六题；任一不一致不产生可信 receipt。模型或 prompt 等身份变更后重校验。六题通过只是本小样本上的校验，不是普遍可靠性证明。

## 复盘、优化和回归

先从逐题输出定位差异，再在 [假设台账](experiments/hypotheses.md) 写具名假设、与替代解释不同的预测及对照。未取数写 open，不由低分直接推断 prompt 有问题。

O4 比较固定 `pool.jsonl`：复制公开配置并仅修改选择规则，`run ... --fixed-pool <baseline pool.jsonl> --label <candidate>`，不重新调用模型。O1/O2/O3 改对象或模型后跑同版本题集，沿缓存身份自动重算受影响输入。不得把参考分、参考分类或成员数喂给被测对象。

`compare <baseline experiment> <candidate experiment>` 要求同题、同尺；至少一项改善、其他不退步、无未知指标、题目全部完成才给 accepted。smoke 不参与验收。开发/回归新闻分组固定，优化选择只读开发结果，最终另查回归子集；已看过的结果不能再称盲留出。未设绝对达标线，不声称统计显著或已全面拟合。

失败/中断后先看各对象 experiment 的 `started.json`、共用 attempts 与已成功缓存，再重跑同配置；有 started 而没有 metadata 表示未收尾，不能视为成功。输出目录按创建 UTC 时刻追加，旧轮不覆盖。代码变动或输入版本变化时先重建基线再比，不把旧实验成绩接到新曲线上。

当前本机 ARK 凭据对应套餐入口 `/api/plan/v3`，不是普通 `/api/v3`。配置显式记录入口；首次smoke已证明误用普通入口会401。迁往其它环境须核该环境生效入口，不假设凭据跨入口通用；不要在401后自动换供应商。
