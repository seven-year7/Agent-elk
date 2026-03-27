# ELK-Agent 智能体：架构设计与实现全手册
**版本:** v1.0 (2026-03-24)
**角色:** Agent 架构师 (Gemini) & 首席开发工程师 (User)

---

## 1. 项目背景与愿景
将传统的 ELK (Elasticsearch, Logstash, Kibana) 从“被动存储堆栈”升级为“主动智能 Agent”。
- **核心目标：** 让运维人员通过自然语言进行日志检索、故障根因分析（RCA）及自动化运维。
- **关键特性：** 语义理解、多维聚合、自主工具调用、RAG 增强诊断。

---

## 2. 技术选型 (Stack)
| 组件           | 选型                            | 作用                                    |
| :------------- | :------------------------------ | :-------------------------------------- |
| **推理大脑**   | Gemini 3 Flash / GPT-4o         | 逻辑推理、意图识别、DSL 生成            |
| **存储/检索**  | Elasticsearch 8.10+             | 向量存储、BM25 全文检索、混合搜索 (RRF) |
| **Agent 框架** | LangGraph / LangChain           | 状态机管理、ReAct 循环实现              |
| **向量模型**   | OpenRouter 路由 `openai/text-embedding-3-small`（或同维替代） | 日志语义向量化（OpenAI 兼容 Embeddings API） |
| **语言/环境**  | Python 3.10+                    | 核心开发语言                            |

---

## 3. 架构设计图 (4-Layer Model)

### 3.1 感知层 (Perception)
- **数据源：** 应用日志、系统指标 (Metrics)、链路追踪 (Tracing)。
- **处理：** 实时向量化并存入 ES，支持 `dense_vector` 字段。

### 3.2 认知层 (Reasoning)
- **意图路由：** 区分“统计需求”、“单点查错”还是“趋势预测”。
- **Text-to-DSL：** 将自然语言转义为复杂的 ES Query (DSL)。
- **自我修正：** 若查询失败，Agent 根据报错信息自动重写 Query。

### 3.3 行动层 (Action)
- **工具箱 (Toolkits)：**
    - `search_logs`: 语义+关键字混合检索。
    - `get_metrics`: 调用 Prometheus 或 ES 指标聚合。
    - `alert_notify`: 触发 Webhook 告警。

### 3.4 交互层 (Interaction)
- **接口：** 命令行工具 (CLI)、Web 聊天窗口或 Kibana 插件。

---

## 4. 实施阶段与功能验证 (Roadmap)

### 阶段一：智能检索 (MVP)
* **任务：** 建立支持向量的 Mapping，跑通“自然语言 -> DSL -> 结果回显”。
* **关键点：** Prompt 中需包含 Index Schema 的定义。
* **验证：** 输入“查找最近一小时的 500 错误”，应准确生成时间范围过滤。

### 阶段二：根因分析 (RCA)
* **任务：** 引入 ReAct 循环，实现跨索引关联。
* **关键点：** Agent 发现 Error 后，自动去查对应的 `trace_id` 相关日志。
* **验证：** 输入“支付延迟高”，Agent 回复：“发现 DB 响应慢，且对应时段线程池满”。

### 阶段三：预测与预防 (Ops-Agent)
* **任务：** 结合历史数据进行异常检测（Anomaly Detection）。
* **关键点：** 使用 ES 的机器学习 API 或 LLM 趋势分析。

---

## 5. 项目目录结构建议

> 仓库根目录名为 `Myself`；下列树形与当前仓库一致。整体变更时请同步更新本节与 [`docs/README.md`](docs/README.md)。

