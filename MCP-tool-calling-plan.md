# DSL 生成标准化方案（MCP + OpenRouter）

> 目标：解决“AI 生成 DSL 不标准、不稳定”的问题。  
> 范围：仅日志工具链，聚焦 `queryByTimeRange` 与 `executeDsl`。

---

## 1. 问题定义

当前 DSL 不标准主要有 4 类：

- 工具选错：本该 `queryByTimeRange` 却直接走 `executeDsl`
- 参数不规范：时间格式、互斥参数、字段名不一致
- DSL 结构不稳定：混入不允许 key，或缺少必要结构
- 失败不可恢复：一次报错后没有自动重写路径

---

## 2. 总体策略（三层防线）

### 第 1 层：生成前约束（Prompt + Tool Schema）

- 把“参数化工具优先”写成硬规则：
  - 时间窗口查询必须优先 `queryByTimeRange`
  - 统计/聚合才允许 `executeDsl`
- 在 tool schema 里写清楚：
  - `startTime/endTime` 与 `lastMinutes/lastHours` 互斥
  - `timeZone` 默认 `Asia/Shanghai`
  - `size` 上限 1000
  - `executeDsl.dsl` 必须是 JSON 字符串

### 第 2 层：执行时校验（MCP Server）

- `queryByTimeRange`：
  - 相对时间优先（`lastMinutes/lastHours`），降低模型时间幻觉
  - 时间窗口上限控制（你现在已做）
  - 参数清洗（level、size、fields）
- `executeDsl`：
  - allowlist + blocklist（你现在已做）
  - 强制覆盖 `size <= 1000`
  - 拒绝危险 key（`script`、`runtime_mappings` 等）

### 第 3 层：失败后恢复（自动重写）

- 当 `executeDsl` 返回 `DSL_NOT_ALLOWED` / `INVALID_DSL_JSON`：
  - 将错误码 + message 回喂模型
  - 模型按规则重写 DSL
  - 最多重试 1~2 次
- 连续失败后降级：
  - 回退为 `queryByTimeRange` 或提示用户收窄问题

---

## 3. 你当前代码的落点（直接对应模块）

- Tool 定义侧：`app/tool_calling/mcp_tools.py`
  - 继续强化 schema 文案与示例，减少模型误填
- 校验执行侧：`app/mcp_server/tools_core.py`
  - 已有 `_validate_dsl_keys`、`_resolve_time_range`，这是核心防线
- 时间规范侧：`app/mcp_server/time_utils.py`
  - 维持 `yyyy-MM-dd HH:mm:ss` + `timeZone` 转 epoch_millis

---








