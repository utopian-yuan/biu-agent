# 可选 · 追踪材料对话与比较实验

在本地 `.env` 填入测试项目的 `LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY`。启用额外依赖后，使用同一个工作台入口。

```sh
uv run --extra langfuse python -m examples.tickets.app --langfuse
```

以「材料助手」和「到货助手」分别问「样品灯和导向牌分别到货了吗，有没有验收依据？」。本地和可选的远端调用链记录轮次、模型、工具名、用量与耗时。下列只读查询可定位两轮数据。在工作台中打开回答的「本轮上下文」可核对实际来源。

```sql
SELECT t.id, s.agent_id, t.status
FROM turns AS t JOIN sessions AS s ON s.id=t.session_id
WHERE s.deleted_at IS NULL ORDER BY t.created_at DESC;
```

需要向测试项目提交实验评分时，把下面两个大写参数换成相应的轮次 UUID。当前题核对两条签收出处、全资料范围下的样品灯验收出处和受限范围的来源，文字结论仍由读者对照原文评阅。

```sh
uv run --extra langfuse python -m integrations.langfuse .biu/workspace/core.sqlite3 \
  --materials-turn MATERIALS_TURN_UUID --receiving-turn RECEIVING_TURN_UUID --name 材料核对第一版
```

导出器仅发送调用元数据、固定题名和判定结果。相同名称与轮次使用稳定评分标识，远端是否入库应在目标项目中回读。[本地协议测试](../../tests/test_observability.py) · [Langfuse OTLP 接口](https://langfuse.com/integrations/native/opentelemetry)