```text
Myself/
├── docs/                 # 扩展文档（架构 / 路线图 / 指南 / 参考）
│   ├── README.md         # 文档地图与维护约定
│   ├── architecture/
│   ├── roadmap/
│   ├── guides/
│   └── reference/
├── app/
│   ├── config/           # 核心运行配置 +（规划）NL→DSL 等模板，见 §6
│   │   ├── __init__.py
│   │   ├── prompt_templates.py
│   │   └── settings.py
│   ├── prompts/          # Phase 2.2：运维对话提示词仓库（与 agent 代码解耦）
│   │   ├── __init__.py
│   │   ├── templates.py  # SYSTEM_PROMPT、ANALYSIS_PROMPT_TMPL、INTENT_EXTRACT_TMPL
│   │   └── router_prompts.py # Phase 2.15：意图路由提示词（OPS_QUERY / GENERAL_CHAT）
│   ├── memory/           # Phase 2.5：对话历史 + 小模型摘要压缩；Phase 2.7：config 解耦阈值；Phase 2.9：Redis 存储层
│   │   ├── __init__.py
│   │   ├── config.py     # TURN_LIMIT、TOKEN 阈值、SUMMARY_MODEL、MEMORY_STORAGE_PATH 等默认项
│   │   ├── manager.py    # MemoryManager / memory_bus（Redis Hash 读写与摘要压缩）
│   │   └── redis_store.py # RedisMemoryStore（Hash 字段级存取封装）
│   ├── core/             # Agent 核心逻辑 (ReAct 循环)
│   │   ├── config.py     # Phase 2.7：MAIN_LLM_TEMPERATURE、DEFAULT_LLM_MODEL
│   │   ├── gateway.py    # Phase 2.14：多级智能网关（关键词直通 / 相似度阈值 / 普通聊天）
│   │   ├── router.py     # Phase 2.24：单模型意图路由（关键词未命中时 LOG/CHAT，取消投票）
│   │   └── agent_brain.py # ELKAgent：kNN 日志上下文 + LLM 诊断；可 python -m app.core.agent_brain
│   ├── tools/            # 工具集 (ES 客户端, DSL 生成器)
│   │   ├── config.py     # Phase 2.7：ES_DEFAULT_URL、SEARCH_LOGS_DEFAULT_TOP_K、kNN 候选参数
│   │   ├── es_client.py  # ESNervousSystem / es_node：连接、init_index、push_log
│   │   └── log_tools.py  # search_logs_tool / es_search_tool：kNN 语义检索日志
│   ├── schema/           # ES Mapping 定义 (Json/Python)
│   │   └── log_schema.py # intelligent_logs_v1：message 使用 IK ik_smart
│   ├── database/         # （新增）数据接入与入库层：Mapping / Loader / Vector Service（新骨架）
│   │   ├── schema.py      # 定义 ES Mapping（索引结构）（与 app/schema/ 目前存在职责重叠，后续可迁移统一）
│   │   ├── loader.py      # 负责 CSV/PDF 的加载与清洗（占位骨架）
│   │   └── vector_service.py # 负责向量化（Embedding）与批量入库（占位骨架）
│   └── utils/            # Embedding 工具, 时间解析器
│       └── embedding.py  # EmbeddingGenerator / embedder（OpenAI 兼容嵌入）
├── app/data/             # （新增）原始数据目录（例如 elk_error_logs_100.csv）
├── config/               # （遗留）memory_state.json；Phase 2.9 起由 Redis 替代（保留兼容回退）
├── .env                  # 本地密钥与覆盖项（勿提交仓库；与 settings 对应）
├── check_env.py          # 环境检查：ES 连通与 init_index（python check_env.py）
├── seed_demo_logs.py     # Phase 1.2：18 条中文多场景日志 + 向量写入
├── ingest_mock_logs.py   # Phase 1.2：8 条英文关联场景（支付/网关/DB）+ 向量写入
├── main.py               # Phase 2.1.3：统一 CLI 入口（python main.py）
├── requirements.txt      # Python 依赖（与 §2 及 app/config 对齐）
├── README.md             # 本手册
└── .cursor/rules/        # Cursor 项目规则
```

## 6. 配置与环境变量（`app/config`）

