# ADR-015：Supplement 定价按调用时间选择有效区间

- 状态：Accepted
- 日期：2026-08-11
- 范围：P1 查询时成本派生中的项目内 supplement；不覆盖 LiteLLM 上游价格历史或 ARK 价格权威性

## 决策

每条 supplement tariff 必须声明 `effective_from` 与可选的 `effective_to`。历史成本按 usage 的 `created_at` 选择区间；当前活跃单价按本次观测时刻选择。相同 `(provider, model)` 的区间必须有序、不重叠且 `effective_from < effective_to`；调价时关闭旧区间并追加新区间，不得就地改写历史区间。

当前 ARK supplement 的首个区间从现有来源发布日期 `2026-05-27` 起。更早的调用没有可支持的有效价，保持 `unpriced`。

## 取舍

只增加三组静态 supplement 的区间语义和构造期校验，不建设数据库价格历史、管理界面或外部计费同步。仅增加 `priced_as_of` 仍会用新价回算旧窗口，可能污染后续量结构比较；完整价格历史系统则超出 P1。

## 边界

本决策只保证 repo 内 supplement 不因未来修改而静默重算历史。ARK tariff 的权威来源、实际套餐计费语义仍未核实；P2 的 tariff-only 变化通知与 A6 不变性继续由 plan 的 V37 验证。
