# 仓内解读与知识库引擎

> [User] Mutable snapshot。AI Radar 自行维护摘要生成和知识库工具；不需要执行 ai-assistant checkout 中的代码。原 [ai-assistant 契约](ai-assistant-contract.md) 保留为历史接口说明，当前入口以本文为准。

## 入口与边界

| 入口 | 作用 | 是否调用模型 / 写 KB |
|---|---|---|
| `./run.sh interpret` | Radar 候选文章解读、按保存决策入库、回写 Radar | 可能调用摘要、标签分类、embedding；按 `save_decision` 保存 |
| `./scripts/interpret/summarize.sh --input <文件或URL> --user <user>` | 生成 scratch article、summary、metadata | 摘要经 gateway；URL 抓取经 Radar selector；不保存 KB |
| `./scripts/interpret/run.sh --check-url <URL> --user <user>` | 规范化 URL 去重 | 不调用模型、不写 KB |
| `./scripts/interpret/run.sh --list-article-records --user <user>` | 版本 1 JSONL catalog：header + 逐篇状态 | 不调用模型、不写 KB 数据；锁文件除外 |
| `./scripts/interpret/run.sh --search <查询> --user <user>` | 向量搜索，可加 `--terms` 字符串加权 | 查询 embedding 经 gateway；不写 KB 数据 |
| `./scripts/interpret/run.sh --save-from-batch <slug> --user <user> --batch-dir <目录> --meta-json <JSON>` | 保存 scratch、追加索引、归一化标签、更新向量、登记项目 | 会写 KB，可能调用标签分类和 embedding |
| `./scripts/interpret/run.sh --build --user <user>` / `--add <slug>` | 增量重建 / 单篇向量更新 | 可能调用 embedding、写向量与 manifest |

同一工具还保留 `--verify-url-vector`、`--list-keywords`、`--append-entry`、`--add-project`、`--list-projects`。完整参数以两个脚本的 `--help` 为准。Python 入口分别为 `airadar.interpret.engine.summarizer` 和 `airadar.interpret.engine.embedding`；脚本显式使用 Radar 的项目环境和源码目录，不加载源仓 `.env`。

高层个人工作流 skill 不是 Radar 实际运行的依赖，因此未整包引入。源仓其它工作流、Claude/Codex CLI 路由、SDK fallback、凭据和用户知识库内容都不随代码迁入。

## 配置已有数据，不复制或重建空库

`AI_RADAR_KB_ROOT` 必须指向含 `articles/`、`<user>/` 的既有 KB 根，而不是某个用户目录。`AI_RADAR_INTERPRET_USER` 选择用户（默认 `default`），直接 CLI 用 `--user`。目录约定：

```text
<AI_RADAR_KB_ROOT>/
├── articles/<slug>.md
├── tags.md                       # 未单独指定词表路径时使用
└── <user>/
    ├── index.json
    ├── persona.md
    ├── article_summaries/
    │   ├── <slug>_output.md
    │   ├── building_effective_agents_output.md
    │   └── 软件工程师头衔要没了ClaudeCode之父YC访谈_output.md
    ├── embeddings/vectors.npy
    ├── embeddings/vectors_manifest.json
    └── open_source_projects.json
```

画像和两份参考输出仍是实际摘要输入，必须从用户指定的数据位置读取；本次未用通用占位内容替代、未复制进 git。启动 preflight 检查用户索引、画像、参考输出和词表；缺项时明确 skip，不把未配置或缺失的旧库当作空库继续生成。独立工具缺少索引会非零退出。

`AI_RADAR_INTERPRET_TAGS_PATH` 可单独指定既有可写词表。未指定时先兼容旧 `AI_ASSISTANT_ROOT/agents/summary-agent/docs/tags.md`，否则使用 `AI_RADAR_KB_ROOT/tags.md`。保存时的标签归一化会更新该文件，因此它是操作员管理的数据，不是只读代码资源。包内 `engine/assets/tags.md` 是源 git HEAD 的种子，不会自动覆盖或替换现有词表；源仓尚未提交的新增词条没有被搬入 git。

旧 `AI_ASSISTANT_ROOT` 和 API/CLI 的 `assistant_root` 参数仅兼容数据布局：KB 位于其 `data/summary_agent`，旧词表也可沿上述路径读取；不会执行那里的脚本或导入其 Python 包。`AI_RADAR_KB_ROOT` 优先于该兼容值。`admin wechat-kb import` 继续接受旧 `--assistant-root`，设置新 KB 环境变量时无需外部 checkout 路径。