- **运维对话提示词（Phase 2.2）：** `app/prompts/templates.py` 集中维护 **`SYSTEM_PROMPT`**、**`ANALYSIS_PROMPT_TMPL`**（占位符：`current_time`、`user_query`、`logs_context`）、**`INTENT_EXTRACT_TMPL`**（进阶意图抽取，预留）；`app/core/agent_brain.py` 仅负责组装与调用。
- **（规划）NL→DSL 等：** `app/config/prompt_templates.py` 仍预留用于自然语言转 Elasticsearch DSL、纠错等模板，与对话仓库分工。
- **核心配置：** `app/config/settings.py` 计划提供 `CoreSettings` 与 `get_core_settings()`，并在导入时通过 `python-dotenv` 加载仓库根目录下的 `.env`（若存在），再读取环境变量（以实现为准）。
- **Phase 2.7（分布式参数）：** 各模块默认数值集中在同级 `config.py`：`app/memory/config.py`（轮数/Token 压缩阈值、摘要模型、记忆落盘相对路径、硬上限默认）、`app/core/config.py`（主模型温度与默认模型 ID）、`app/tools/config.py`（`ES_URL` 回退地址、检索 `top_k`、kNN `num_candidates` 计算参数）。环境变量（如 `MEMORY_TOKEN_LIMIT`、`ES_URL`）仍可覆盖对应行为。

根目录 **`.env`** 当前约定如下（请填入真实值，**勿提交**密钥；若使用 Git，请将 `.env` 加入 `.gitignore`）：

| 环境变量 | 说明 |
|----------|------|
| `ES_URL` | Elasticsearch 节点地址；启用安全且走 TLS 时一般为 `https://主机:端口` |
| `ES_API_KEY` | ES API Key；**若已设置则优先使用**。使用 Basic 时请删除或清空无效的占位 Key，否则会先走 API Key 导致认证失败 |
| `ES_USERNAME` | 启用 Basic 认证时的用户名（如 `elastic`）；与 `ES_PASSWORD` 成对使用 |
| `ES_PASSWORD` | Basic 认证密码；**勿提交到 Git**，仅写在本地 `.env` |
| `ES_CA_CERTS` | （推荐）指向 PEM 格式的**集群或企业根 CA** 证书文件路径，用于校验自签名/内网 HTTPS |
| `ES_VERIFY_CERTS` | 是否校验 TLS 证书，默认 `true`；内网自签且暂无法导入 CA 时可设 `false`（**有中间人风险，勿用于生产**） |
| `OPENROUTER_API_KEY` | **向量嵌入（推荐）**：OpenRouter API Key，经 `https://openrouter.ai/api/v1` 调用兼容 OpenAI 的 Embeddings |
| `OPENROUTER_BASE_URL` | 可选；默认 `https://openrouter.ai/api/v1` |
| `OPENROUTER_HTTP_REFERER` | 可选；OpenRouter 建议的 `HTTP-Referer`（站点 URL） |
| `OPENROUTER_APP_NAME` | 可选；OpenRouter 建议的 `X-Title`（应用名） |
| `EMBEDDING_MODEL` | 嵌入模型 ID；OpenRouter 上多为 `厂商/模型`，例如 `openai/text-embedding-3-small`（与 `log_schema` 默认 1536 维一致） |
| `LLM_MODEL` | **对话模型**（`app/core/agent_brain.py`）；OpenRouter 上多为 `openai/gpt-4o-mini` 等；未设时回退 `DEFAULT_LLM_MODEL`，再回退 `openai/gpt-4o-mini` |
| `DEFAULT_LLM_MODEL` | `LLM_MODEL` 的别名回退（与部分文档一致） |
| `OPENAI_API_KEY` | **回退**：未配置 `OPENROUTER_API_KEY` 时使用，直连 OpenAI 或配合 `OPENAI_BASE_URL` 走其它兼容网关 |
| `OPENAI_BASE_URL` | 与 `OPENAI_API_KEY` 联用时的自定义基址（直连场景通常留空） |
| `MEMORY_SUMMARY_MODEL` | **Phase 2.5/2.6**：摘要用小模型（OpenRouter ID），默认 `xiaomi/mimo-v2-flash` |
| `MEMORY_HISTORY_THRESHOLD` | **轮数阈值**：`user+assistant` 消息条数超过即触发压缩（与 Token 阈值二选一满足即触发）；默认 `8` |
| `MEMORY_TOKEN_LIMIT` | **Phase 2.6**：历史消息估算 Token 超过即触发压缩（`≈ len/4` 字符）；默认 `3000` |
| `MEMORY_HARD_STOP_LIMIT` | **硬上限**：摘要+历史+本轮 system/检索模板估算 Token ≥ 此值则 **不调用主模型**；默认 `7000`（约 8k 窗口留余量） |
| `MEMORY_STATE_PATH` | 可选；遗留 JSON 路径（Phase 2.9 起主要使用 Redis），默认仓库根目录 `config/memory_state.json` |
| `REDIS_HOST` | **Phase 2.9**：Redis 主机地址；默认 `localhost` |
| `REDIS_PORT` | **Phase 2.9**：Redis 端口；默认 `6379` |
| `REDIS_DB` | **Phase 2.9**：Redis 数据库编号；默认 `0` |
| `SESSION_PREFIX` | **Phase 2.9**：Redis 存储前缀；Key 格式为 `SESSION_PREFIX + thread_id`，默认 `elk:agent:session:` |
| `MEMORY_TTL` | **Phase 2.9**：会话记忆 TTL（秒）；默认 `3600`（1 小时） |

