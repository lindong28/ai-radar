# Summary Agent 数据存储文档

本文档描述 `data/summary_agent/` 下每个数据存储的 schema、读写方、不变量及维护方式。

---

## 1. `<user>/index.json` — 文章索引

**路径**: `data/summary_agent/<user>/index.json`
**格式**: JSON 数组

**Schema** (每个条目):
```json
{
  "title": "文章标题",
  "input": {
    "article_file_path": "data/summary_agent/articles/<slug>.md"
  },
  "output": {
    "summary_file_path": "data/summary_agent/<user>/article_summaries/<slug>_output.md"
  },
  "metadata": {
    "source": "mp.weixin.qq.com",
    "url": "https://...",
    "saved_at": "2026-02-23 17:58",
    "tags": ["标签1", "标签2"],
    "keywords": ["关键词1", "关键词2"]
  }
}
```

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 写入/追加 | 正常保存由 `agents/summary-agent/run.sh --save-from-batch` 编排并调用内部 append；`--append-entry` 仅是受支持的低层手动入口。文件复制发生在 index append 之前，两者不是一个原子事务 |
| 读取 | `search-knowledgebase` skill, `agents/summary-agent/src/embedding.py`（`--check-url`、`--verify-url-vector`、`--list-article-records`、`--list-keywords`、`--build`、`--add`、`--search`） |

**不变量**:
1. **Source of truth**: index.json 是文章列表的唯一真相源，其他存储（embeddings、summaries）派生自它
2. **条目顺序稳定**: 新文章追加到末尾，不重排已有条目
3. **slug 唯一**: 每个条目的 slug（从 `output.summary_file_path` 派生）在数组中唯一
4. **canonical URL 唯一**: `metadata.url` 按 `embedding.py` 的 `canonicalize_url()` 去重；现代微信 `/s/<id>` 用 path 身份并去掉 query/fragment，完整的旧式 `/s?__biz=...&mid=...&idx=...&sn=...` 保留这四个身份字段，其他 URL 去掉常见 tracking 参数后比较。完整微信规则见 [ADR-014](../../../docs/adr/014-preserve-legacy-wechat-article-identity.md)
5. **路径有效**: `input.article_file_path` 和 `output.summary_file_path` 指向存在的文件

**维护方式**: `summarize-article` skill 把 scratch 交给 `run.sh --save-from-batch`；该入口保存文章与摘要、追加 index，并按选项更新 embedding 与项目表。

---

## 1.1. Version 1 文章目录 JSONL（派生只读视图）

**入口**: `agents/summary-agent/run.sh --list-article-records --user <user>`
**格式**: JSON Lines；第一行 header，后续每个 `index.json` 条目一行
**消费者**: 需要枚举完整文章的跨仓只读工具（首个消费者是 AI Radar 的手动 KB 归档导入）和操作员诊断

### 权威与边界

`index.json` 仍是文章集合与顺序的权威，manifest 定义向量行与 slug 的对应，`vectors.npy` 承载实际向量。本 JSONL 只是 CLI 在同一次加锁读取中对这三层做的版本化投影，不是第二份 store，也不是增量变更流。

外部消费者必须通过稳定的 `run.sh --list-article-records` 入口读取，不直接打开 `index.json` / manifest / `vectors.npy`，也不 import `embedding.py` 的私有 loader。这个边界让路径解析、URL canonicalization、slug 对齐和损坏数据的状态语义留在数据 owner 一侧；存储布局可以在不改外部消费者的情况下演化。消费者自己拥有业务过滤与导入策略；producer 不隐藏非微信、不完整或损坏的 index 行。

**适用边界**:

- 适用于有界的、按需全量盘点：跨仓 reconciliation、手动归档导入、发布前目录健康诊断。命令不需要 OpenAI key。
- 不用于普通知识库检索（用 `search-knowledgebase` skill / `--search`）、单 URL 向量恢复判定（用 `--verify-url-vector`）、写入或修复 store，也不是长驻订阅接口。
- `SUMMARY_AGENT_KB_ROOT` 是测试和隔离代码 worktree 的显式数据根 override；它不改变锁文件仍落在当前 checkout `tmp/` 的事实。因此，多个 checkout 指向同一 KB root 时不共享锁命名空间；只可在已排除其它写者的手动只读场景使用，不得把它当作跨 worktree 并发协调机制。

Header schema：

```json
{
  "record_type": "catalog",
  "schema_version": 1,
  "user": "dong_lin",
  "index_rows": 3448,
  "manifest_rows": 3448,
  "vector_rows": 3448,
  "vector_ndim": 2,
  "vector_dim": 1536,
  "expected_vector_dim": 1536,
  "alignment_status": "exact"
}
```

Article record schema：

