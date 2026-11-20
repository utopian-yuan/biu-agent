import { el, node, btn, icon, option, dialog, confirmAction, sections, toast } from "./controls.js";
const singular = { agents: "智能体", models: "模型", skills: "技能", tools: "工具", mcps: "连接" };
const labels = {
  name: "名称",
  model: "模型标识",
  url: "服务地址",
  secret: "访问密钥",
  prompt: "系统提示词",
  description: "用途",
  content: "技能说明",
  model_id: "默认模型",
  sources: "可用材料",
  skills: "可用技能",
  mcps: "可用连接",
  tools: "可用工具",
  enabled: "启用",
};
export function configPage(kind, cfg, api, refresh) {
  const rows = Object.values(cfg[kind]),
    box = node("div", null, "page-inner"),
    head = node("div", null, "page-title"),
    intro = node("div");
  intro.append(node("h1", sections[kind][0]), node("p", sections[kind][2]));
  head.append(intro);
  box.append(head);
  if (kind !== "tools") {
    const actions = node("div", null, "config-operations");
    if (kind === "skills") {
      const input = node("input");
      input.type = "file";
      input.accept = ".md";
      input.hidden = true;
      input.setAttribute("aria-label", "选择技能文件");
      input.onchange = async () => {
        const file = input.files[0];
        if (!file) return;
        try {
          if (file.size > 48000) throw Error("技能文件过大，请缩短工作说明。");
          const markdown = new TextDecoder("utf-8", { fatal: true }).decode(
            await file.arrayBuffer(),
          );
          await api("skills/import", { markdown });
          await refresh();
          toast("技能已导入，可在智能体中勾选使用。");
        } catch (err) {
          toast(err.message);
        } finally {
          input.value = "";
        }
      };
      actions.append(
        btn("内置技能", library, "secondary"),
        input,
        btn("导入 SKILL.md", () => input.click(), "secondary"),
      );
    }
    actions.append(btn("添加" + singular[kind], () => edit(null), "primary"));
    head.append(actions);
  }
  const list = node("div", null, "row-list");
  box.append(list);
  for (const r of rows) {
    const row = node("div", null, "data-row"),
      picture = node("span", null, "row-icon"),
      info = node("div", null, "data-info");
    picture.append(icon(sections[kind][1]));
    const desc =
      kind === "agents"
        ? r.sources
            .map((k) => cfg.source_names[k])
            .filter(Boolean)
            .join(" · ")
        : kind === "models"
        ? r.model
        : kind === "skills"
        ? r.description
        : kind === "tools"
        ? cfg.tool_descriptions?.[r.id] || r.id
        : r.url;
    info.append(node("strong", r.name), node("small", desc));
    if (kind === "tools") info.append(node("code", r.id, "tool-id"));
    row.append(picture, info);
    if (kind === "skills") {
      const bound = Object.values(cfg.agents)
        .filter((a) => a.skills.includes(r.id))
        .map((a) => a.name);
      info.append(node("small", bound.length ? "已绑定 · " + bound.join("、") : "尚未绑定智能体"));
      const toggle = btn(
        r.enabled ? "已启用" : "已停用",
        async () => {
          toggle.disabled = true;
          try {
            const values = {
              name: r.name,
              description: r.description,
              content: r.content,
              enabled: !r.enabled,
            };
            await api("workspace/skills/" + r.id + "/save", { revision: r.revision, values });
            await refresh();
          } catch (err) {
            toast(err.message);
          } finally {
            toggle.disabled = false;
          }
        },
        r.enabled ? "state" : "state off",
      );
      toggle.setAttribute("aria-pressed", String(r.enabled));
      toggle.setAttribute("aria-label", (r.enabled ? "停用 " : "启用 ") + r.name);
      toggle.title = toggle.getAttribute("aria-label");
      row.append(toggle);
    } else {
      row.append(node("span", r.enabled ? "已启用" : "已停用", r.enabled ? "state" : "state off"));
    }
    const fixed = kind === "models" && ["teaching", "environment"].includes(r.id);
    if (!fixed) row.append(btn("编辑" + r.name, () => edit(r), "icon", "edit"));
    if (kind === "skills") {
      const download = node("a", "导出", "plain small");
      download.href = new URL(
        "../api/skills/" + encodeURIComponent(r.id) + "/export",
        location.href,
      );
      download.download = "SKILL.md";
      download.setAttribute("aria-label", "导出 " + r.name + " 的 SKILL.md");
      row.append(download);
    }
    if (kind === "mcps") row.append(btn("测试连接", () => test(r), "plain small"));
    if (!fixed && kind !== "tools" && r.id !== "materials")
      row.append(
        btn(
          "删除" + r.name,
          () =>
            confirmAction(
              "删除" + singular[kind],
              "删除「" + r.name + "」后将从本机配置中移除。",
              "删除",
              async () => {
                await api("workspace/" + kind + "/" + r.id + "/delete", { revision: r.revision });
                await refresh();
              },
            ),
          "icon",
          "trash",
        ),
      );
    list.append(row);
  }
  if (!rows.length)
    list.append(node("p", "还没有" + singular[kind] + "。点击右上角添加。", "empty"));
  async function library() {
    try {
      const { items } = await api("skills/library");
      dialog("内置技能", (body, close) => {
        for (const item of items) {
          const row = node("div", null, "data-row"),
            info = node("div", null, "data-info");
          info.append(node("strong", item.name), node("small", item.description));
          row.append(info);
          if (item.matches) row.append(node("span", "已是内置版本", "state"));
          else {
            const install = async () => {
              await api("skills/library/" + item.id, { revision: item.revision });
              close();
              await refresh();
              toast("已载入内置技能");
            };
            row.append(
              btn(
                item.revision ? "使用内置版" : "添加到我的技能",
                () => {
                  if (item.revision) {
                    confirmAction(
                      "恢复内置技能",
                      "将用内置文件更新技能名称、用途和正文。当前启用状态和智能体绑定会保留。",
                      "使用内置版",
                      install,
                    );
                  } else install().catch((err) => toast(err.message));
                },
                "secondary",
              ),
            );
          }
          body.append(row);
        }
      });
    } catch (err) {
      toast(err.message);
    }
  }
  async function probe(r) {
    return (await api("connections/" + r.id + "/test", {})).tools;
  }
  async function test(r) {
    try {
      const names = await probe(r);
      dialog("连接测试", (body) => {
        body.append(
          node("p", "可用的只读工具", "mini-label"),
          node("p", names.join("、") || "服务端没有声明只读工具。", "excerpt"),
        );
      });
    } catch (err) {
      toast(err.message);
    }
  }
  function edit(r) {
    dialog((r ? "编辑" : "添加") + singular[kind], (body, close) => {
      const form = node("form"),
        fields = {},
        err = node("p", null, "form-error");
      body.append(form);
      function input(key, type = "text", initial = "", hint = "") {
        const label = node("label", labels[key], "field"),
          control = node(type === "area" ? "textarea" : "input");
        if (type !== "area") control.type = type;
        control.value = initial;
        control.required = !(
          ["secret", "prompt"].includes(key) ||
          (key === "tools" && kind === "mcps")
        );
        if (type === "password") control.autocomplete = "new-password";
        label.append(control);
        if (hint) label.append(node("small", hint));
        form.append(label);
        fields[key] = () => control.value;
        return control;
      }
      function checks(key, options, values) {
        const group = node("div", null, "field");
        group.append(node("span", labels[key]));
        const line = node("div", null, "switches"),
          items = [];
        for (const [id, name] of Object.entries(options)) {
          const label = node("label", null, "check"),
            c = node("input");
          c.type = "checkbox";
          c.checked = values.includes(id);
          c.value = id;
          items.push(c);
          label.append(c, document.createTextNode(name));
          line.append(label);
        }
        if (!items.length) line.append(node("small", "暂无可选项"));
        group.append(line);
        form.append(group);
        fields[key] = () => items.filter((i) => i.checked).map((i) => i.value);
      }
      input("name", "text", r?.name || "");
      if (kind === "models" || kind === "mcps") {
        if (kind === "models") input("model", "text", r?.model || "", "填写服务端提供的模型标识。");
        input(
          "url",
          "url",
          r?.url || "",
          kind === "mcps" ? "填写 Streamable HTTP 端点。" : "填写兼容 OpenAI 的服务根地址。",
        );
        input(
          "secret",
          "password",
          "",
          r?.has_secret ? "已保存。地址不变时留空可沿用。" : "可选，仅由服务端使用。",
        );
      }
      if (kind === "mcps") {
        const toolNames = input(
          "tools",
          "text",
          (r?.tools || []).join(", "),
          "填写已发现的只读工具名，用逗号分隔。先保存地址即可测试。",
        );
        fields.tools = () => toolNames.value.split(/[,，\s]+/).filter(Boolean);
        if (r)
          form.append(
            btn(
              "发现已保存地址的工具",
              async () => {
                try {
                  const names = await probe(r);
                  checks("tools", Object.fromEntries(names.map((n) => [n, n])), fields.tools());
                  toolNames.parentElement.hidden = true;
                } catch (e) {
                  err.textContent = e.message;
                }
              },
              "secondary small",
            ),
          );
      }
      if (kind === "skills") {
        input("description", "text", r?.description || "");
        input("content", "area", r?.content || "");
      }
      if (kind === "agents") {
        input("prompt", "area", r?.prompt || "");
        const label = node("label", labels.model_id, "field"),
          sel = node("select");
        for (const m of Object.values(cfg.models).filter((x) => x.enabled))
          sel.append(option(m.id, m.name));
        sel.value = r?.model_id || Object.keys(cfg.models).at(-1);
        label.append(sel);
        form.append(label);
        fields.model_id = () => sel.value;
        const allow = node("label", null, "check"),
          box = node("input");
        box.type = "checkbox";
        box.checked = r?.allow_uploads !== false;
        allow.append(box, document.createTextNode("允许本对话使用上传附件"));
        form.append(allow);
        fields.allow_uploads = () => box.checked;
        checks("sources", cfg.source_names, r?.sources || Object.keys(cfg.source_names));
        for (const k of ["tools", "skills", "mcps"])
          checks(
            k,
            Object.fromEntries(Object.values(cfg[k]).map((x) => [x.id, x.name])),
            r?.[k] || (k === "tools" ? Object.keys(cfg.tools) : []),
          );
      }
      const toggle = node("label", null, "check"),
        on = node("input");
      on.type = "checkbox";
      on.checked = r?.enabled ?? true;
      toggle.append(on, document.createTextNode("启用"));
      form.append(toggle);
      fields.enabled = () => on.checked;
      const bar = node("div", null, "section-tools"),
        save = node("button", "保存", "primary");
      bar.append(btn("取消", close), save);
      form.append(err, bar);
      form.onsubmit = async (e) => {
        e.preventDefault();
        save.disabled = true;
        err.textContent = "";
        try {
          const values = Object.fromEntries(Object.entries(fields).map(([k, read]) => [k, read()]));
          await api("workspace/" + kind + "/" + (r ? r.id + "/save" : "add"), {
            values,
            revision: r?.revision || 0,
          });
          close();
          await refresh();
          toast("配置已保存");
        } catch (e) {
          err.textContent = e.message;
        } finally {
          save.disabled = false;
        }
      };
    });
  }
  return box;
}
