# 文档体系（Document Map）

本目录与仓库根目录 [`README.md`](../README.md)（架构设计与实现全手册）配合使用：**主手册保留总览**，`docs/` 存放可独立演进的扩展文档。

## 目录结构

| 路径 | 对应主手册 | 用途 |
|------|------------|------|
| [`architecture/`](./architecture/README.md) | §3 四层架构 | 序列图、组件边界、数据流等深化说明 |
| [`roadmap/`](./roadmap/README.md) | §4 实施阶段 | 各阶段任务拆解、验收用例、检查清单 |
| [`guides/`](./guides/README.md) | §4 阶段实施 | 接入 ES、Prompt/Schema、本地运行等操作指南 |
| [`reference/`](./reference/README.md) | §2 / §3 行动层 | Mapping 片段、DSL 示例、工具契约（Tool I/O） |

## 维护约定

- 主手册章节标题或路线图发生**整体调整**时，同步更新本文件中的「对应主手册」列与各子目录 `README.md` 的说明。
- 新增文档类型（例如 `adr/` 架构决策记录）时，在本节表格中增加一行并创建对应子目录。
