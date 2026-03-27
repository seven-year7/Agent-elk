# 全量 BM / Hybrid 排查报告

## 1) 执行背景

- 目标提示词：`请检索最近两天 payment 服务的报错日志，先给出关键事实（时间分布、error_code 排名、高频 requestId），再结合知识库证据给出根因分析和可执行处置步骤；如果知识库无命中请明确说明并仅基于日志结论。`
- 目标：在 `--limit 0` 全量样本下，确认 BM25 真实命中情况，并与 MCP Hybrid 空命中现象做并排对照。

---

## 2) 步骤一：全量评测（limit=0）

### 命令

```bash
python test/eval_rag_business_recall.py --limit 0
```

### 输出摘要

- Top1 (`samples=100`)
  - BM25: `category=1.000 executable=1.000 consistency=0.920`
  - Vector: `category=1.000 executable=1.000 consistency=1.000`
  - Hybrid: `category=0.000 executable=0.000 consistency=0.000`
- Top3 (`samples=100`)
  - BM25: `category=1.000 executable=1.000 consistency=0.960`
  - Vector: `category=1.000 executable=1.000 consistency=1.000`
  - Hybrid: `category=0.000 executable=0.000 consistency=0.000`
- Top5 (`samples=100`)
  - BM25: `category=1.000 executable=1.000 consistency=0.960`
  - Vector: `category=1.000 executable=1.000 consistency=1.000`
  - Hybrid: `category=0.000 executable=0.000 consistency=0.000`

### 结论

- 全量条件下，离线 BM25/Vector 指标稳定为高召回；Hybrid 仍然全量为 0。

---

## 3) 步骤二：打印 BM25 命中明细

### 命令

```bash
python test/eval_rag_business_recall.py --limit 0 --print-bm-details --bm-detail-topn 3
```

### 输出摘要（示例）

- 示例 A（Top1）
  - `sample_title`: 外部恶意流量攻击触发mq网关层限流策略
  - `top_hits[0]`: `_id=_Ho9LZ0BTbzPsy9Zc9D_`, `error_code=MQ_RATE_LIMIT_001`, `servicename=mq-service`, `_score=166.12192`
- 示例 B（Top1）
  - `sample_title`: payment模块数据库慢查询引起的服务高延迟告警
  - `top_hits[0]`: `_id=_Xo9LZ0BTbzPsy9ZdNAE`, `error_code=PAYMENT_HIGH_LATENCY_002`, `servicename=payment-service`, `_score=144.13791`
- 示例 C（Top1）
  - `sample_title`: 系统由于下游user接口响应缓慢导致请求超时
  - `top_hits[0]`: `_id=A3o9LZ0BTbzPsy9ZdNEE`, `error_code=USER_READTIMEOUT_008`, `servicename=user-service`, `_score=125.6391`

### 结论

- BM25 明细可见每条样本都能拿到可用命中，且 `_id/title/error_code/servicename/_score` 字段完整。

---

## 4) 步骤三：与 MCP Hybrid 调试信息并排对照

### 对照证据（同一样本）

- 样本：`外部恶意流量攻击触发mq网关层限流策略`
  - BM25：`hit_count=1`，命中 `_id=_Ho9LZ0BTbzPsy9Zc9D_`
  - Hybrid：`EMPTY_RETRIEVAL_EXECUTED_NO_HITS`，`bm25Hits=0`，`vectorHits=0`
- 样本：`payment模块数据库慢查询引起的服务高延迟告警`
  - BM25：`hit_count=1`，命中 `_id=_Xo9LZ0BTbzPsy9ZdNAE`
  - Hybrid：`EMPTY_RETRIEVAL_EXECUTED_NO_HITS`，`bm25Hits=0`，`vectorHits=0`
- 样本：`系统由于下游user接口响应缓慢导致请求超时`
  - BM25：`hit_count=1`，命中 `_id=A3o9LZ0BTbzPsy9ZdNEE`
  - Hybrid：`EMPTY_RETRIEVAL_EXECUTED_NO_HITS`，`bm25Hits=0`，`vectorHits=0`

### Hybrid debugDetails 共同特征

- `clientSource=primary`
- `indexVisibleInPrimary=true`
- `indexVisibleInEffective=true`
- `indexVisibleInFallback=true`
- `filterCount=0`
- `usedFilterFallback=false`
- `bm25DslShape.queryType=bool`
- `vectorDslShape.queryType=script_score`

### 结论

- 直接 BM25 路径与 MCP Hybrid 路径在相同样本上出现稳定分叉：前者可命中，后者在工具内部统计仍为 `bm25Hits=0/vectorHits=0`。
- 从当前证据看，问题已不在“索引不可见/链路编码损坏”层，而更可能位于 Hybrid 工具内部检索执行路径（DSL 生成、查询文本构造、候选阶段逻辑或执行上下文）与离线路径不一致。

---

## 5) 下一步修复建议

1. 在 `app/mcp_server/tools_kb.py` 中为 `_run_bm25/_run_vector` 增加“执行前后完整 DSL + hits 总数 + 首条 `_id/_score`”日志（仅 debug 开关打开时输出）。
2. 对同一样本做“离线 BM25 DSL”与“Hybrid 内部 BM25 DSL”逐字段 diff，重点比对 `fields/query/operator/minimum_should_match`。
3. 临时增加 `force_raw_bm25=true` 调试参数，让 Hybrid 直接复用离线路径 DSL，验证是否可立即恢复命中，用于快速定位根因边界。
4. 若 `force_raw_bm25=true` 命中恢复，再回归修复 Hybrid 当前查询构造流程并补充回归测试（Top1/3/5 + 关键样本对照）。

---

## 6) 运行结论（本次）

- 本次全量排查已完成：命令、输出、并排证据均已固化。
- 当前“Hybrid 为空”的直接证据明确且可重复：**BM25 离线路径命中稳定，Hybrid 工具内路径稳定 0 命中**。
