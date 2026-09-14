# ADR-20260914-f1a8：把旧 `eval` 明确放在 AIHOT canonical 评测链之外

- Status: accepted
- Date: 2026-09-14
- Relates: [ADR-20260905-499e](./20260905-499e-aihot-reference-fit-eval-system.md), [ADR-20260914-0442](./20260914-0442-close-the-aihot-eval-system-three-layer-loop.md)

## Context

三层评测体系完成后，零调用 audit 仍把两个不同问题写成待办：一是旧 `eval-fit` reports “尚待按新身份契约重算”，二是旧 `./run.sh eval` “尚未证明为父体系权威入口”。前者不能靠今天重算解决：旧 `run.json` 没有生产时的 `outputs_sha256`，新 report 对一份旧 run 的实测会直接拒绝 `expected=None`；现在给当前文件补摘要只能证明当前字节，不能证明当时计算报告所用的字节。后者也不是等待更多证据的问题：旧 `eval` 早于 `eval-fit` 存在，读取单份 AIHOT Markdown 与当前数据库，产出 V1–V5 指标报告和 V6 HTML；`eval-fit` 与归档量具已经被后续 ADR 指定为逐条与首页归档的两个 canonical 面。

## Decision

保留旧 `./run.sh eval` 的现有行为、手工比较能力和历史产物，但把它明确分类为父 AIHOT canonical 评测链之外的 legacy snapshot comparison/reporting tool。它不进入 `eval-fit` 的建立、运行、判官、回归与报告链，也不进入归档趋势；当前是否仍有调用者以及未来是否退休保持未核实，不由本次分类推断。

audit 的状态维度继续只用 `located`、`missing`、`invalid`；`separate` 只写在角色说明中。`located` 不能硬编码：必须从当前源码同时确认 `eval` parser、`main` dispatcher、`_eval → run_eval` adapter、`run_eval` producer，以及 V1–V5 report / V6 presentation 的输出契约。任一环节存在而链不完整时为 `invalid`，全部缺失时为 `missing`。探针保持只读和零 LLM 调用，不实际执行默认会调用判官的旧入口。

旧 reports 保留为 pre-contract、non-comparable 历史资产，不补造 producer-time identity，也不再写成“等待重算”。新 report 继续在计算前复算 questions、outputs、judgments 与 calibration 的实际摘要；只有生产时身份存在且一致的 run/report 才计入 governed 数量。

## Options Considered

### 删除旧入口

否决。会失去仍可执行的手工快照比较能力；已有 tracked 历史证据本身不会因入口删除而消失，因此不把“保留历史证据”作为这一选项的否决理由。

### 把旧入口别名到 `eval-fit`

否决。两者的输入、身份、输出与付费语义不同，别名会把不同对象伪装成同一权威链。

### 继续标为 `unverified`

否决。已有源码与 ADR 足以判定它不属于父链；继续悬置会让 audit 永久留下一个不存在的接续问题。

### 只改说明文字，不探测链路

否决。入口删掉或断开后仍会报告同一结论，与 ADR-20260914-0442 要求 audit 从真实资产反推状态相冲突。

## Consequences

- 旧入口的角色、当前连通性与 canonical authority 分成三个不同问题，不再混成一个 `unverified` 状态。
- 33 个旧 run 与 25 个旧 report 可以诚实保留，但不能升级为新身份契约下的可比较基线；未来新资产会单独计入 governed 数量。
- 本决策不修改题集、参考答案、判官模型、目标标准、生产内容逻辑或调用次数，也不触发任何付费调用。

## Decision Review

独立评审先判 `复核`：方向成立，但指出“presentation comparator”漏掉 V1–V5 指标报告，且若 audit 只改成硬编码 `located`，入口断裂后仍无法发现。收窄为完整 legacy snapshot comparison/reporting role，并增加 parser→dispatcher→adapter→producer→output contract 的静态连通性探针与断链负例后，复核结论为 `放行`。决策指纹为 `106135a29aada9ebce7c56d6cba54414d6789af04d432fada76a1d0af1d2eb9b`。