```json
{
  "record_type": "article",
  "schema_version": 1,
  "user": "dong_lin",
  "kb_slug": "article-slug",
  "title": "文章标题",
  "url": "https://mp.weixin.qq.com/s/example",
  "canonical_url": "https://mp.weixin.qq.com/s/example",
  "source": "公众号名",
  "saved_at": "2026-02-10 12:00",
  "tags": ["视频生成"],
  "keywords": ["Seedance"],
  "article_file_path": "/absolute/path/article.md",
  "summary_file_path": "/absolute/path/article-slug_output.md",
  "entry_status": "ok",
  "file_status": "ok",
  "vector_status": "ok"
}
```

### 字段语义

| Header 字段 | 语义 |
|---|---|
| `record_type` / `schema_version` / `user` | 固定记录类型、契约版本和被读取的 user；消费者必须拒绝未知版本或 user 不匹配。 |
| `index_rows` | 权威 index 条目数，也是完整流应输出的 article record 数。 |
| `manifest_rows` / `vector_rows` | manifest slug 数与向量数组第一维长度；数据不存在时为 `0`。 |
| `vector_ndim` / `vector_dim` / `expected_vector_dim` | 实际数组维数、二维数组的实际宽度，以及当前 producer 要求的宽度；无 vectors 或非二维时 `vector_dim=0`。 |
| `alignment_status` | `exact`：manifest slugs 与 index slugs 顺序和内容相同；`append_only`：index 只在 manifest 已知前缀后追加了条目；`mismatch`：manifest 缺失、重排、删除或其它不一致。该字段不单独证明 vector 行数一致。 |

| Article 字段 | 语义 |
|---|---|
| `record_type` / `schema_version` / `user` | `record_type` 固定为 `article`；版本与 user 必须与 header 一致。 |
| `kb_slug` | 从 index 记录的 `output.summary_file_path` 派生的 Summary Agent KB slug，不是外部数据库的 ID。 |
| `title` / `url` / `source` / `saved_at` / `tags` / `keywords` | 来自 index 条目；标量异常类型会被字符串化，list 字段只保留字符串元素，并由 `entry_status=invalid` 显式报告原条目不合法。 |
| `canonical_url` | producer 用入库同一套 `canonicalize_url()` 计算的 URL 身份；消费者不自行重实现规则。 |
| `article_file_path` / `summary_file_path` | 相对 store 路径按当前 `KB_ROOT` 解析后的绝对路径；空或异常输入保留为空字符串或对应 resolved 路径。 |
| `entry_status` / `file_status` / `vector_status` | 条目结构、引用文件存在性与向量可用性是三个独立诊断面，不能用其中一个代替另两个。 |

### 状态枚举

| 字段 | 值 | 含义 |
|---|---|---|
| `entry_status` | `ok` | 条目是 object，`input` / `output` / `metadata` 是 object，slug 非空，title、两个路径、URL、source、saved_at 是非空字符串，tags 与 keywords 是字符串列表。 |
| `entry_status` | `invalid` | 不满足上述结构；该行仍输出，便于对账而不是静默丢失。 |
| `file_status` | `ok` | 两个 resolved path 在读取时都是普通文件。 |
| `file_status` | `article_missing` / `summary_missing` / `both_missing` | 原文、摘要或两者不是存在的普通文件。 |
| `vector_status` | `ok` | 全局 alignment 为 `exact`，vectors 是宽度 1536 的二维数组，当前行存在、全部有限且非全零。 |
| `vector_status` | `alignment_mismatch` | 全局 alignment 不是 `exact`；此时所有 article 行都不可按位置消费向量。通常的 manifest 或 vectors 缺失会先落入此状态。 |
| `vector_status` | `vector_shape_mismatch` | alignment 已为 `exact`，但 vectors 不是二维 1536 宽，或当前 index 行越过现有 vector 行数。 |
| `vector_status` | `zero_or_nonfinite` | 当前向量行全零或含非有限值。 |

### 可消费判定

完整目录消费者必须同时核对：

1. 进程成功退出，header 的 `record_type=catalog`、`schema_version=1` 且 `user` 与请求一致。退出码 `0` 只表示已完成检查；含有非 `ok` 状态的目录仍会成功退出。
2. `alignment_status=exact`、`index_rows == manifest_rows == vector_rows`、`vector_ndim=2` 且 `vector_dim == expected_vector_dim == 1536`。`alignment_status=exact` 本身只检查 index/manifest slug 列表，不代替行数与 shape 检查。
3. 后续恰有 `index_rows` 条 article record，每条 `record_type=article`，且 `schema_version` 和 `user` 与 header 一致。
4. 要消费完整文章的单条记录必须同时满足 `entry_status=file_status=vector_status=ok`。其它行可用于审计和对账，不得当作完整文章导入。

