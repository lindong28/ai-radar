# ADR-20260829-c0e8：将 AI Assistant 出网收据绑定到实现闭包与生产路径

- Status: accepted
- Date: 2026-08-29
- Supersedes: ADR-20260826-68e2 的 `External AI_ASSISTANT_ROOT boundary` 中 v1 收据身份范围；其余 selector、调用点闭包与审计决策保持不变

## Context

ADR-20260826-68e2 允许仓外 `AI_ASSISTANT_ROOT` 在 operator receipt 同时匹配 selector policy 与 `summarize.sh`、`run.sh` 两份脚本摘要后进入 interpret 保证范围。生产恢复复核发现，两份 shell wrapper 不持有实际网络实现：`shared/llm/client.py`、embedding、summarizer 与 tag normalization 等 Python 路径可以改变，而 v1 receipt 仍保持匹配。这样 preflight 在“实现仍兼容 selector”和“实现已绕过 selector”两种情况下会给出相同结果，不能支撑兼容性结论。

同一复核还发现，早期 fake-selector 验证只命中了 summarize/search 路径，没有执行生产使用的 `--check-url` 与 `--save-from-batch`，也没有分别命中 known-tag embedding 与 unknown-tag classification 分支。动态 `docs/tags.md` 会影响分支选择，但它是运行时可变数据，不适合作为代码身份摘要的一部分。

## Decision

### Versioned v2 receipt

新增 `ai-radar-egress-contract-v2.json`，不原地改写 v1 schema。AI Radar interpret 只接受字段集合完全匹配、所有 attestation 字段均为 `passed` 的 v2 receipt：

- `schema_version=2`
- `policy_id`
- `policy_sha256`
- `egress_implementation_sha256`
- `parent_gcp_env_selector_only_test=passed`
- `summarize_llm_selector_test=passed`
- `check_url_local_only_test=passed`
- `save_embedding_selector_test=passed`
- `save_unknown_tag_classification_selector_test=passed`

`egress_implementation_sha256` 由 repo-relative 路径与文件字节的确定性 framed records 计算，覆盖：

- `agents/summary-agent/summarize.sh`
- `agents/summary-agent/run.sh`
- root `pyproject.toml`
- root `uv.lock`
- `agents/summary-agent/src/` 下所有非 test Python 文件
- `shared/` 下所有 Python 文件

固定文件、代码根或 regular file 条件缺失，或者枚举、读取失败时，preflight fail closed。新增符合范围的 Python 文件会自然改变 digest；`docs/tags.md`、KB、临时文件与其它运行时数据不进入实现摘要。

### Receipt generation requires path-level attestation

Trusted operator 只有在隔离 mirror 的上述代码字节与目标 production root 机械一致，并完成以下 fake-selector canary 后才可生成 v2 receipt：

- 先把父进程六个标准 proxy 变量指向不可用地址，再由 AI Radar managed subprocess env 覆盖为 fake selector，证明父 GCP 环境不能接管路径。
- 用 production entrypoint 执行 summarize，并在 fake selector 观察 LLM 请求。
- 执行 `--check-url`，证明它只读本地索引且不访问 selector。
- 分别用 known tag 与 unknown tag 执行 `--save-from-batch`，观察 embedding 请求，并在 unknown-tag case 额外观察 classification LLM 请求。
- 使用临时 data、tmp 与 tags 副本，不写生产 KB。
- 对旧 v1、实现闭包字节变化、attestation 字段变化执行拒绝对照；有效 v2 必须通过。

Receipt 仍是 trusted operator 的声明。AI Radar 负责严格校验 schema、policy 与当前实现摘要，不把 receipt 外推成对 Python/uv/site-packages 或任意未来网络入口的动态证明。

### Failure discovery and rollback

Policy、receipt 或实现闭包漂移在下一次 interpret preflight 直接 fail closed；遗漏的新路径由发布前上述 canary 暴露。回滚 v2 收据或关闭 `AI_RADAR_ENABLE_INTERPRET` 只停止 interpret，不阻断 fetch/curate；需要回退代码时，保留的 v1 文件仍可供旧 consumer 使用，但新 consumer 不接受它。

## Options Considered

### 继续使用 wrapper-only v1 receipt

否决。它无法区分 Python 网络实现是否漂移，且没有覆盖生产 save/check-url 分支。

### 对整个 AI Assistant 目录做摘要

否决。它会把动态 tags、KB、临时输出和无关文档纳入身份，造成无关变化阻断生产，同时仍不能证明现场解释器与依赖环境。

### 只依赖运行时 fake-selector canary，不校验静态实现摘要

否决。Canary 结束后实现可以漂移而 receipt 不变；静态闭包摘要提供下一次 preflight 的漂移发现点。

### 把 Python、uv 与 site-packages 也纳入完整 runtime snapshot

否决。当前证据只支持列出的代码/lock snapshot 与路径级 canary，不能把环境存在或 lock 文件外推成现场依赖字节保证。

## Consequences

- AI Assistant 的网络相关 Python 改动或新增范围内文件会使 interpret 在重新 attestation 前安全跳过。
- Operator attestation 比 v1 更重，但只在实现或 policy 漂移时执行；稳定生产 run 仍只做本地摘要与 JSON 比对。
- 动态 tags 变化不会无故使 receipt 失效；其 known/unknown 两条网络分支由 canary 分别覆盖。
- v1 文件保留以支持旧 consumer 回滚，新 consumer 的语义通过新文件名与 schema version 明确切断。

## Scope and Unverified Items

本决策只保证列出的代码/lock snapshot 与五项 trusted path attestation。它不保证现场 Python/uv/site-packages 与 lock 完全一致，不覆盖未来跨出两个代码根的 import、插件、native socket、自定义 transport 或 receipt 生成后的运行时 monkeypatch。动态 `docs/tags.md` 的具体内容不在 identity guarantee 内；它只通过构造 known/unknown 两种隔离输入验证当前两条路径。

独立 decision review 首轮指出原提案把尚未执行的 canary 当成既有事实、把 lock snapshot 外推为完整 runtime、且缺少发现与回滚时间界。修正为 receipt 生成硬前置、收窄作用域并补充 fail-closed/rollback 后，原评审者复核放行；其非阻塞提醒是 mirror 的目标代码字节一致性必须机械校验，不能靠口头声明。
