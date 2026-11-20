# 09 · 远端列出的工具，哪些能进本轮工具箱

材料目录由另一个服务维护。助手需要查询目录版次，对方同时提供了一个模拟提交操作。本课在本机启动真正的 MCP 服务，通过 Streamable HTTP 发现和调用工具。服务使用系统分配的回环端口，脚本退出后关闭。[完整代码](code.py)

```sh
uv run --no-sync python -m tutorials.s09_mcp.code
uv run --no-sync python -m tutorials.s09_mcp.code --select submit
```

## 从服务声明到一次远程调用

1. 看两个 `@server.tool`。它们将 Python 函数发布为带名称、描述和参数的远端工具。`edition` 声明只读，`submit` 没有这个声明。服务可以一起列出它们。
2. 跟进工作台实际使用的 `discover`。客户端通过 `list_raw_tools()` 获取描述，筛选名称合规、明确只读且没有破坏性提示的项。实验打印出来的候选列表只有 `edition`。这里检查的是远端元数据，真实业务权限还要由远端服务在执行时校验。[MCP 发现逻辑](../../src/biu_agent/agents/mcp/__init__.py)
3. 回到 `inspect`。下面这两行分别取本地绑定与远端可用工具的交集、打印本轮能调用的名字。只有进入后续循环，`get_tool` 才把远端工具包装为 SDK 工具，`remote.call()` 才产生 RPC 回执。

```python
bound = [name for name in names if name == selected]
print("绑定到本轮", bound)
```

默认输出先有两行 `['edition']`，随后出现带命名空间的工具名和「春季目录第三版」。选择 `submit` 后第一行仍有 `edition`，第二行变为 `[]`，没有远端调用回执。连接可用、本地绑定、工具执行可以据此分别诊断。

**动手验证。** 给 `edition` 加一个 `destructiveHint=True`，保留它的只读声明。先从 `discover` 的布尔条件预测结果，再跑默认命令，两个列表都应为空。恢复声明后，追到 `Workspace.prepare`，看实际工作台如何把获准的代理工具加入本轮 Toolkit。[工作台装配](../../examples/tickets/workspace.py)

若想继续看到「模型获知工具名 → 发起调用 → 再读回执」，运行 `uv run --no-sync pytest -q tests/test_material_connections.py`。这个本机集成用例同时启动模型协议端与 MCP 端，断言远端收到一次调用、模型调用两次。[协议用例](../../tests/test_material_connections.py)

下一课把另一个 Agent 当作父循环可以调用的工具。[10 · 子 Agent](../s10_subagent/README.md)
