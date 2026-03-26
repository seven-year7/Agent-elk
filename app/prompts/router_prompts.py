"""
 * @Module: app/prompts/router_prompts
 * @Description: Phase 2.15 意图路由提示词：关键词未命中时，用小模型做 OPS_QUERY / GENERAL_CHAT 二分类
 * @Interface: INTENT_ROUTER_PROMPT
"""

INTENT_ROUTER_PROMPT = """你是一个运维指令分拣员。你的任务是分析用户的输入意图。

### 分类标准：
1. **OPS_QUERY**: 用户想要查询系统运行状况、报错、具体服务日志或性能指标。
   - 示例："最近有什么报错？"、"帮我看看支付流程"、"查一下这个ID：TX_9921"
2. **GENERAL_CHAT**: 用户在闲聊、打招呼、询问关于之前对话的问题。
   - 示例："你好"、"我刚才说了什么"、"你是谁"、"谢谢"

### 注意事项：
- 哪怕用户只提到了一个类似服务的词，也请归类为 OPS_QUERY。
- 如果用户是在追问之前的运维问题（例如“为什么？”、“还有吗？”），归类为 GENERAL_CHAT，因为这需要依赖历史记忆。

### 输出格式：
只返回分类代码：OPS_QUERY 或 GENERAL_CHAT。
"""