迁移前先确认目标 user 对应原索引，保留原 `vectors.npy` 和 manifest。旧索引内的 `data/summary_agent/...` 相对路径会映射到配置后的 KB 根；绝对路径保持原义，不自动重写历史索引。可先通过 `--list-article-records`、`--check-url` 和 `--verify-url-vector` 检查，不要先调用 `--build` 重付已有向量。

锁身份绑定实际 KB / 词表路径，不随调用目录或 checkout 改变。旧 `<owner>/data/summary_agent` 布局继续使用 `<owner>/tmp/summary_agent_<user>.lock`；其它 KB 使用根下 `.locks/`。旧 `agents/summary-agent/docs/tags.md` 词表继续使用原 owner 的 `tmp/summary_agent_tags.lock`；其它词表使用同目录 `<文件名>.lock`。两阶段向量更新保留；KB 文件复制与 index append 仍不是完整原子事务，未借迁移扩大为存储重构。

## 模型与失败语义

摘要默认 logical model 为 `deepseek-v4-pro`，可用 `--model` 或 `AI_RADAR_INTERPRET_MODEL` 选择。Radar runner 保留 `ai-radar-interpret-deepseek` 的本地兼容名称，它只映射到 `AI_RADAR_DEEPSEEK_INTERPRET_MODEL`（默认 `deepseek-v4-pro`），不再表示 ARK-first 或任何 provider fallback。`AI_RADAR_DEEPSEEK_THINKING` 保留原 thinking 选项。标签分类默认使用同一配置入口；embedding 型号固定为 `text-embedding-3-small`、1536 维，未改变既有向量空间。

所有模型调用复用 `airadar.provider.llm_gateway`：只连已验证的 loopback base URL，使用非秘密占位 key、`max_retries=0`、`X-LLM-Project` 和发送前生成的 `X-LLM-Request-ID`。chat 与 embedding 均校验 companion projection、request ID、requested logical model，provider/native model 取 gateway 的实际身份。配置只需 `AI_RADAR_LLM_GATEWAY_BASE_URL`、`AI_RADAR_LLM_GATEWAY_PROJECT`（默认 `ai-radar`）及可选 session；provider keys 留在 gateway，子进程白名单不再透传它们。

429、超时、identity 不符、输出解析失败都不在本轮重新发送。摘要在解析前保存 `<slug>_gateway.json`，含 request ID 与原始成功响应；错误对象保留 sent request ID，便于关联 gateway ledger。正常 metadata 继续携带 usage，并增加 gateway 实际身份；Radar 既有 best-effort 计量不驱动模型重试。

旧 [缺失 criteria_reason 立即重试 ADR](../adr/20260828-c3a5-retry-missing-criteria-reason-once.md) 的“同轮立即重发一次”部分已被本次 gateway 迁移取代；跨轮错误队列的既有 backoff/cap 保留。推荐档位、独立 `save_decision`、validation 信息性语义不变。KB 的 URL/slug 冲突在标签归一化和 embedding 前检查；命名冲突可换 slug，但不把已付费摘要再生成一次。

## 维护面与验证边界

摘要模板、解析 schema、slug 规则、设计文档和 KB 数据契约来自下列源版本。存储/schema 细节见 [迁入数据契约](../../src/airadar/interpret/engine/assets/data_stores.md)，其中旧仓脚本路径和环境变量是来源快照；当前入口及配置以本文为准。runtime 新增直接依赖 `numpy>=1.26.0`，锁定版本未升级；其余所需依赖已在 Radar 中。

离线验证覆盖 chat/embedding 成功、缺 identity、错 request ID、错 model、429、timeout 六态；摘要有效/无效解析；旧相对路径的真实本地 catalog CLI；已有 schema、去重、向量/manifest、标签归一化、项目登记、并发锁及 Radar 消费者测试。未用真实文章、真实画像或真实 KB 验证生成质量；未部署、未验证远端 gateway 可达性、实际上游模型能力或生产写入者切换。真实付费验收与部署由主任务单独记录。

```bash
AI_RADAR_DB=/tmp/radar-interpret-tests.db PYTHONPATH=src:. uv run pytest -q tests/interpret_engine tests/test_wechat_interpretation.py tests/test_wechat_kb_archive.py
```

## 来源身份

来源仓 `ai-assistant`，提交 `ad119e305903d3f6ce4793e14bcd8ab77ca33c51`（2026-09-22 读取）。以下 SHA-256 是迁移前源字节，不是适配后目标文件摘要。Python 与测试取该时点工作树（所涉源码无未提交差异）；三份 docs 取 git HEAD，明确不包括 source `docs/tags.md` 的未提交词条。迁入后由 Radar 维护，不运行自动同步或运行时源码加载。

