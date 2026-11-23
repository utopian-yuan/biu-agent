# 02 · 工具调用，从读取一段原文到登记一份已批准的摘录

同事让助手“把到货记录里的每一项都列出来”。第一次查询完全成功，助手读到两台样品灯，写了一份看起来很合理的清单。可导向牌在第二页。这个错误应该修工具描述、查询代码，还是助手判断“已经查完”的条件？

公众号第二篇借项目清单、项目详情和周报审批讲工具设计。本章沿着同一份到货记录完成三件事。先按 ID 找到能引用的原文，再给要求“全部”的同事一份完整清单，最后把一段经复核的摘录放入指定小组的待发队列。每一步都能看到实际回执。[公众号原文](https://mp.weixin.qq.com/s/KYJpRs8Cq4MthQX60wcGUw) · [本课完整代码](code.py)

## 一、什么是工具调用

**工具**是宿主允许模型按约定名称和参数请求的一项操作。一次函数调用（function calling）包含两次交接。模型把“想做什么”写成结构化请求，宿主依据当前身份执行，再把结果与原请求配对。用户问“签收是什么意思”时可以直接解释，问“这块导向牌签收了吗”时，答案取决于哪份业务记录、哪个版本和当前可见范围。

设计工具时，先决定模型在什么情况下该选它，再决定 Python 函数接收哪些字段。本课把“发现段号”和“取得正文”拆成两项。目录请求可以成功，但只要还没执行读取，就没有一段原文可以引用。

| 契约 | `list_sections` 列段号 | `read_material` 读原文 |
| --- | --- | --- |
| 适用问题 | “到货记录里有哪些条目？” | “R2 到底写了什么？” |
| 模型填写 | 资料 ID，续页时再传上次返回的游标 | 资料 ID 和段号 |
| 宿主提供 | 已授权的资料范围、本页段号、文件版本、下一页标记 | 同一授权范围、原文、出处定位符、文件版本 |
| 成功后可以据此判断 | 下一步该读哪一段，目录还有没有下一页 | 这段材料说了什么，支持回答中的哪一项 |
| 单独无法证明 | 具体物品是否已经签收 | 整份清单是否读完、物品是否通过另一项验收 |

## 二、一次读取经过哪些步骤

### 2.1 声明工具，先让模型选得对

若两个工具的描述都写成“获取资料”，模型缺少选择依据。`build_toolkit` 的两个描述写出了各自的输入与用途。列目录会给出段号，读详情需要已知段号。它们共用当前宿主提供的 `allowed`。模型看得到如何定位资料，授权集合只在宿主的闭包中。[两个注册入口及用途](code.py#L42-L64)

读下面的注册代码时，可以把每个参数代回一笔实际请求。`read_material` 是宿主要执行的函数，`Passage` 约束可填写的字段，描述告诉模型何时先去列目录，`on_result` 让宿主在成功后登记证据。后面更换段号、权限和游标的实验，共用的就是这套注册。

[源码 · 把业务函数、参数约束和用途注册为一个工具](code.py#L57-L62)

```python
tool(
    read_material,
    Passage,
    "已知资料 ID 与段号时，读取原文、出处和版本。未知段号先用 list_sections。",
    on_result=record_excerpt,
),
```

参数模型进一步规定合法的输入形状。下面是分页工具接受的字段。默认值 `None` 表示第一次调用可以省略 `cursor`，续页时应拿上一页的原值。父类 `Inputs` 禁止多余字段。详情工具复用产品里的 `Passage`，`id` 和 `section` 都必填，长度上限分别是 48 和 12。[详情参数定义](../../examples/tickets/corpus.py#L15-L17) · [额外字段规则](../../src/biu_agent/tools.py#L13-L14)

[源码 · 分页工具的输入声明](code.py#L20-L24)

```python
class Page(Inputs):
    id: str = Field(min_length=1, max_length=48, description="已知的资料 ID，本题为 receiving。")
    cursor: str | None = Field(
        default=None, max_length=180, description="首次省略，续页原样传回 next_cursor。"
    )
```

```sh
uv run --no-sync python -m tutorials.s02_tools.code --schema
```

在项目根目录执行。首次安装用 Python 3.12 和 `uv sync --locked`。终端输出 SDK 实际暴露的 JSON。找到 `function.name`、`description`、`parameters.required` 和 `additionalProperties`。输出中的参数列表只有模型能填写的字段。当前账号获准读取的资料集合由宿主传给工具实现。即使手工在请求里添加 `allowed`，也会触发多余字段错误。

### 2.2 区分调用编号、业务位置和数据版本

[源码 · 由请求块取得 SDK 的最终工具回执](code.py#L67-L77)

```python
async def invoke(toolkit, state, call_id, name, arguments):
    call = ToolCallBlock(id=call_id, name=name, input=json.dumps(arguments, ensure_ascii=False))
    print("请求", call.id, call.name, call.input)
    result = None
    async for event in toolkit.call_tool(call, state):
        if isinstance(event, ToolResponse):
            result = event
    if result is None:
        raise RuntimeError("SDK 工具流没有给出终态")
    print("回执", result.id, result.state)
    return result
```

`ToolCallBlock.input` 装的是参数的 JSON 文本。`call.id` 标识这一笔请求。`Toolkit.call_tool` 按名称查找已注册的函数，处理参数，产出工具数据块以及最终的 `ToolResponse`。本课读后者的 `id`、`state` 和 `content`。只看到「请求 call-1」时，还无法断定业务函数已经查到了东西。

| 看见的字段 | 一个实例 | 能用它核对什么 |
| --- | --- | --- |
| 调用 ID | `call-1` | 这份回执属于哪笔请求。本章每次命令重开，示例 ID 可以重复 |
| 业务位置 | `receiving#R2` | 用户回查哪份材料的哪一段。另一次读取仍可以指向相同位置 |
| 文件版本 | `sha256` | 两次读取对应的整份文件字节是否一致。即便 R2 原样保留，修改 R1 也会改变这个摘要 |

### 2.3 参数通过校验，仍然要过资料权限这一关

[源码 · 读取授权原文并登记出处](code.py#L46-L50)

```python
async def read_material(id, section):
    return excerpt(id, section, allowed)

def record_excerpt(row):
    evidence[row["source"]] = row["sha256"]
```

`excerpt` 最终按登记的文件 ID 和当前授权范围检查可见性，再检查段号。`id="acceptance", section="A2"` 类型、长度和段号格式都合理，但这个实验只给了 `{"receiving"}`。传什么参数和允许做什么，由两处代码分别决定。[授权检查与原文读取](../../examples/tickets/corpus.py#L20-L51) · [本章注入的资料范围](code.py#L110-L114)

沿 `on_result=record_excerpt` 再读进共享的 `tool()`。它校验参数、调用业务函数，并对序列化后的 JSON 应用 4,000 字符上限，全部通过才登记出处。参数错误、记录不可见或输出过长时会回传错误状态。这个上限的单位是字符，不能据此推导模型用了多少 token。工作台发布答案时使用相同的登记时机，还会回查原文版本。[工具包装器](../../src/biu_agent/tools.py#L17-L38) · [工作台出处登记](../../examples/tickets/workspace.py#L135-L151)

### 2.4 “查到一段”和“查完整份”有不同的结束条件

本课的分页工具每页给一个段号。可以沿三个字段读懂返回值。`sections` 告诉调用者接下来读哪段，`next_cursor` 告诉它是否还有下一页，`sha256` 标识列目录时所见的版本。游标绑定资料 ID、版本和段号。调用者续页时原样传回即可。文件换版后沿用旧游标会得到 `invalid_cursor`。这一页一个段号的接口是课程实验，现有工作台的目录使用自己的返回结构。[游标与版本检查](code.py#L27-L39) · [工作台目录实现](../../examples/tickets/workspace.py#L116-L133)

[源码 · 客户端怎样认定清单读完](code.py#L101-L107)

```python
    cursor = page["next_cursor"]
    if cursor is None:
        return "清单已读完"
    if cursor in seen:
        return "清单未读完，续页标记重复"
    seen.add(cursor)
return "清单未读完，已达到页数上限"
```

比较三个退出分支。返回空游标，才是服务端认可的结束。重复游标说明继续走会打转。页数预算耗尽说明本轮时间或资源用完，已读到的材料仍有用，但清单只能标为部分结果。读取正文时还会对照同页的版本，避免将目录和刚更新的段落拼成一份看似一致的清单。[原文版本比对](code.py#L90-L99)

## 三、实战，按回执定位错误

### 实验一 · 从工具回执读出一条事实

```sh
uv run --no-sync python -m tutorials.s02_tools.code
uv run --no-sync python -m tutorials.s02_tools.code --section R1
```

默认输出里，`请求 call-1 read_material` 后面是 `{"id": "receiving", "section": "R2"}`。下一行是 `回执 call-1 success`。随后工具回传的 JSON 包含「收货点已签收一块导向牌，待安排检查。」和 `receiving#R2`。最后的 `登记的出处 ['receiving#R2']` 来自成功后的回调。把段号换成 R1，工具名和参数声明相同，正文换成样品灯，出处也随实读位置变化。

### 实验二 · 改坏一个字段，观察哪里先拦下来

以下命令都从同一个工具调用入口进入。运行前先预测哪一步会失败，以及最终登记几条出处。

```sh
uv run --no-sync python -m tutorials.s02_tools.code --extra
uv run --no-sync python -m tutorials.s02_tools.code --section R9
uv run --no-sync python -m tutorials.s02_tools.code --call read_material '{"id":"acceptance","section":"A2"}'
uv run --no-sync python -m tutorials.s02_tools.code --call archive_material '{}'
uv run --no-sync python -m tutorials.s02_tools.code --call list_sections '{"id":"receiving","cursor":"wrong"}'
```

| 改变了什么 | 预期回执 | 应做的下一步 |
| --- | --- | --- |
| 多传 `all=true` | `error`，工具参数无效 | 按 schema 删除多余字段，再发起一次合法请求 |
| 用名称和长度都合法的 R9 | `error`，`not_found` | 重新列获准资料的段号，核对定位符 |
| 直接请求当前不可见的验收说明 A2 | 同样是 `not_found` | 按当前资料说明证据缺口。服务端用同一外部错误避免泄露私有记录是否存在 |
| 请求未注册的 `archive_material` | `ToolNotFoundError` | 回到当前工具声明检查名称。没有相应能力时向用户说明 |
| 自己编造续页标记 | `invalid_cursor` | 从第一页重新获取当前版本的游标 |

这五个命令都会打印 `登记的出处 []`。R9 和 A2 在实验中有不同成因，读者知道原因是因为掌握了虚构样本。面向实际用户时，程序只能按自己获准披露的信息解释错误。

### 实验三 · 重现“每次调用都成功，报告却漏了一项”

```sh
uv run --no-sync python -m tutorials.s02_tools.code --report
uv run --no-sync python -m tutorials.s02_tools.code --report --max-pages 1
```

`report` 用确定性 Python 代码逐页查询，适合检验两项工具的契约。第一课里负责决定何时调用工具的是教学模型。无论谁选择下一步，声称“全部”之前都要拿到清单已经遍历到末页的证据。

| 顺序 | 真正执行的动作 | 得到的依据 |
| --- | --- | --- |
| 1 | `page-1` 列目录，`read-1-R1` 取详情 | 原文支持“两台样品灯已签收”。目录同时给出一个非空游标 |
| 2 | `page-2` 带上游标，`read-2-R2` 取详情 | 原文支持“一块导向牌已签收，待安排检查”。此页游标为 `null` |
| 3 | 检查终止条件和已登记出处 | 显示「清单已读完」，登记 R1、R2 两个位置 |

将预算设为一页，前两笔工具调用仍是 `success`，终端最后两行给出已经核对的范围。

```text
清单未读完，已达到页数上限
登记的出处 ['receiving#R1']
```

此时可以报告“已核对样品灯，到货记录还有下一页”，却无法据此断言“只有样品灯到货”。这和项目助手漏掉第二页延期项目是同一种错误。执行成功率再高，也不能代替集合覆盖检查。

**动手验证。** 在临时副本里，把 `section_page` 返回的 `next_cursor` 固定改成 `None`，保留真实的 R2，重跑 `--report`。预计程序会显示「清单已读完」，却只登记 R1。拿输出与源文件逐项对照，就能判断漏报来自服务端错误地宣告末页。再恢复原代码，用 `--max-pages 1` 跑一次对照。这次服务端给出了游标，客户端记录了因预算用完而停止的位置。[两段原始记录](../../examples/tickets/data/receiving.md)

## 四、从读到写，哪一版摘录可以进待发队列

读取原文时，一条成功的工具回执可以支撑一段材料事实。生成草稿、编辑已有文件、把成品发给别人，是三种影响不同的动作。把它们都塞进「处理报告」这个工具，既难描述适用时机，也难界定一次授权涵盖哪些后果。

### 实验四 · 重放同一份批准，再改动一个字

沿用 R2 已核对的签收摘录。展具组同意把这段原文加入自己的待发队列。假设刚提交，调用方就失去了回执，重试一次会出现几条记录？如果有人借用同一批准，在正文末尾加上“可以立即投入使用”，会怎样？

```sh
uv run --no-sync python -m tutorials.s02_tools.code --approval-demo
```

`approval_demo` 先通过同一个 `read_material` 取得 R2，在正文末尾加上出处。随后用一条固定的复核记录模拟人的决定，其中存有批准编号、接收小组和正文的 SHA-256 摘要。工具清单只含资料查询，无法凭模型的请求生成批准。随后由宿主调用写入函数 `enqueue_reviewed`。复核记录与待发队列保存在本次进程的 SQLite 内存数据库里。[起草与复核的入口](code.py#L157-L175)

[源码 · 在同一事务中验证批准并按批准编号去重](code.py#L138-L154)

```python
def enqueue_reviewed(db, review_id, recipient, body):
    digest = sha256(body.encode("utf-8")).hexdigest()
    with db:
        insert = db.execute(
            """INSERT INTO outbox(review_id, recipient, body)
               SELECT id, recipient, ? FROM approvals
               WHERE id=? AND recipient=? AND digest=?
               ON CONFLICT(review_id) DO NOTHING""",
            (body, review_id, recipient, digest),
        )
        if insert.rowcount:
            return f"已登记，回执 {review_id}"
        replay = db.execute(
            "SELECT review_id FROM outbox WHERE review_id=? AND recipient=? AND body=?",
            (review_id, recipient, body),
        ).fetchone()
    return f"复用回执 {replay[0]}" if replay else "缺少匹配的批准"
```

跟着 SQL 读一次。`SELECT` 用批准编号、接收小组和摘要一起匹配，匹配到才能插入。`outbox.review_id` 是主键，重复提交会触发 `DO NOTHING`。此时程序再按原编号、小组和完整正文查队列，内容完全一致才返回旧回执。两个请求就算碰巧读到了同一份批准，队列的唯一约束也只容得下一条记录。

除工具请求及出处登记外，终端会显示这六行。[五种输入和队列回读](code.py#L170-L184)

```text
复核前 缺少匹配的批准
已批原稿 已登记，回执 1
重复提交 复用回执 1
改动正文 缺少匹配的批准
更换对象 缺少匹配的批准
待发队列回读 1 条 [(1, '展具组', '收货点已签收一块导向牌，待安排检查。 [receiving#R2]')]
```

最后一行来自对队列表的查询。它证明指定摘录入队一次。若用户问“对方收到了吗”，还得向投递系统取得对应的送达回执。本实验进程退出时数据库随之释放，跨进程恢复和实际投递属于业务系统的职责。

### 从本章的两项工具迁移到工作台

如果用户说“把现有周报里的一个标题改掉”，可以先用 `glob` 找文件名，用 `read` 确认旧标题和上下文，再把读到的旧文字交给 `edit`。如果只记得标题里的关键词，用 `grep` 找匹配行更直接。这里每个工具都能写出独立的输入、结果和失败条件。例如，本工程的 `edit` 默认要求旧文字只匹配一处，还可用 `expected_version` 避免覆盖并发修改。[工作台中的读取、编辑与搜索](../../src/biu_agent/agents/tools/conversation.py#L190-L230)

| 设计决策 | 放进业务系统时怎样检验 |
| --- | --- |
| 工具如何选 | 把最容易选错的两个工具放在一起写描述。找文件名、搜正文、读内容、改一处和整份写入，各自要求的输入与产生的影响不同。可先公布当前身份有权用的目录，再按任务装配详细声明 |
| 写之前确认什么 | 向复核人展示要提交的具体内容、版本、目标和收件范围。改正文或换对象，就重新核对批准所绑定的信息 |
| 写之后怎样判断完成 | 从目标系统按操作 ID 查回执，回读实际记录或文件版本。向模型返回「已请求」时，业务状态仍可能是待确认 |
| 超时后怎样处理 | 保存原操作 ID，先查询执行结果。如果业务接口支持幂等，可用同一键恢复。把已执行的写入当成一次全新请求重放，可能生成重复记录 |

工作台里，`Workspace.prepare` 根据启停、智能体绑定和当轮能力装配工具，`Actions.confirm` 负责受身份约束的确认与回执恢复。若要在执行前后增加审计，可接着运行第四课的 Hooks 实验。[每轮工具装配](../../examples/tickets/workspace.py#L141-L174) · [确认操作](../../src/biu_agent/actions.py#L82-L119) · [Hooks 实验](../s04_hooks/README.md)

## 五、总结与自检

拿自己的业务 API 试答这组问题。请求参数从哪里来？授权范围由谁给？错误回执能否指导一个合法的下一步？单条结果有什么出处？用户要求“全部”时靠什么判断读完？如果操作会改变业务状态，还要找到能证明变更已发生的记录。

在本章里，这组问题可以逐项追到 `Page`、`build_toolkit`、`invoke`、`record_excerpt`、`report` 和 `enqueue_reviewed`。下一课固定合法的 ID 和段号，只改变账号可见范围，再看目录、直读和旧会话各自在哪一步检查权限。[03 · 权限](../s03_permissions/README.md) · [课程目录](../README.md)
