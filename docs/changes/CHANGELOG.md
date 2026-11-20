# 项目变更日志

## 2026-11-20 · 发送后立即清空输入框

发送问题时文字和附件一起移入聊天区，等待回答时可继续起草下一问。提交被拒绝时可取回文字和文件。已通过桌面及窄屏浏览器复验。[缺陷记录](../bugs/BUG-20261120-composer-submit/change.md) · [读取期间截图与复验](../bugs/BUG-20261120-composer-submit/evidence.md)

## 2026-11-20 · 就近管理对话与七个工作台工具

最近对话的菜单、改名和删除确认贴着触发按钮展开。助手可以查找和读取可见文件，在对话中生成、编辑和下载文件，用 Bash 处理文本，或从指定网页取证。[工具实操](../../tutorials/00-material-workspace/README.md#七个工具怎样配合) · [工作项与复验](../features/FEAT-20261120-assistant-workspace/evidence.md#e9)

## 2026-11-20 · 原位重生成、文件上传与技能管理

回答现在在原问题下重新生成。最近对话可直接改名、删除。加号支持随问题上传文档并按段回查，资料搜索与 Shell 可在授权段落上工作。技能支持内置目录、SKILL.md 导入导出、启停与智能体绑定。真实模型在一份虚构附件上调用了 Shell 并给出可回查的出处。[工作项](../features/FEAT-20261120-assistant-workspace/change.md) · [复验与截图](../features/FEAT-20261120-assistant-workspace/evidence.md#e8)

## 2026-11-20 · 对着代码学业务 Agent

十个中文机制课各有可运行的 Python 入口、逐步源码讲解和改变条件的练习，从工具结果回填讲到父子任务如何传递证据。课程的 24 个命令回归与一个本机 MCP 集成用例通过。[教学大纲](../../tutorials/README.md) · [工作项](../tasks/TASK-20261120-agent-lessons/change.md) · [实测记录](../tasks/TASK-20261120-agent-lessons/evidence.md)

## 2026-11-20 · 注册后登录与统一启动入口

登录页新增独立注册按钮。已有账号原地迁移，新注册账号单独管理对话、配置和权限。`uv run python -m examples.tickets.app` 直接启动中文材料工作台，旧工单宿主、专用测试和重复前端已移除。20 个本地测试及桌面、窄屏浏览器复验通过。[变更范围](../features/FEAT-20261120-assistant-workspace/change.md) · [复验记录](../features/FEAT-20261120-assistant-workspace/evidence.md#e7)

## 2026-11-20 · 中文材料工作台

本地入口支持自行建号、六步导览、历史管理、回答反馈、模型切换和智能体能力配置。教学改用三份虚构材料。回答可以回查原文版本，并查看本轮使用的资料范围和工具。12 个本地用例、桌面与窄屏浏览器路径通过，DeepSeek 同题在两个权限范围均完成实际回答。[工作项四件套](../features/FEAT-20261120-assistant-workspace/README.md) · [验证证据](../features/FEAT-20261120-assistant-workspace/evidence.md) · [动手教程](../../tutorials/00-material-workspace/README.md)

## 2026-11-20 · 首版工单助手本地实现

Codex 交付基于 AgentScope 的可嵌入聊天页、宿主身份与工具接入、确认写入、幂等回执和七章工单实验。干净检出通过 26 项离线测试。真实浏览器验证取消零条、丢回执后自动核对仍为一条。DeepSeek 两组固定题各为 8/8，独立 Langfuse 4.83.0 实例读回 16 项原生评分和 167 个实验调用节点。[实施工作项](../features/archive/FEAT-20261120-ticket-assistant/change.md) · [运行证据](../features/archive/FEAT-20261120-ticket-assistant/evidence.md)

## 2026-11-20 · 首版实施计划流程图

Codex 将七个开发阶段、宿主接入、工单确认与故障恢复、教学和 17 项拟验收用例整理为七页图册。总览已嵌入计划正文，网页版和 A4 横向打印版已在本地核对。[流程图](../tasks/archive/TASK-20261120-implementation-plan/assets/reviewed-plan.pdf) · [工作项](../tasks/archive/TASK-20261120-implementation-plan/change.md) · [核对证据](../tasks/archive/TASK-20261120-implementation-plan/evidence.md#e3)

## 2026-11-20 · 首版实施计划

Codex 将已讨论的范围细化为按依赖排序的编码切片，补充宿主接入、数据与状态、流式界面、故障回读及验收矩阵。本次交付为本地规划，基线为 `main@a35352e` 加工作区文档。[计划](../plan/PLAN-20261120-first-release.md) · [工作项](../tasks/archive/TASK-20261120-implementation-plan/change.md) · [核对证据](../tasks/archive/TASK-20261120-implementation-plan/evidence.md#e1)

## 2026-11-20 · 本地文档交付

新增项目文档中心、编码 Agent 协作入口、工作项模板和首版路线图，首页已接入文档中心。文档整理与静态核对由 Codex 执行，基线为 `main@a35352e` 加工作区改动。此条记录对应文档交付，产品功能仍在规划中。[工作项](../tasks/TASK-20261120-docs-adoption/change.md) · [本轮证据](../tasks/TASK-20261120-docs-adoption/evidence.md#e1)