源前缀均为 `agents/summary-agent/`；`src/` 映射到 `src/airadar/interpret/engine/`，`docs/` 映射到该包 `assets/`，两组测试汇入 `tests/interpret_engine/`。新增 gateway 适配、paths 模块、wrapper 和专门 gateway 测试由本次迁移编写，不冒充源文件。

| 源相对路径 | SHA-256 |
|---|---|
| `src/embedding.py` | `f7fb82067b175aaaaeb945e717576d96f60e79c59358e14a5d728b1ddfb2a0f4` |
| `src/tag_normalizer.py` | `87b279f190bbd301e91c57e70204342980d2f744ff920511a9e0d7572c765143` |
| `src/summarizer/__init__.py` | `9855b520abedaf1a2db0675529962db3ce4f2536d01cb5bc3006f9038337e978` |
| `src/summarizer/__main__.py` | `dfbaa4846f2795298f3cc33430e8fa3e06e3cbbebca1043f6d6aaf0e21fd56cf` |
| `src/summarizer/core.py` | `770f233437d030c0d6a311ef4f59a472fdacf7233f5b0ecabb28aa504e0f63cf` |
| `src/summarizer/cli.py` | `ce6ca5d95951046600f48d55f03d9023b6b88ab970449bdac681874311511e77` |
| `src/summarizer/schema.py` | `8d701d35e66d157669a56ccacccd86d458ab3c912c1b5b5fe81c1b4f1193b04e` |
| `src/summarizer/slug.py` | `cc74a6f583a81bb7b3d33338d1c179963003e76da2149ee60c600d6c82c853dd` |
| `src/summarizer/fetcher.py` | `4f203bcb572ac5f8461955d72f805c68ece5cec1c832c569d7657a582227d2d6` |
| `src/summarizer/prompts/system.md.j2` | `013e3ee43917d1d1ee297e9690e34e6db6e35b3e2232f9b970033182db684459` |
| `src/summarizer/prompts/user_article.md.j2` | `c29f794c66836ffcd45cbca780a665a963a70e746d426c5dfc2c475ded578dd3` |
| `docs/summary_agent_design.md` | `f8f0823b5bf9c88b4dd84ead8610f2079f5bdc675d8c675cfb54fd1a010ff3a6` |
| `docs/data_stores.md` | `b1eb33cfe59573e1b598c2d298cf8722cd2d4ace257781baee3d215c631618f7` |
| `docs/tags.md` | `1948f70d540e01db0c92cb8263ddc00e15b746b65107328c55e3e0ee1700debf` |
| `tests/test_embedding_locking.py` | `dcb3b620eb31a2f40671fb67ffb2318afed476d688e6d80b176d340e352a8c5c` |
| `tests/test_save_from_batch.py` | `1f7023810e10d5e3b904ef1db250e7c72a33d20c6d80010eddea8ef89ab8aba5` |
| `tests/test_save_from_batch_projects.py` | `cbbc7cbed4c301a9ba824295738a3d1efe3245e59228d553ece8bbbaf1f24ae5` |
| `tests/test_save_from_batch_normalization.py` | `9905e11c827c11d78115a8cbf48fcb4f0f991fd6135cf20fc8d38f4245515721` |
| `tests/test_tag_normalizer.py` | `09c6102c00749f0ce97acea493a3231589ed905ef4e28e6ceab7ca886ee48936` |
| `tests/test_article_catalog.py` | `d9b42f258f48ea4f861237e218ffa80b97bfd403d77baf6bb565c0d565affb35` |
| `tests/test_project_url_validation.py` | `1a91d55233ed6602e3ce0ddbcc9d525436467de96fd09397c1f9646ca88e5396` |
| `tests/test_dedup_integration.py` | `07b4bb2916e41ba89761073c34cde8e4afed2ffc66ceffbfe0c04db092e70695` |
| `src/summarizer/tests/test_slug.py` | `b8645c59081a85a10c021b9faa8232ac100b60b93f7f0674c52166f4d2f4addf` |
| `src/summarizer/tests/test_schema.py` | `5fced6b48b4c340b076f870cdd1358b6d8190f9e982194734e0bc58d7bac857d` |
| `src/summarizer/tests/test_validation.py` | `47fd5b72e6ceeb4713a858a8536a7f8febd49d0dfc3ee1d97208f4e987b3ff7f` |
| `src/summarizer/tests/test_fetcher.py` | `e1be53a81161773157453abaaba16d47a8cd83575e7b0103ed2677ec681c4a90` |