`file_status=ok` 只证明命令检查当下两个路径是文件，不证明内容 schema、可解码性，也不冻结命令退出后的文件字节。需要读取文件内容的消费者应在本次有界导入中立即打开，并把读取或解码失败作为本次导入失败；要求更新快照时重新运行命令。

### 不变量

1. 命令持有当前 checkout 下 `tmp/summary_agent_<user>.lock` 的 per-user `fcntl` 锁读取 index、manifest、vectors 并检查引用路径，输出代表同一锁内观察。
2. `schema_version` 的字段和语义不可原地改变；不兼容演化切换新版本。
3. 流按 `index.json` 稳定顺序包含每个 index 条目。Malformed 条目也输出一行并标记 `entry_status=invalid`，不会静默消失。
4. 这是派生读取面，不是新的 source of truth；写入仍只通过本文件其余章节登记的 owner 完成。

---

## 2. `<user>/article_summaries/<slug>_output.md` — 结构化摘要

**路径**: `data/summary_agent/<user>/article_summaries/<slug>_output.md`
**格式**: Markdown，遵循 `summary_agent_design.md` 第 3 节定义的模块结构

**Schema**: Summarizer 的结构校验按模块名识别标题，不依赖 emoji。必需模块是 `文章概况`、`独特亮点`、`可动手实践`、`可复用认知`、`关键词与分类标签`（也接受分开的 `关键词` + `分类标签`）和 `价值判断`；`产品方向洞察` 可按内容条件出现。下游 embedding 文本提取目前更严格：`_extract_overview()` 依赖精确的 `### 📋 文章概况` 标题，因此保存后的摘要仍须保留这个 emoji 形态；在消费者放宽前不能只依据 Summarizer 校验通过就删除它。

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 写入 | `summarize-article` skill 通过 `run.sh --save-from-batch` 保存 scratch 输出 |
| 读取 | `search-knowledgebase` skill（展示搜索结果）, `agents/summary-agent/src/embedding.py`（提取概况和适合场景用于 embedding text） |

**不变量**:
1. **一对一**: 每个 index.json 条目对应恰好一个 `_output.md` 文件
2. **概况可提取**: `### 📋 文章概况` 章节存在且第一行非空（`agents/summary-agent/src/embedding.py` 的 `_extract_overview` 依赖此格式）
3. **适合场景可提取**: 价值判断章节中 `**适合场景**:` 行存在且非空（`agents/summary-agent/src/embedding.py` 的 `_extract_suitable_scenarios` 依赖此格式）

**维护方式**: 由 `summarize-article` skill 生成 scratch，再交给 `run.sh --save-from-batch` 写入最终路径。

---

## 3. `<user>/embeddings/vectors.npy` — 嵌入向量

**路径**: `data/summary_agent/<user>/embeddings/vectors.npy`
**格式**: NumPy float32 数组，shape `(N, 1536)`

**Schema**: 第 i 行对应 `vectors_manifest.json` 中第 i 个 slug 的 embedding 向量。维度 1536 = `text-embedding-3-small` 模型输出。

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 写入 | `agents/summary-agent/src/embedding.py --build`, `agents/summary-agent/src/embedding.py --add` |
| 读取 | `agents/summary-agent/src/embedding.py` 的 `--search`、`--verify-url-vector` 与 `--list-article-records` |

**不变量**:
1. **行数匹配 manifest**: `vectors.shape[0] == len(manifest["slugs"])`
2. **非零向量有效**: 非零行是有效的 embedding 向量；零行表示尚未 embed 的占位
3. **始终与 manifest 同步写入**: `_save_vectors` 在同一调用中连续写入 `vectors.npy` + `vectors_manifest.json`（非原子操作，不一致时由 `_check_alignment` 检测并触发重建）

**维护方式**: 通过 `_save_vectors(emb_dir, vectors, slugs)` 保证与 manifest 同步。

---

## 4. `<user>/embeddings/vectors_manifest.json` — 向量对齐清单

**路径**: `data/summary_agent/<user>/embeddings/vectors_manifest.json`
**格式**: JSON

**Schema**:
```json
{"slugs": ["slug1", "slug2", ...]}
```

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 写入 | `agents/summary-agent/src/embedding.py`（通过 `_save_vectors`） |
| 读取 | `agents/summary-agent/src/embedding.py`（通过 `_load_vectors`） |

**不变量**:
1. **与 vectors.npy 同步**: manifest 中 slug 数量 == `vectors.npy` 行数
2. **记录 vectors.npy 的行含义**: `slugs[i]` 是 `vectors.npy` 第 i 行对应的文章 slug
3. **与 index.json 对齐时一致**: 当 alignment == "exact" 时，manifest slugs == index slugs（顺序和内容）

**维护方式**: 由 `_save_vectors` 与 `vectors.npy` 一起写入。`_check_alignment` 在每次 `--add` / `--search` 时校验 manifest 与 index 的一致性，不一致时触发 `_incremental_rebuild`。

