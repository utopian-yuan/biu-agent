# 02 · 一个工具从参数声明到业务回执

上节课的模型碰巧给出了正确段号。假如它多传一个参数，或者传入格式正确但不存在的 R9，哪一层会发现？本课直接调用工作台使用的 `tool()` 包装器，把发布给模型的 schema、执行结果和登记的出处放在一起看。[完整代码](code.py)

```sh
uv run --no-sync python -m tutorials.s02_tools.code
uv run --no-sync python -m tutorials.s02_tools.code --extra
uv run --no-sync python -m tutorials.s02_tools.code --section R9
```

## 跟着一次调用穿过三层

```python
reader = tool(read_material, Passage, "按资料 ID 与段号读取原文", on_result=recorded.append)
```

先打开 `Passage`。它规定 `id` 和 `section` 的类型与长度，父类 `Inputs` 禁止多余字段。SDK 的 `Toolkit.get_tool_schemas()` 把这份声明暴露给模型。脚本第一行打印的两个必填字段就来自这里。[输入类型与资料读取](../../examples/tickets/corpus.py)

再从 `reader.call(**arguments)` 跟到 `tool` 内部的 `guarded`。它调用 `args.model_validate(raw)`，校验通过才进入业务函数 `excerpt`。这解释了两种错误为何不同。额外的 `all` 字段在参数校验时被拦住。R9 是合法的字符串，执行到资料层才发现这个位置不可读。[工具包装器](../../src/biu_agent/tools.py)

最后看 `on_result`。只有读取成功且序列化结果在长度限制内，包装器才调用它。工作台在相同位置用 `record_excerpt` 保存出处和版本，供发布回答时核对。[工作台注册处](../../examples/tickets/workspace.py)

| 输入 | 工具回传 | 登记的出处 |
| --- | --- | --- |
| 默认 R2 | 含原文、段号、SHA-256 的 JSON | `['receiving#R2']` |
| 多传 `all` | 参数无效 | `[]` |
| R9 | 含 `not_found` 的错误 | `[]` |

**动手验证。** 把参数改为 `--section R1`。预测工具名和参数声明是否会变，再核对返回的物品和出处。预期工具仍叫 `read_material`，物品变为样品灯，登记位置变为 R1。下一课保留合法参数，换掉当前身份能读的资料。[03 · 权限](../s03_permissions/README.md)
