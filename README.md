# ELK-Agent 智能体：架构设计与实现全手册
**版本:** v1.1 (2026-03-26)

---

## 1. 项目背景与愿景
将传统的 ELK (Elasticsearch, Logstash, Kibana) 从“被动存储堆栈”升级为“主动智能 Agent”。
- **核心目标：** 让运维人员通过自然语言进行日志检索、故障根因分析（RCA）及自动化运维。
- **关键特性：** 语义理解、多维聚合、自主工具调用、RAG 增强诊断。

---

## 2. 技术选型 
| 组件           | 选型                            | 作用                                    |
| :------------- | :------------------------------ | :-------------------------------------- |
| **推理大脑**   | OpenAI SDK + OpenRouter/OpenAI 兼容接口 | 逻辑推理、工具选择、诊断输出 |
| **存储/检索**  | Elasticsearch 8.10+             | 日志检索、知识库 BM25+向量混合检索 (RRF+MMR) |
| **Agent 编排** | 自研 ToolCallingOrchestrator    | `tool_choice="auto"` 多轮工具调用与回喂 |
| **语言/环境**  | Python 3.10+                    | 核心开发语言                            |
| **其他** | Redis | 存储短期记忆 |

---

## 3. 架构设计图 (4-Layer Model)

![image-20260330133014062](C:\Users\25513\AppData\Roaming\Typora\typora-user-images\image-20260330133014062.png)

### 3.1 感知层 (Perception)
- **数据源：** 应用日志、系统指标 (Metrics)、链路追踪 (Tracing)。
- **处理：** 实时向量化并存入 ES，支持 `dense_vector` 字段。

### 3.2 认知层 (Reasoning)
- **工具选择：** 主链路由模型在 `queryByTimeRange/queryByRequestId/executeDsl/queryKnowledgeBaseHybrid` 中自动选择。
- **Text-to-DSL：** 通过 `executeDsl` 工具让模型生成 DSL（字符串），服务端校验后执行。
- **自我修正：** 当 `executeDsl` 返回 `DSL_FIELD_TYPE_MISMATCH/DSL_NOT_ALLOWED/INVALID_DSL_JSON` 时，编排器最多重写 2 次。

### 3.3 行动层 (Action)
- **MCP 工具箱（当前已接入）：**
    - `queryByTimeRange`: 时间范围日志检索（绝对时间或 `lastMinutes/lastHours`）。
    - `queryByRequestId`: `requestId + serviceName` 精确链路查询。
    - `executeDsl`: 执行经 allowlist/type-check 校验的 DSL。
    - `queryKnowledgeBaseHybrid`: 知识库 BM25+向量混合召回，先 RRF 融合再 MMR 重排。

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

---

## 5. 项目目录结构建议

> 仓库根目录名为 `AGENT-ELK`；下列树形与当前仓库一致。整体变更时请同步更新本节与 [`docs/README.md`](docs/README.md)。

```text
AGENT-ELK/
├── docs/                 # 扩展文档（架构 / 路线图 / 指南 / 参考）
│   ├── README.md         # 文档地图与维护约定
│   ├── architecture/
│   ├── roadmap/
│   ├── guides/
│   └── reference/
├── app/
│   ├── prompts/          # Phase 2.2：运维对话提示词仓库（与 agent 代码解耦）
│   │   ├── __init__.py
│   │   ├── templates.py  # SYSTEM_PROMPT、ANALYSIS_PROMPT_TMPL、INTENT_EXTRACT_TMPL
│   │   └── tool_calling_prompts.py # Tool-calling + Memory 提示词（含 RAG/DSL/记忆摘要与关键提取模板）
│   ├── memory/           # Phase 2.5：对话历史 + 小模型摘要压缩；Phase 2.7：config 解耦阈值；Phase 2.9：Redis 存储层
│   │   ├── __init__.py
│   │   ├── config.py     # TURN_LIMIT、TOKEN 阈值、SUMMARY_MODEL、MEMORY_STORAGE_PATH 等默认项
│   │   ├── manager.py    # MemoryManager / memory_bus（Redis Hash 读写与摘要压缩）
│   │   └── redis_store.py # RedisMemoryStore（Hash 字段级存取封装）
│   ├── core/             # Agent 核心逻辑（主链路：tool-calling）
│   │   ├── config.py     # Phase 2.7：MAIN_LLM_TEMPERATURE、DEFAULT_LLM_MODEL
│   │   └── agent_brain.py # ELKAgent：system prompt + tool-calling 编排入口
│   ├── tools/            # 历史工具层（MCP 上线后主链路通常不直接调用）
│   │   ├── config.py     # Phase 2.7：ES_DEFAULT_URL、SEARCH_LOGS_DEFAULT_TOP_K、kNN 候选参数
│   │   ├── es_client.py  # ESNervousSystem / es_node：连接、init_index、push_log
│   │   └── log_tools.py  # 历史 ES 工具实现（保留兼容）
│   ├── schema/           # ES Mapping 定义 (Json/Python)
│   │   └── log_schema.py # intelligent_logs_v1：message 使用 IK ik_smart
│   ├── database/         # 知识库数据接入层：Mapping / Loader / Embedding
│   │   ├── schema.py      # 知识库索引 `elk_agent_knowledge_base` Mapping
│   │   ├── loader.py      # CSV 入库脚本（含索引初始化与 bulk）
│   │   └── vector_service.py # Embedding 生成
│   ├── mcp_client/       # Agent -> MCP Server HTTP 客户端
│   ├── mcp_server/       # FastAPI MCP Server 与工具实现
│   ├── tool_calling/     # 工具注册与编排器（主链路）
│   └── utils/            # 通用工具（含 embedding 工具）
│       └── embedding.py  # EmbeddingGenerator / embedder（OpenAI 兼容嵌入）
├── app/data/             # （新增）原始数据目录（例如 elk_error_logs_100.csv）
├── config/               # （遗留）memory_state.json；Phase 2.9 起由 Redis 替代（保留兼容回退）
├── .env                  # 本地密钥与覆盖项（勿提交仓库；与 settings 对应）
├── check_env.py          # 环境检查：ES 连通与 init_index（python check_env.py）
├── seed_demo_logs.py     # Phase 1.2：18 条中文多场景日志 + 向量写入
├── ingest_mock_logs.py   # Phase 1.2：8 条英文关联场景（支付/网关/DB）+ 向量写入
├── main.py               # Phase 2.1.3：统一 CLI 入口（python main.py）
├── requirements.txt      # Python 依赖（与 §2 对齐）
├── README.md             # 本手册
└── .cursor/rules/        # Cursor 项目规则
```