后续在 `settings.py` 中实现时，仍可支持 `ELASTICSEARCH_URL`、`ELASTICSEARCH_API_KEY`、`DEFAULT_LLM_MODEL`、`LOG_LEVEL` 等扩展项及默认值。

安装依赖：`python -m pip install -r requirements.txt`（Windows PowerShell 5.x 中请用分号串联命令，避免使用 `&&`）。

## 6.0 MCP Server（FastAPI）与 Agent 接入

本仓库新增 **Elasticsearch MCP Server**（HTTP 形态，FastAPI），用于统一暴露日志查询工具。**Agent 侧不再直连 ES**，实时日志检索通过 MCP Server 完成。

### 6.0.1 MCP Server 启动

在仓库根目录执行：

```bash
python -m uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8000
```

### 6.0.2 MCP Server 环境变量（服务端）

| 环境变量 | 说明 |
|----------|------|
| `MCP_ES_URL` | MCP Server 连接 ES 的地址（缺省回退 `ES_URL`，再回退 `http://localhost:9200`） |
| `MCP_ES_AUTHORIZATION` | MCP→ES 的默认鉴权 Header（如 `Basic ...` / `ApiKey ...` / `Bearer ...`）；当请求未带 Authorization 时作为兜底 |
| `MCP_HERA_BASE_URL` | Hera 索引配置查询 API 基址（仅用于 `queryByIamId`） |

### 6.0.3 Agent 环境变量（客户端）

| 环境变量 | 说明 |
|----------|------|
| `MCP_SERVER_URL` | MCP Server 地址，默认 `http://127.0.0.1:8000` |
| `MCP_AUTHORIZATION` | Agent→MCP 的鉴权 Header（只走 Header，不透传 username/password） |

### 6.0.4 Agent 执行流（检索链路）

