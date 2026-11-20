# 03 · 列目录、按 ID 读取、回看旧会话，分别在哪里鉴权

假设用户昨天看过验收说明，记得段号 A2。今天部署者收窄了授权，用户仍可以输入相同的 ID。我们用同一份输入检查三个入口。[完整代码](code.py)

```sh
uv run --no-sync python -m tutorials.s03_permissions.code
uv run --no-sync python -m tutorials.s03_permissions.code --revoke
```

## 先看集合，再看生命周期

```python
allowed = deployment.intersection(selected)
```

实验的部署范围和智能体配置最初都包含 `receiving`、`acceptance`。传入 `--revoke` 只从部署范围移除后一项，智能体的选择保持相同。你可以用纸先算出交集，再与 `catalog(allowed)` 的输出核对。工作台的 `Workspace.sources` 在每轮装配时计算相同的交集。[工作台装配](../../examples/tickets/workspace.py)

沿 `excerpt("acceptance", "A2", allowed)` 进入 `document`。它先查 ID 是否登记、是否在本次范围内，随后才读取文件。因此即便调用者记得 ID，直接读取也得到 `404 not_found`。相同返回还能避免通过错误信息枚举私有资料。[材料读取](../../examples/tickets/corpus.py)

再看 `original.check(current)`。会话保存了原身份的权限版本。实验用 `replace` 模拟宿主在撤权时推进版本，旧会话得到 `409 scope_changed`。工作台按会话所属智能体检查版本，旧回答的来源链接还会重新核对文件版本。[身份校验](../../src/biu_agent/identity.py) · [会话检查](../../src/biu_agent/session.py)

**动手验证。** 暂时让 `deployment` 保留两份材料，只从 `profile` 移除 `acceptance`，运行默认命令。目录和直接读取仍然受限。这次编辑保留了原版本号，最后一行仍显示「原会话可继续」。如果希望已建立的会话也能察觉授权变化，宿主还要在修改范围时更新权限版本。工作台的撤权回归用例覆盖这条链路。[撤权实测用例](../../tests/test_material_workspace.py)

下一课把每次执行都要做的检查放到一个入口。[04 · Hooks](../s04_hooks/README.md)
