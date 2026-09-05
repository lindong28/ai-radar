# ADR-20260905-b00f：出网收据只在写盘前复核生产策略后生成

- Status: accepted
- Date: 2026-09-05
- Extends: ADR-20260829-c0e8 的 `Receipt generation requires path-level attestation`

## Context

Selector compatibility attestation 与外部收据写盘原先是两个手工动作。2026-09-02 的一轮 attestation 测试了 `policy_sha256=6fdcfb9f…`，但在收据写盘前生产策略已切换为 `58a97e64…`；因此收据落盘时已经不能代表当前生产策略，随后 interpret 的严格 preflight 连续拒绝它。Consumer 的 fail-closed 比对工作正常，缺口在 receipt producer 没有把最后一次生产策略读取与写盘绑定在同一入口里。

## Decision

新增受测的 AI Radar operator writer。操作者把本轮实际完成 attestation 的 policy SHA 与 implementation SHA 显式传入；writer 先重新计算目标 AI Assistant 实现闭包摘要，不相等时不再读取 selector，也不写任何文件。闭包相等后，writer 在任何备份或收据改动之前清除进程内 selector policy cache，并通过 `require_selector_policy()` 重新执行生产 `check-proxy-status --format=kv` 契约。只有 live `policy_sha256` 与 tested SHA 相等时，writer 才备份既有 v2 收据并以同目录原子替换写入新收据；production preflight 失败、实现摘要不相等或 policy SHA 不相等时，writer 非零退出且不改收据、不创建备份。

Writer 复用 `require_selector_policy()`，不把 `./run.sh egress-preflight` 当前面向人的 stdout 反向固化为机器解析契约。正常与策略切换两侧都由测试直接观察目标收据和备份是否变化。

本次补跑的动态证明在 macOS 上把真实 production entrypoint 放进 `sandbox-exec` 的进程树网络围栏，只允许 loopback fake selector，并为每个命令生成唯一 Seatbelt denial marker。统一日志读数在同一个两秒有界窗口内先以“非 loopback 直连被拒且 denial 可见”作阳性对照，再以“loopback 可达且 denial 为零”作阴性对照；任一 production entrypoint 出现带本轮 marker 的 outbound denial 都使 attestation 失败。Fake selector 同时记录全部 method/path、拒绝未知请求，并将脱敏 payload identity 与落盘 embedding artifact 纳入断言，避免 dummy request 与真实输入未送达产生同形通过。

## Options Considered

### 只在 contract 文档追加手工复核步骤

否决。检查与写盘仍是两个可分离的操作者动作，负例只能证明检查命令报错，不能证明实际写盘入口拒绝修改收据。

### 修改 interpret runtime preflight

否决。Runtime 已严格比较 live policy、receipt 与实现闭包；把 producer 的写入竞态继续加固在 consumer 侧不会让收据在生成时变得真实。

## Consequences

- 完整 attestation 仍是 trusted operator 的人工证明；writer 只负责把测试身份、最后一次 live policy 读取和收据写盘收敛为一个可复核动作。
- 策略在 attestation 结束后、writer 读取前已经切换时，writer 会拒绝写盘并要求对当前策略重跑 attestation。
- 收据仍写在 AI Assistant 根目录；AI Radar 仓只持有 writer、测试与跨仓契约文档，不接管 AI Assistant 的运行时数据。

## Scope and Unverified Items

本决策只在 writer 与生产 interpret consumer 读取同一个 selector authority 时成立；在另一台机器、另一套 `HOME`/shell 配置或另一份 status authority 上运行 writer，不能证明生产 consumer 所见策略。它也不声称消除 `require_selector_policy()` 返回与同目录 `os.replace` 之间外部无锁策略更新的理论微窗，且不新增多 operator 并发 attestation 的锁协议。完整 path-level canary、实现闭包边界与 trusted operator 限定继续由 ADR-20260829-c0e8 持有。

`sandbox-exec` 已被 macOS 标记 deprecated；上述网络围栏与两秒日志观察窗只用于本机本轮 attestation，并以同轮阳性对照校准，不构成跨平台或长期 telemetry 保证。它证明目标 HTTP entrypoint 没有成功或被围栏拒绝的非 loopback outbound 尝试，不把 Unix socket、安装态 venv/site-packages 字节或实现闭包外 imports 纳入保证。真实 entrypoint 必须在新保留镜像中全程通过该围栏并经独立 instrument review，才可据此重写生产收据。

独立 decision review 首轮要求补齐 AI Assistant 侧决策语料，并拒绝把 egress-preflight 的人读 stdout 当机器契约。补充确认 AI Assistant tracked ADR/issues 中没有 receipt/backup 既有契约，并改为复用 `require_selector_policy()` 后，原评审者复核放行；其非阻塞提醒即上面的 selector authority 与理论微窗边界。
