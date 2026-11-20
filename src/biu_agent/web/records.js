// Optional records and confirmation controls for tools supplied by an embedding host.
import { btn, node } from "./controls.js";

export function recordList(display, select) {
  const box = node("section", null, "host-records");
  box.append(node("h3", "查询结果"));
  const list = node("ol");
  for (const item of display.items) {
    const row = node("li");
    row.append(
      btn(
        item.label || item.id,
        () => select(display.display_set_id, "查看第 " + item.position + " 项"),
        "cite",
      ),
    );
    list.append(row);
  }
  box.append(list);
  if (!display.items.length) box.append(node("p", "当前范围内没有记录。"));
  if (display.has_more)
    box.append(btn("下一页", () => select(display.display_set_id, "查看下一页")));
  return box;
}

const states = {
  pending: "等待确认",
  executing: "正在执行",
  succeeded: "已完成",
  uncertain: "结果待核对",
  expired: "已过期",
  superseded: "已有新的预览",
  denied: "权限不足",
  stale: "记录已变化",
  cancelled: "已取消",
  invalid: "内容无效",
};

export function actionCard(action, update) {
  const box = node("section", null, "host-records");
  box.append(
    node("h3", action.target_label || action.target_id),
    node("p", action.body),
    node("small", states[action.status] || action.status),
  );
  const controls = node("div", null, "section-tools");
  if (action.status === "pending") {
    if (action.can_confirm !== false)
      controls.append(btn("确认执行", () => update(action, "confirm"), "primary"));
    controls.append(btn("取消", () => update(action, "cancel"), "secondary"));
  }
  if (action.status === "uncertain")
    controls.append(btn("核对结果", () => update(action, "confirm"), "secondary"));
  if (action.receipt) box.append(node("pre", JSON.stringify(action.receipt, null, 2)));
  box.append(controls);
  return box;
}