---

## 5. `<user>/persona.md` — 用户画像

**路径**: `data/summary_agent/<user>/persona.md`
**格式**: Markdown，schema 定义在 `summary_agent_design.md` 第 4.2 节

**Schema**: 包含以下章节：
- `# 用户画像`
- `## 长期目标`
- `## 身份层级`（含 `### 层级 1（主要）` 等子章节）

每个层级包含：关注领域、不关注领域、"可操作"的判断标准。

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 读取 | `summarize-article` skill（画像过滤） |
| 写入 | 用户手动编辑 |

**不变量**:
1. **每用户一份**: 每个用户目录下恰好一个 `persona.md`
2. **影响摘要内容**: 画像决定 `summarize-article` 的 primary/secondary 分层标记

**维护方式**: 用户手动更新。当用户阶段发生变化时，调整身份层级顺序。

---

## 6. `articles/<slug>.md` — 文章原文

**路径**: `data/summary_agent/articles/<slug>.md`
**格式**: Markdown（从原始来源抓取并转换）

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 写入 | `summarize-article` skill 编排 `run.sh --save-from-batch`，由该入口把 batch scratch 复制到最终路径 |
| 读取 | `summarize-article` skill（重新分析时读取本地文件） |

**不变量**:
1. **跨用户共享**: 文章原文存放在 `data/summary_agent/articles/`（不在 `<user>/` 下），多用户共享同一份原文
2. **slug 命名**: 文件名 = slug + `.md`，slug 规则见 CLAUDE.md Conventions
3. **index 引用有效**: 每个 index.json 条目的 `input.article_file_path` 指向一个存在的原文文件

**维护方式**: Summarizer 只在 batch 目录生成 scratch；`summarize-article` skill 把它交给 `run.sh --save-from-batch`，由后者保存到最终路径并追加 index。

---

## 7. `tags.md` — 标签词汇表

**路径**: `agents/summary-agent/docs/tags.md`（注意：在代码仓库中，不在 `data/` 子模块中）
**格式**: Markdown 表格，按维度组织

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 读取 | `summarize-article` skill（标签分配时查阅词汇表） |
| 写入 | 正常保存路径在 `run.sh --save-from-batch` 内归一化标签；确需新增词汇时同步更新本文件 |

**不变量**:
1. **标签归维度**: 每个标签归属一个维度（构建、评测、进化、应用方向）
2. **标签复用优先**: 新增标签前必须检查已有标签能否覆盖
3. **归一化成功时与 index.json 一致**: `tag_normalization.normalization_failed=false` 的保存结果中，`metadata.tags` 已复用或登记到此词汇表；外部 embedding / 分类依赖失败时保存会 best-effort 继续并保留原 tag，此时消费者不得假定词表闭包

**维护方式**: `summarize-article` skill 通过 `run.sh --save-from-batch` 归一化 metadata tags；新增标签时同步更新此文件。保存收据的 `tag_normalization.normalization_failed=true` 表示本次没有建立完整闭包，需要按 warning 排障，不能据“文章已保存”反推词表已同步。

---

## 8. `<user>/open_source_projects.json` — 开源项目追踪

**路径**: `data/summary_agent/<user>/open_source_projects.json`
**格式**: JSON 数组

**Schema** (每个条目):
```json
{
  "name": "项目名",
  "url": "https://github.com/...",
  "description": "一句话描述项目功能和与用户项目的关联",
  "source_url": "发现该项目的信息来源（微信文章链接、HTTP 链接、本地文档路径等）",
  "tags": ["tag1"],
  "keywords": ["keyword1", "keyword2"],
  "saved_at": "2026-03-07 15:00"
}
```

**读写方**:
| 操作 | 执行者 |
|------|--------|
| 写入/追加 | 正常保存由 `run.sh --save-from-batch` 从 metadata projects 自动追加；也可显式调用 `--add-project` |
| 读取 | `search-knowledgebase` skill, `agents/summary-agent/src/embedding.py --list-projects` |

**不变量**:
1. **url 全局唯一**: `url` 是去重依据，不允许重复
2. **url 可复用**: `url` 必须非空，且指向用户可直接复用的代码、包、仓库、gist、模型/数据集或 skill 来源；产品官网、新闻、论文、微信文章和普通文档页不进入本文件
3. **tags 校验边界**: 正常 `--save-from-batch` 路径会按 `tags.md` 归一化；低层 `--add-project` 只校验 `list[str]`，不保证词表闭包
4. **source_url 仅供追溯**: 不做引用完整性校验

**维护方式**: `summarize-article` skill 把 metadata projects 交给 `run.sh --save-from-batch` 自动追加；也可通过 `--add-project` 手动添加，手动调用方自行负责受控 tag。

