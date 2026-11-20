# 05 · 模型在什么时候看到 SKILL.md 正文

采购、签收、验收各有核对方法。如果每轮都把每份说明全文放进提示词，一个简单追问也要携带所有流程。我们拿仓库里的「材料核对」做一次真正的 SDK 技能读取，分别打印初始目录和工具回传。[完整代码](code.py) · [实验使用的 SKILL.md](../../src/biu_agent/agents/skills/source-review/SKILL.md)

```sh
uv run --no-sync python -m tutorials.s05_skill_loading.code
uv run --no-sync python -m tutorials.s05_skill_loading.code --name missing
```

## 区分三个时刻

1. **宿主发现。** `LocalSkillLoader` 读取指定目录下的文件，解析头部的 `name`、`description` 和后面的 Markdown 正文。文件读入宿主内存和正文进入模型上下文是两个时刻。
2. **模型看到目录。** 查 `skill_instruction_template`。本课模板仅取名称和描述。`get_skill_instructions()` 生成的首行有 `source-review` 和「逐项核对材料并标注出处」，完整核对顺序尚未出现在这一行。
3. **按名称展开。** 下面的调用块相当于模型提出的一次动作。传进 `toolkit.call_tool` 后，脚本只打印最终的 `ToolResponse`。成功时这条回执才带有「先看目录」等正文，供下一次模型决策使用。第二条命令把名字改成不存在的技能，得到 `SkillNotFoundError`。

```python
call = ToolCallBlock(id="skill-1", name="Skill", input=json.dumps({"skill": name}))
```

**动手验证。** 在模板中删去 `{{ s.description }}`，保留 `{{ s.name }}`。再跑默认命令，预测哪一行变短。正文仍可按名称读出，但自由选择技能的模型少了一条判断适用场景的线索。脚本指定了技能名称，所以这个实验只验证上下文变化。

工作台从账号配置里取出智能体已绑定且启用的技能，构造 `Skill`，交给同一个 Toolkit。读到技能后还要单独调用有权限的资料工具，才会有可引用原文。可以在综合实操中停用「读取段落」，保留技能，检查本轮登记了哪些出处。[工作台的 Skills 装配](../../examples/tickets/workspace.py) · [综合实操](../00-material-workspace/README.md)

下一课把刚读出的证据与历史对话放在一起比较。[06 · 上下文工程](../s06_context/README.md)
