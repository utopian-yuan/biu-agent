# biu-agent 项目文档中心

当前默认入口是中文材料工作台。[仓库首页](../README.md)维护运行与接入步骤，[教学大纲](../tutorials/README.md)提供十个 Agent 机制实验、材料实操与业务接入选修。这里供开发者和编码 Agent 查找工作项、接手任务与核对交付。

## 当前工作

| 要了解什么 | 从哪里读 |
| --- | --- |
| 对着代码学业务 Agent 的十个实验及验证 | [机制课程工作项](tasks/TASK-20261120-agent-lessons/README.md) · [第一课](../tutorials/s01_agent_loop/README.md) |
| 当前中文工作台、教程来源和权限验收 | [工作台四件套](features/FEAT-20261120-assistant-workspace/README.md) · [材料实操](../tutorials/00-material-workspace/README.md) |
| 材料工作台的交付顺序与复验入口 | [首版路线图](plan/roadmap.md) |
| 从哪个文件开始写，接口、状态机及测试如何对应 | [工作台实施计划与流程图](plan/PLAN-20261120-first-release.md) |
| 通用核心、材料工作台和可选集成各管什么 | [架构边界](architecture/README.md) |
| 本次文档接入的状态、检查和交接 | [文档治理工作项](tasks/TASK-20261120-docs-adoption/README.md) |
| 本地已交付哪些文档，验证过什么 | [项目变更日志](changes/CHANGELOG.md) |

## 如何管理一项改动

按交付目标查找已有的[功能](features/README.md)、[缺陷](bugs/README.md)或[工程任务](tasks/README.md)。新目标复制[工作项模板](_templates/change/README.md)。工作项使用同一个 ID 贯穿范围、实施、证据和变更日志。

| 维护位置 | 记录的事实 |
| --- | --- |
| `change.md` | 唯一主状态、负责人、范围、编号验收条件及接受或验收决定 |
| `design.md` | 方案、契约、影响面和恢复方式。稳定后将跨模块约定链接到架构文档 |
| `tasks.md` | 任务、实际执行人、依赖、交付物和下一位接手者的入口 |
| `evidence.md` | 每次验证的日期、基线、输入、步骤、实际结果与局限，按 E 编号追加 |

需要复验文档时读[核对指南](guides/verification.md)。涉及单独授权、正式评审或实际发布时，分别在 `docs/approvals/`、`docs/reviews/`、`docs/releases/` 建立首条真实记录，写清对象、人员、范围与依据，再从工作项引用。归档时将整项移入同类型的 `archive/`，保留 ID 和历史证据并更新链接。
