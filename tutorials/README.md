# 对着代码学业务 Agent

导向牌已经签收，能说它通过验收了吗？[到货记录的 R2](../examples/tickets/data/receiving.md)写着「待安排检查」，[验收说明的 A2](../examples/tickets/data/acceptance.md)只覆盖样品灯。十课从这两段原创虚构材料出发，逐步弄清楚助手如何取得证据、在什么条件下可以回答。

## 从两次模型决策开始

使用 Python 3.12 和 uv，先在仓库根目录安装锁定依赖，再运行第一课。

```sh
uv sync --locked
uv run --no-sync python -m tutorials.s01_agent_loop.code
```

对照[第一课讲义](s01_agent_loop/README.md)和[第一课代码](s01_agent_loop/code.py)阅读终端里的三行输出。再给同一命令加上 `--steps 1`，解释为什么这次工具读到了材料，助手却没有完成回答。这就是后面每一课的读法。安装依赖后，十课均可在本机完成，无需账号或模型密钥。MCP 一课会临时监听回环端口。

| 讲义 | 打开代码先读哪里 | 动手后应该能解释 |
| --- | --- | --- |
| [01 · Agent loop](s01_agent_loop/README.md) | [`run_loop`](s01_agent_loop/code.py) | 工具结果怎样进入下一次模型决策？ |
| [02 · 工具](s02_tools/README.md) | [`read_material` 与 `reader.call`](s02_tools/code.py) | 参数错误和段号不存在为何产生不同回执？ |
| [03 · 权限](s03_permissions/README.md) | [`inspect_scope`](s03_permissions/code.py) | 撤权后，记得资料 ID 的人还能读到什么？ |
| [04 · Hooks](s04_hooks/README.md) | [`with_hooks`](s04_hooks/code.py) | 怎样区分模型请求、实际执行和操作成功？ |
| [05 · Skills 加载](s05_skill_loading/README.md) | [`get_skill_instructions` 与 `call_tool`](s05_skill_loading/code.py) | 模型何时看到技能目录，何时看到正文？ |
| [06 · 上下文工程](s06_context/README.md) | [`messages_for` 与 `workspace.verify`](s06_context/code.py) | 旧问题可以帮忙理解追问，旧引用何时会被拒绝？ |
| [07 · 记忆系统](s07_memory/README.md) | [`remember` 与 `answer`](s07_memory/code.py) | 新会话怎样记得偏好？用户要求忘记时删哪一行？ |
| [08 · cron 调度](s08_cron/README.md) | [`daily_time` 与 `tick`](s08_cron/code.py) | 同一分钟重复触发或失败重试，会写出几份报告？ |
| [09 · MCP](s09_mcp/README.md) | [`inspect`](s09_mcp/code.py) | 远端提供的工具经过哪些检查才会被调用？ |
| [10 · 子 Agent](s10_subagent/README.md) | [`review_scope` 与 `delegate`](s10_subagent/code.py) | 子任务能看哪些消息、继承哪些权限、交回什么结果？ |

工作台使用 AgentScope 实现循环、工具、中间件、技能和 MCP，并维护会话历史。第 07 课的跨会话偏好、第 08 课的定时触发和第 10 课的父子委派是独立教学扩展。

## 把机制串成一次真实请求

运行 `uv run --no-sync python -m examples.tickets.app --scripted`，打开[本机工作台](http://127.0.0.1:8765/)。注册登录后，按[材料工作台综合实操](00-material-workspace/README.md)用两种权限提同一问题，看本轮工具轨迹并回查原文。接入自己的数据时，按[宿主与只读 API 接入练习](01-connect-your-system.md)替换一个来源。需要比较模型回答质量时，见[可选链路与评分](../integrations/langfuse/README.md)。

课程组织参考了 shareAI-lab 将一个机制拆成可运行章节的做法。要比较编码 Agent 如何将命令输出交回模型，可以对照它的第一课与本课程的原文读取实验。[learn-claude-code 中文第一课](https://github.com/shareAI-lab/learn-claude-code/blob/main/s01_agent_loop/README.zh.md)