- **Tool-calling loop（推荐主链路）**：由 OpenRouter LLM 自动选择工具，程序负责调用 MCP 并回喂结果，直到模型输出最终 RCA。
- 可用工具：`queryByTimeRange` / `queryByRequestId` / `executeDsl`
- `queryByTimeRange` 支持两种时间入参：`startTime + endTime`（绝对时间）或 `lastMinutes/lastHours`（相对时间，服务端按真实当前时间换算），用于稳定“最近日志”检索。
- `queryByRequestId` 已改为直连 ES：必填 `requestId + serviceName`（匹配字段 `servicename`），可选 `indexName/level/size`，不再依赖 Hera。
- 当工具返回 `errors` 时，Agent 会在最终答复前优先透传工具原始错误（tool/code/message），降低模型误判概率。
- `executeDsl` 采用 **DSL 双层防线**：
  - 生成前：工具描述明确要求按目标索引实时 mapping 生成字段与子字段。
  - 执行前：MCP Server 拉取并缓存 mapping，按字段类型做语义校验（如 `term/match/range/aggs.terms`）。
  - 失败后：若返回 `DSL_FIELD_TYPE_MISMATCH` / `DSL_NOT_ALLOWED` / `INVALID_DSL_JSON`，编排器会把错误回喂模型触发 DSL 重写，最多 2 次。
- 核心原则：**工具选择权在模型**，代码不再做复杂路由器来决定调用哪个工具
## 6.1 模型连通性测试（小模型 vs 大模型）

当你遇到“**小模型一直报错/限流，但大模型正常**”时，先跑这个独立脚本，可快速判断是 **OpenRouter free 模型上游限流/额度** 还是 **本地代码/配置问题**。

运行方式：

```bash
python test/test_llm_connectivity.py
```

脚本会读取仓库根目录 `.env`（若存在）与环境变量，并分别测试：
- **小模型**：`MEMORY_SUMMARY_MODEL`（默认 `xiaomi/mimo-v2-flash`）
- **大模型**：`LLM_MODEL`（默认 `openai/gpt-4o-mini`，回退 `DEFAULT_LLM_MODEL`）

如何解读常见报错：
- **429 / Rate limit exceeded**：通常为 **free 小模型上游限流**（高峰期很常见）。脚本会尽量打印 `X-RateLimit-*` / `Retry-After` 相关响应头（若 SDK 暴露）。
- **402 / Spend limit exceeded**：通常为该 API Key 设置了花费上限（或额度不足），与模型本身无关。
- **两者都失败**：优先检查 `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL`（或回退的 `OPENAI_API_KEY` / `OPENAI_BASE_URL`）是否正确、网络是否可达。

## 6.2 Elasticsearch 证书专项测试（TLS）

当你怀疑是 ES 证书导致连接/查询失败时，可运行：

```bash
python test/test_es_tls_certificate.py
```

该脚本会执行两层验证：
- `requests` 握手层（快速判断证书链、CA、校验开关是否正确）
- `elasticsearch` SDK 层（与项目真实客户端参数一致，验证实际可用性）

依赖环境变量：`ES_URL`、`ES_VERIFY_CERTS`、`ES_CA_CERTS`、`ES_API_KEY`（或 `ES_USERNAME` + `ES_PASSWORD`）。

**Elasticsearch 索引与 IK：** `intelligent_logs_v1` 的 `message` 字段映射为 **IK `ik_smart`**，节点需安装 **`analysis-ik`** 插件。若该索引曾以其他 analyzer（如 `standard`）创建过，Elasticsearch **不会**自动改 mapping，需先在 Dev Tools 执行 `DELETE /intelligent_logs_v1`（会删数据）后再运行 `python check_env.py`。

**Phase 1.2（数据灌溉 / 记忆灌溉）：** 建议先 `python check_env.py` 检查 ES 与索引，再运行 `python seed_demo_logs.py`（**18 条** 中文场景）或 `python ingest_mock_logs.py`（**8 条** 英文关联场景）。写入字段需符合 `log_schema`。

**Phase 2.1（Agent 工具层）：** `app/tools/log_tools.py` 提供 `search_logs_tool`（别名 `es_search_tool`）用于日志检索（MCP 链路下通常由 `executeDsl`/`queryByTimeRange` 完成）。完整 Thought → Action → Observation 循环可在 `app/core` 用 LangGraph / 自研循环衔接。