## 6. 配置与环境变量（运行时环境变量）

- **运维对话提示词（Phase 2.2）：** `app/prompts/templates.py` 维护基础对话模板（`SYSTEM_PROMPT/ANALYSIS_PROMPT_TMPL/INTENT_EXTRACT_TMPL`），`app/prompts/tool_calling_prompts.py` 统一维护 tool-calling + memory 相关提示词（RAG 执行约束、DSL 失败降级提示、`executeDsl` 工具描述、记忆摘要模板、关键内容提取模板）；业务代码仅负责组装与调用。
- **Phase 2.7（分布式参数）：** 各模块默认数值集中在同级 `config.py`：`app/memory/config.py`（轮数/Token 压缩阈值、摘要模型、记忆落盘相对路径、硬上限默认）、`app/core/config.py`（主模型温度与默认模型 ID）、`app/tools/config.py`（`ES_URL` 回退地址、检索 `top_k`、kNN `num_candidates` 计算参数）。环境变量（如 `MEMORY_TOKEN_LIMIT`、`ES_URL`）仍可覆盖对应行为。

安装依赖：`python -m pip install -r requirements.txt`（Windows PowerShell 5.x 中请用分号串联命令，避免使用 `&&`）。

## 6.0 MCP Server（FastAPI）与 Agent 接入

本仓库新增 **Elasticsearch MCP Server**（HTTP 形态，FastAPI），用于统一暴露日志查询工具。**Agent 侧不再直连 ES**，实时日志检索通过 MCP Server 完成。

### 6.0.1 MCP Server 启动

在仓库根目录执行：

```bash
python -m uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8000
```

### 6.0.2 MCP Server 环境变量（服务端）

- `MCP_ES_URL`：MCP Server 连接 ES 的地址（缺省回退 `ES_URL`，再回退 `http://localhost:9200`）。
- `MCP_ES_AUTHORIZATION`：MCP→ES 的默认鉴权 Header（如 `Basic ...` / `ApiKey ...` / `Bearer ...`）；当请求未带 Authorization 时作为兜底。
- `MCP_HERA_BASE_URL`：Hera 索引配置查询 API 基址（仅用于 `queryByIamId`）。

### 6.0.3 Agent 环境变量（客户端）

- `MCP_SERVER_URL`：MCP Server 地址，默认 `http://127.0.0.1:8000`。
- `MCP_AUTHORIZATION`：Agent→MCP 的鉴权 Header（只走 Header，不透传 username/password）。

### 6.0.4 Agent 执行流（检索链路）

- **Tool-calling loop（推荐主链路）**：由 OpenRouter LLM 自动选择工具，程序负责调用 MCP 并回喂结果，直到模型输出最终 RCA。
- 可用工具：`queryByTimeRange` / `queryByRequestId` / `executeDsl` / `queryKnowledgeBaseHybrid`
- `queryByTimeRange` 支持两种时间入参：`startTime + endTime`（绝对时间）或 `lastMinutes/lastHours`（相对时间，服务端按真实当前时间换算），用于稳定“最近日志”检索。
- `queryByRequestId` 当前实现为直连 ES：必填 `requestId + serviceName`，可选 `indexName/level/size`。
- 当工具返回 `errors` 时，Agent 会在最终答复前优先透传工具原始错误（tool/code/message），降低模型误判概率。
- `executeDsl` 采用 **DSL 双层防线**：
  - 生成前：工具描述明确要求按目标索引实时 mapping 生成字段与子字段。
  - 执行前：MCP Server 拉取并缓存 mapping，按字段类型做语义校验（如 `term/match/range/aggs.terms`）。
  - 失败后：若返回 `DSL_FIELD_TYPE_MISMATCH` / `DSL_NOT_ALLOWED` / `INVALID_DSL_JSON`，编排器会把错误回喂模型触发 DSL 重写，最多 2 次。
- `queryKnowledgeBaseHybrid`：知识库混合召回（BM25 + 向量 + RRF + MMR），用于在日志事实之后补充 SOP/案例证据，提升 RCA 准确率与可执行性。
- 核心原则：**工具选择权在模型**，代码不再做复杂路由器来决定调用哪个工具
## 7. 文档体系

| 位置 | 说明 |
|------|------|
| 本文件 `README.md` | 全手册总览（背景、选型、架构、路线图、目录、配置） |
| [`docs/README.md`](docs/README.md) | 文档索引：各子目录与主手册章节对应关系及维护约定 |
| `docs/architecture/` | §3 架构深化（图、流、边界） |
| `docs/roadmap/` | §4 阶段任务与验收清单细化 |
| `docs/guides/` | 实施与运维操作指南 |
| `docs/reference/` | Schema、DSL、工具契约等参考材料 |
