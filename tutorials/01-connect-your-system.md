# 从材料练习接入自己的系统

先在工作台完成一次注册、登录，再用「材料助手」问「样品灯和导向牌分别到货了吗，有没有验收依据？」。保留这次回答的引用，作为替换期间的对照。

现在可以先用两个真实的 HTTP 路由跑通目录、详情和身份传递。四组输入只改变授权范围或登录状态，命令会同时打印工具回执、服务端识别的用户和最后登记的出处。读完下面的宿主接入步骤，可以带着这组对照去验证自己的上游。[逐段讲义](lab_readonly_adapter/README.md) · [可运行的适配器](lab_readonly_adapter/code.py)

```sh
uv run --no-sync python -m tutorials.lab_readonly_adapter.code --case revoke-after-list
```

1. **替换来源。** 将登记目录与段落读取函数接到自己的只读业务 API。输入仍使用稳定 ID 和段号，结果含可回查的原文、出处、版本。由服务端调用 API，保管连接凭据。[来源适配](../examples/tickets/corpus.py)
2. **绑定身份。** 独立版通过注册账号登录，账号各有自己的配置、对话和登录会话。嵌入已有系统时，由宿主的身份解析函数提供当前用户与权限版本。检索、按 ID 读取和引用回查都需要以该身份重新鉴权。[身份契约](../src/biu_agent/identity.py) · [宿主装配](../examples/tickets/app.py)
3. **减少上下文。** 先给模型可用目录和工具说明，再送入此次用到的段落。选择一个短小的 Skill 说明核对方法。用相同问题对比技能启停、工具调用数与出处正确性。[每轮装配](../examples/tickets/workspace.py)
4. **嵌入。** 将助手挂在宿主同源路径。使用 `<iframe title="业务助手" src="/assistant/chat/"></iframe>` 引用聊天页；挂载路径变更时调整 `src`。跨域部署需要单独设计身份交换。
5. **复验。** 使用两个账号分别建立会话，核对彼此不可读取的历史。对同一问题收窄资料范围，再核对真实 API 收到的身份与返回的段落。需要追踪时选择[可选 Langfuse 集成](../integrations/langfuse/README.md)。