**Phase 2.1（续，大脑与入口）：** `app/core/agent_brain.py` 中 **`ELKAgent`** 采用 MCP tool-calling 流程，由模型在 `queryByTimeRange / queryByRequestId / executeDsl` 间自主选择，再综合证据生成诊断。对话客户端优先 **`OPENROUTER_API_KEY`** + **`OPENROUTER_BASE_URL`**（默认 `https://openrouter.ai/api/v1`），否则使用 **`OPENAI_API_KEY`**（可选 **`OPENAI_BASE_URL`**）。**交互入口（Phase 2.1.3）：** **`python main.py`** — 运维对话式 CLI；亦可 `python -m app.core.agent_brain`（内置 REPL）；或在代码中 `from app.core.agent_brain import agent` 后调用 `agent.chat("你的问题")`。

**Phase 2.2（提示词解耦）：** 系统角色与用户侧「检索结果 + 问题」缝合模板迁至 **`app/prompts/`**，便于独立迭代提示词工程；`INTENT_EXTRACT_TMPL` 供后续结构化意图抽取使用。

**Phase 2.5（记忆架构与小模型级联）：** **`app/memory/manager.py`** 中 **`MemoryManager`** 维护多轮对话；超过 **`MEMORY_HISTORY_THRESHOLD`**（默认 8 条消息）时，用 **`MEMORY_SUMMARY_MODEL`**（默认 **`xiaomi/mimo-v2-flash`**）压缩旧轮，仅保留最近 2 条原文 + 摘要 system 段。**主模型**（`LLM_MODEL`）仍在 `agent_brain` 中做带检索上下文的推理；成功回复后向 `memory_bus` 写入「用户原话 + 助手结论」（不含整段 ES 模板），避免摘要语料膨胀。

**Phase 2.6（双指标、持久化与安全兜底）：** 在 2.5 基础上增加 **轮数 OR 估算 Token**（**`MEMORY_TOKEN_LIMIT`**，默认 3000）双触发摘要；**`config/memory_state.json`**（可 **`MEMORY_STATE_PATH`** 覆盖）持久化 `summary` / `history`，启动时自动加载；**`check_safety_overflow`** 在调用主模型前校验「摘要 + 历史 + 主 system + 本轮检索模板」估算 Token 是否低于 **`MEMORY_HARD_STOP_LIMIT`**（默认 7000），否则直接返回提示并 **不请求 API**，避免上下文过长导致报错崩溃。摘要失败时 **Trim** 最旧消息兜底。

**Phase 2.7（配置解耦）：** 上述默认值在代码中的「单一来源」分别为 **`app/memory/config.py`**、**`app/core/config.py`**、**`app/tools/config.py`**；`.env` 覆盖项与 §6 表格一致。

**Phase 2.9（Redis 结构化存储）：** 将短期记忆从本地 JSON 文件切换为 Redis Hash：每个会话（Session）使用 Key `elk:agent:session:{thread_id}` 隔离存取，并对话更新时写入 `summary`、`history`、`stats:token_count`、`meta:service`、`meta:updated_at` 等字段；同时设置会话 TTL（默认 `MEMORY_TTL=3600`），避免存储爆炸。


## 7. 文档体系

| 位置 | 说明 |
|------|------|
| 本文件 `README.md` | 全手册总览（背景、选型、架构、路线图、目录、配置） |
| [`docs/README.md`](docs/README.md) | 文档索引：各子目录与主手册章节对应关系及维护约定 |
| `docs/architecture/` | §3 架构深化（图、流、边界） |
| `docs/roadmap/` | §4 阶段任务与验收清单细化 |
| `docs/guides/` | 实施与运维操作指南 |
| `docs/reference/` | Schema、DSL、工具契约等参考材料 |
