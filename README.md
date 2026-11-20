# biu-agent

**给你的业务系统，加个 Agent。**

**Bring AI assistants to your business apps.**

用 Python 和 AgentScope 给已有系统加一个可嵌入的助手。clone 后可以先打开中文工作台，用三份虚构的展具材料练习读取原文、组装上下文、调用工具和控制权限。

## 打开工作台

使用 Python 3.12 和 uv。在包含本版源码和锁文件的检出目录运行以下命令。

```sh
git clone https://github.com/utopian-yuan/biu-agent.git
cd biu-agent
uv sync --locked
uv run python -m examples.tickets.app
```

打开 [本机工作台](http://127.0.0.1:8765/)，点击「注册账号」，设置用户名和密码。注册成功后返回登录页，用该账号登录。端口被占用时可以加 `--port 8780`，再打开对应地址。账号、会话和配置保存在本机的 `.biu/workspace/`。没有模型配置时自动使用离线教学模型，也可以用 `--scripted` 指定它。资料读取、工具调用和权限检查照常经过服务端。

1. 选择「材料助手」，问「样品灯和导向牌分别到货了吗，有没有验收依据？」。
2. 点回答中的 **R2** 看导向牌的签收原文，再点 **A2** 看这次验收覆盖了什么。「本轮上下文」可以查看历史问题数量、工具调用和读到的段落。
3. 切换到「到货助手」再问一次。两项物品都有签收依据。这个助手只能读取采购清单、到货记录，因而两项都缺少可见的验收说明。

点回答下方的「重新生成」，新回答会出现在原问题下方。点左栏对话旁的 **⋯**，可以直接在这一行旁改名或确认删除。按 Esc 收起菜单。

点输入框左下的 **＋** 随问题附上本地文件。支持 UTF-8 的 TXT、MD、CSV、JSON、LOG，以及 DOCX 和文字型 PDF。每条最多 4 个文件，单个最大 2 MiB。发送前可移除，发送后可追问并点击出处查看段落。附件仅在发出它的账号和会话中可用。

在左栏「工具」启用能力，在「智能体」里勾选谁能使用。用 `glob` 找文件、`grep` 查关键词、`read` 看原文。`write` 和 `edit` 把成果存入当前对话，回答下方可以下载当时的文件版本。`bash` 可在会话目录列文件、运行文本管道，`browser` 可阅读教程材料页和问题里指定的公开 HTTPS 网页。「技能」可导入、导出 `SKILL.md`。[用一份展签清单试完七个工具](tutorials/00-material-workspace/README.md#七个工具怎样配合)

需要自由提问时，在「模型」中添加兼容 OpenAI Chat Completions 且支持工具调用的服务。也可根据 [.env.example](.env.example) 配置 `BIU_MODEL_NAME`、`BIU_MODEL_API_KEY` 与可选 `BIU_MODEL_BASE_URL`，重启并选择已配置的模型。已有账号、资料和历史可继续使用。

## 把教程换成自己的业务资料

| 步骤 | 在本工程里怎么做 | 如何判断已接通 |
| --- | --- | --- |
| 取得素材 | 把固定文件目录和段落读取函数替换为自己的只读 API，沿用稳定的资料 ID、段号或记录 ID、版本和原文。见 [corpus.py](examples/tickets/corpus.py) | 从回复里的出处打开同版本原文 |
| 绑定身份与权限 | 独立版按注册账号隔离对话、配置和权限。嵌入时由宿主后端提供身份和每次读取的授权，服务端再次检查。见[身份契约](src/biu_agent/identity.py) | 用两个可见范围提同一问题，受限范围不能取回被排除的记录 |
| 装配上下文与工具 | 在智能体中选择可用资料、模型、技能和工具。先列目录，再按需取段，追问保留先前问题，事实重新读取。见[每轮装配](examples/tickets/workspace.py) | 用「本轮上下文」核对实际调用，切换模型后查看该回答的模型名 |
| 嵌入并复验 | 同源挂载聊天服务，指向挂载路径下的 `chat/`。把练习题和预期出处改成自家的可公开样本。见[嵌入教程](tutorials/01-connect-your-system.md) | 在宿主页面完成登录、提问、出处回读和权限收窄试验 |

## 代码与课程

| 目录 | 职责 |
| --- | --- |
| [src/biu_agent](src/biu_agent) | 通用 AgentScope 运行、会话、身份接口、调用链、同源聊天界面和确认动作 |
| [src/biu_agent/agents](src/biu_agent/agents) | 上下文、钩子、会话记忆、技能、提示词、工具和 MCP 的运行模块 |
| [examples/tickets](examples/tickets) | 本版工作台、本地账号、配置和三份教程原文。业务资料适配从这里替换 |
| [tutorials](tutorials/README.md) | 十个可运行的 Python 机制课，从 Agent loop 讲到子 Agent，另有材料工作台综合实操与宿主接入练习 |
| [integrations/langfuse](integrations/langfuse) | 可选链路导出。基础运行与本地核对可独立进行 |
| [docs](docs/README.md) | 设计决定、工作项四件套、实施及验证记录 |

从[第一课](tutorials/s01_agent_loop/README.md)开始，运行 `uv run --no-sync python -m tutorials.s01_agent_loop.code`，对着循环的三行输出看模型何时得到工具结果。工作台的版本、截图和覆盖范围见[验证记录](docs/features/FEAT-20261120-assistant-workspace/evidence.md)。项目采用 [Apache 2.0 许可证](LICENSE)。
