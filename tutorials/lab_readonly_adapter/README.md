# 接入实战 · 把目录和原文工具接到业务系统

模型已经通过目录拿到了验收说明的段号 A2。就在读取详情前，小舟的账号被收回了这份资料。目录给出的 A2 仍然是个正确位置，这时业务接口还会把正文交给模型吗？

本课从一次 AgentScope 工具查询出发，HTTP 客户端带上宿主身份，业务端按当前权限返回资料，工具成功后才登记出处。两个 HTTP 路由由 FastAPI 提供，HTTPX 通过进程内 ASGI 传输调用真实路由，适合在还没有外部业务环境时检查接口契约。[完整代码](code.py)

## 一、先约定目录、详情和身份

| 接口 | 调用方提供 | 业务端决定 | 返回什么 |
| --- | --- | --- | --- |
| `GET /materials` | 宿主连接所带的会话凭据 | 此次请求时账号可看哪些资料 | 资料 ID、合法段号，不附全部正文 |
| `GET /excerpt` | 同一连接，加上 `id`、`section` | 此刻会话是否仍有效、对象是否仍在范围内 | `text`、`source`、`sha256` 等原文字段，或标准错误 |

这个顺序对接现有系统时很重要。如果第一次列目录时有权，第二次读取时已经撤权，详情必须使用最新范围。把目录结果缓存为授权名单就会把这段时间内的变化漏掉。[真实材料的范围检查](../../examples/tickets/corpus.py#L20-L29)

[源码 · 每个路由都用自己的请求重新解析身份和可读范围](code.py#L22-L35)

```python
def scope(request):
    actor = sessions.get(request.headers.get("Authorization"))
    requests.append((request.url.path, actor.display_name if actor else "未认证"))
    if actor is None:
        raise Problem(401, "session_expired", "请在宿主重新登录。")
    return grants.get((actor.tenant_id, actor.user_id), set())

@app.get("/materials")
async def list_materials(request: Request):
    return [{"id": r["id"], "sections": r["sections"]} for r in catalog(scope(request))]

@app.get("/excerpt")
async def read_material(request: Request, id: str, section: str):
    return excerpt(id, section, scope(request))
```

`scope` 从 HTTP 凭据查到租户和账号，再计算当前可读资料。凭据在命令启动时临时生成，由宿主写入 HTTP 客户端，模型的工具参数里只有资料 ID 与段号。服务端日志只保留路径和演示身份，方便从另一个观察位置验证谁实际收到请求。[宿主的连接装配](code.py#L65-L77)

## 二、适配 HTTP 成功、错误和证据

[源码 · 路由回执怎样变成工具结果与证据](code.py#L40-L55)

```python
def adapters(client, evidence):
    async def get(path, **params):
        response = await client.get(path, params=params)
        if response.is_error:
            error = response.json()["error"]
            raise Problem(response.status_code, error["code"], error["message"], error["retryable"])
        return response.json()

    async def list_materials():
        return await get("/materials")

    async def read_material(id, section):
        return await get("/excerpt", id=id, section=section)

    def record(row):
        evidence[row["source"]] = row["sha256"]
```

业务端按统一 JSON 契约返回错误。适配器保留 HTTP 状态、业务码、说明及可重试标志，工具包装器据此生成错误回执。成功的正文则带有可回查的出处和版本。`record` 随 `on_result` 绑定到详情，列目录的成功不会登记任何一段正文。[SDK 工具注册](code.py#L57-L62)

[源码 · 在两次请求之间改变权限或登录状态](code.py#L79-L90)

```python
    listing = await invoke(toolkit, state, "list-1", "list_materials", {})
    print("目录内容", listing.content[0].text)
    if case == "revoke-after-list":
        grants[owner].remove("acceptance")
    if case == "expired-session":
        sessions.clear()
    result = await invoke(
        toolkit, state, "read-1", "read_material", {"id": "acceptance", "section": "A2"}
    )
    print("详情内容", result.content[0].text)
print("服务端观察", requests)
print("已登记出处", list(evidence))
```

第二次请求始终使用 `acceptance/A2`。这样就能复现“昨天抄下了合法段号”和“刚刚还看到了这份目录”的场景，无需让模型碰巧猜到同一个参数。

## 三、实战，拿两份回执和一张服务端记录对账

```sh
uv run --no-sync python -m tutorials.lab_readonly_adapter.code
uv run --no-sync python -m tutorials.lab_readonly_adapter.code --case limited
uv run --no-sync python -m tutorials.lab_readonly_adapter.code --case revoke-after-list
uv run --no-sync python -m tutorials.lab_readonly_adapter.code --case expired-session
```

| 输入 | `list-1` 得到什么 | `read-1` 与登记的出处 |
| --- | --- | --- |
| 默认资料范围 | 到货和验收目录 | 成功，`['acceptance#A2']` |
| 一开始就限制范围 | 只有到货目录 | 直读 A2 得 `not_found`，出处为空 |
| 列完目录再撤权 | 目录里确有 A2 | 服务端仍识别小舟，但详情得 `not_found`，出处为空 |
| 列完目录使会话失效 | 同样列出了 A2 | 详情请求在服务端记录为“未认证”，返回 `session_expired`，出处为空 |

对比后两行，目录和第二次请求的参数完全相同。决定该去重新登录，还是应该按现有资料说明缺口，要看服务端检查的结果。再向下读“服务端观察”，四条路径都尝试了两个 HTTP 路由，只有默认路径有一份成功的段落证据。

## 四、换成真实业务 API 时如何验收

先挑一项允许公开演示的业务记录，约定稳定 ID、最小返回字段和版本标识。然后让适配器连接部署好的服务，用宿主正式的会话机制替换本课的临时会话表。上游若用游标分页，还要完成第二课的覆盖检查。若返回结构不同，先在适配器将它转成统一契约，再交给 `tool` 处理。真实网络的连接失败、限流和重试需要结合上游约定单独验证。

验收时，对照模型可填写的工具参数、上游处理请求时使用的身份、读到的原文版本，以及最后登记的证据。让有权的账号和受限账号读同一 ID，再在两次请求之间撤权。只看到助手说“我没有权限”，尚不足以判断上游是否真的挡住了读取。嵌入同源聊天页、绑定智能体配置和复验用户会话的后续步骤见接入指南。[宿主接入与嵌入](../01-connect-your-system.md) · [课程目录](../README.md)
