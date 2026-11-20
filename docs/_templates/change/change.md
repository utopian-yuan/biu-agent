# 范围与状态

```json
{
  "id": "TASK-YYYYMMDD-slug",
  "type": "task",
  "status": "proposed",
  "owner": "",
  "created": "",
  "updated": ""
}
```

类型取 `feature`、`bug`、`task`。负责人填写实际认领的人类账号。状态从 `proposed`、`accepted`、`in_progress`、`blocked`、`done`、`cancelled` 中选择，受阻写明解除条件，最终验收或取消记录决定人和依据。

## 目标与范围

填写请求来源、交付物、适用对象与本次边界。范围改变时追加日期和决定。

## 验收条件

| 编号 | 给定输入与操作 | 可观察的预期结果 |
| --- | --- | --- |
| AC1 | 填写可复现的步骤 | 填写可独立核对的结果 |

## 决定记录

记录需求接受与最终验收的人员、日期和范围，并链接[验证证据](evidence.md)。
