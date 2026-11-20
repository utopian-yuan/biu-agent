export const el = (id) => document.getElementById(id);
export function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
const paths = {
  plus: "M12 5v14M5 12h14",
  chat: "M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3H3V6a2 2 0 0 1 2-2z M7 9h10M7 13h6",
  book: "M3 5q5-2 9 1 4-3 9-1v14q-5-2-9 1-4-3-9-1z M12 6v14",
  agent: "M6 7h12q2 0 2 2v8q0 2-2 2H6q-2 0-2-2V9q0-2 2-2z M12 7V4m-1 0h2M8 12h1m6 0h1m-6 4h4",
  layers: "m3 8 9-5 9 5-9 5z M3 12l9 5 9-5M3 16l9 5 9-5",
  skill: "m4 18 9-9 3 3-9 9z M14 3v4m-2-2h4m3 9v4m-2-2h4M6 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1z",
  tools: "m14 6 4 4 3-3a6 6 0 0 1-8 7l-7 7-3-3 7-7a6 6 0 0 1 7-8z",
  plug: "M8 3v5m8-5v5M6 8h12v2a6 6 0 0 1-12 0z M12 16v5",
  search: "M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  help: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M9 9a3 3 0 1 1 5 2c-1 1-2 1-2 3m0 3h.01",
  logout: "M9 4H4v16h5m5-12 4 4-4 4m-6-4h12",
  more: "M4 12h.7m6.95 0h.7m6.95 0h.7",
  menu: "M3 5h18M3 12h18M3 19h18",
  up: "M12 19V5m-6 6 6-6 6 6",
  close: "m5 5 14 14M19 5 5 19",
  compare: "M5 4h10v13H5z M9 20h10V8m-12 2 2 2 4-4",
  scan: "M4 9V4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5m4-3h8",
  file: "M6 2h8l5 5v15H6z M14 2v6h5M9 12h7m-7 4h5",
  history: "M3 6v5h5M3 11a9 9 0 1 1 1 7m8-12v6l4 3",
  download: "M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4",
  copy: "M8 8h12v13H8z M4 16H3V3h12v2",
  like: "M7 9H3v12h4z M7 11l5-8q3 0 2 4l-1 3h5q3 0 2 3l-2 7H7",
  dislike: "M7 14H3V2h4z M7 12l5 8q3 0 2-4l-1-3h5q3 0 2-3l-2-7H7",
  refresh: "M20 4v6h-6M4 20v-6h6 M5 9a8 8 0 0 1 14-3l1 4M4 14l1 4a8 8 0 0 0 14-3",
  edit: "M4 16 16 4l4 4L8 20l-5 1z M13 7l4 4",
  trash: "M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7",
};
export function icon(name) {
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg"),
    p = document.createElementNS(ns, "path");
  svg.setAttribute("class", "glyph");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  p.setAttribute("d", paths[name] || paths.file);
  svg.append(p);
  return svg;
}
export function btn(label, fn, cls = "plain", glyph) {
  const b = node("button", cls === "icon" || cls === "tool-button" ? null : label, cls);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.title = label;
  if (glyph) b.prepend(icon(glyph));
  b.onclick = fn;
  return b;
}
export function option(value, text) {
  const o = node("option", text);
  o.value = value;
  return o;
}
let toastTimer;
export function toast(text) {
  el("toast").textContent = text;
  el("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el("toast").hidden = true;
  }, 3000);
}
export function dialog(title, build) {
  const d = el("dialog");
  if (d.open) d.close();
  el("dialog-title").textContent = title;
  const body = el("dialog-body");
  body.replaceChildren();
  build(body, () => d.close());
  d.showModal();
}
export function confirmAction(title, detail, verb, submit) {
  dialog(title, (body, close) => {
    const bar = node("div", null, "section-tools");
    bar.append(
      btn("取消", close),
      btn(
        verb,
        async () => {
          try {
            await submit();
            close();
          } catch (err) {
            toast(err.message);
          }
        },
        "danger",
      ),
    );
    body.append(node("p", detail), bar);
  });
}
export function showExcerpt(row) {
  dialog(row.title + " · " + row.section, (body) => {
    if (row.url?.startsWith("https://")) {
      const link = node("a", "查看原网页");
      link.href = row.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      body.append(link);
    }
    body.append(
      node("p", row.source, "mini-label"),
      node("p", row.text, "excerpt"),
      node("p", "文件版本 SHA-256", "small muted"),
      node("p", row.sha256, "fingerprint"),
    );
  });
}
export const sections = {
  agents: ["智能体", "agent", "为不同任务选择提示词、模型、可用资料和能力。"],
  models: ["模型", "layers", "配置支持工具调用的模型，可在对话中切换。"],
  skills: ["技能", "skill", "导入或编写工作说明，在智能体中勾选后，助手可按需读取。"],
  tools: ["工具", "tools", "控制可交给智能体使用的本地操作。"],
  mcps: ["MCP 连接", "plug", "连接外部服务，选择允许助手使用的只读工具。"],
};
