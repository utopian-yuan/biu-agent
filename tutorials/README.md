# 对着代码学业务 Agent

导向牌已经签收，能说它通过验收了吗？[到货记录的 R2](../examples/tickets/data/receiving.md)写着「待安排检查」，[验收说明的 A2](../examples/tickets/data/acceptance.md)只覆盖样品灯。十课从这两段原创虚构材料出发，逐步弄清楚助手如何取得证据、在什么条件下可以回答。

## 从两次模型决策开始

使用 Python 3.12 和 uv，先在仓库根目录安装锁定依赖，再运行第一课。

```sh
uv sync --locked
uv run --no-sync python -m tutorials.s01_agent_loop.code
```

对照[第一课讲义](s01_agent_loop/README.md)和[第一课代码](s01_agent_loop/code.py)阅读终端里的三行输出。再给同一命令加上 `--steps 1`，解释为什么这次工具读到了材料，助手却没有完成回答。这就是后面每一课的读法。安装依赖后，十课均可在本机完成，无需账号或模型密钥。MCP 一课会临时监听回环端口。

## 按手头的问题选课

- **想搭起第一个循环。** 从 01、02 入手，跟着调用编号读一遍模型请求、工具回执和下一轮输入。
- **准备让助手写业务数据。** 把 03、04 连起来读。它们共同展开第三篇《Agent 权限与 Hooks》的 P-101 备注案例。先检查一份批准究竟绑定了什么，再亲手制造撤权、审计失败和回执丢失。
- **助手似乎忘了，也可能记得太多。** 05、06、07 分别看技能正文何时加载、历史如何按预算进入上下文、个人偏好怎样跨会话保存或删除。
- **让任务可以定时执行、跨服务查询或委派。** 08、09、10 分别用数据库行、远端调用记录和父任务复读的原文核对结果。准备接企业接口时，最后用只读接入实战把这些观察串起来。[只读 API 接入实战](lab_readonly_adapter/README.md)

| 讲义 | 打开代码先读哪里 | 动手后应该能解释 |
| --- | --- | --- |
| [01 · Agent loop](s01_agent_loop/README.md) | 从 [`run_loop`](s01_agent_loop/code.py#L36) 到 [`run_manual`](s01_agent_loop/code.py#L106) 和 [`run_framework`](s01_agent_loop/code.py#L142) | 三次模型调用如何用上五份回执？只给一步预算时，两种循环各自怎样退出？ |
| [02 · 工具调用](s02_tools/README.md) | [`build_toolkit`](s02_tools/code.py#L42)、[`report`](s02_tools/code.py#L80) 和 [`enqueue_reviewed`](s02_tools/code.py#L138) | 第一页读取成功后，怎样发现清单仍缺一项？同一批准重试两次会入队几条？ |
| [03 · 权限与单次批准](s03_permissions/README.md) | [`decision`](s03_permissions/code.py#L32) 与 [`consume_approval`](s03_permissions/code.py#L70) | 改正文、撤权后又恢复、等到第 300 秒，哪一份备注能写入？ |
| [04 · Hooks 与写入回执](s04_hooks/README.md) | [`with_hooks`](s04_hooks/code.py#L26) 与 [`ReviewGate`](s04_hooks/code.py#L127) | 框架收到确认后谁还会检查？审计失败后怎样避免再加一条备注？ |
| [05 · Skills 加载](s05_skill_loading/README.md) | [`load_skills`](s05_skill_loading/code.py#L18) 与[两次调用](s05_skill_loading/code.py#L61) | 看见技能目录、展开正文、取得原文，分别能留下什么证据？ |
| [06 · 上下文工程](s06_context/README.md) | [模型输入与发布](s06_context/code.py#L32) 和 [`history_window`](s06_context/code.py#L53) | 重生成时哪些历史仍可见？为什么真实的引用也可能支持不了结论？ |
| [07 · 记忆系统](s07_memory/README.md) | [`remember`](s07_memory/code.py#L17) 与 [`answer`](s07_memory/code.py#L29) | 新会话如何恢复偏好？不同租户的同号账号、撤权和遗忘会改变哪一行？ |
| [08 · cron 调度](s08_cron/README.md) | [`tick`](s08_cron/code.py#L40) 与 [`simultaneous`](s08_cron/code.py#L62) | 两个连接同时触发、首次失败或错过九点，各有几份报告？ |
| [09 · MCP](s09_mcp/README.md) | [`make_server`](s09_mcp/code.py#L16) 与 [`inspect`](s09_mcp/code.py#L41) | 发现两项工具，为何有时只调用一项，有时服务端计数为零？ |
| [10 · 子 Agent](s10_subagent/README.md) | [`review_scope`](s10_subagent/code.py#L30) 与 [`verify_finding`](s10_subagent/code.py#L49) | 子任务已经找到原文，父任务为什么还会拒绝交接？ |
| [接入实战 · 只读业务 API](lab_readonly_adapter/README.md) | [HTTP 资料接口](lab_readonly_adapter/code.py#L18) 与 [SDK 适配器](lab_readonly_adapter/code.py#L40) | 列目录后撤权，下一次详情读取和证据登记各会发生什么？ |

工作台使用 AgentScope 实现循环、工具、中间件、技能和 MCP，并维护会话历史。第 07 课的跨会话偏好、第 08 课的定时触发和第 10 课的父子委派是独立教学扩展。

## 把机制串成一次真实请求

运行 `uv run --no-sync python -m examples.tickets.app --scripted`，打开[本机工作台](http://127.0.0.1:8765/)。注册登录后，按[材料工作台综合实操](00-material-workspace/README.md)用两种权限提同一问题，看本轮工具轨迹并回查原文。接入自己的数据时，按[宿主与只读 API 接入练习](01-connect-your-system.md)替换一个来源。需要比较模型回答质量时，见[可选链路与评分](../integrations/langfuse/README.md)。

课程组织参考了 shareAI-lab 将一个机制拆成可运行章节的做法。要比较编码 Agent 如何将命令输出交回模型，可以对照它的第一课与本课程的原文读取实验。[learn-claude-code 中文第一课](https://github.com/shareAI-lab/learn-claude-code/blob/main/s01_agent_loop/README.zh.md)
