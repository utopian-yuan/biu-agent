import { historyMenu } from "./history-menu.js";
import { composerFiles, packFiles } from "./attachments.js";
import { readEvents } from "./stream.js";
import { actionCard, recordList } from "./records.js";
import { el, node, btn, icon, option, toast, dialog, showExcerpt, sections } from "./controls.js";
import { configPage } from "./configuration.js";

const apiBase = new URL("../api/", location.href);
const key = "biu.materials." + apiBase.pathname;
let csrf = "",
  cfg,
  view = "chat",
  agent = "materials",
  model = "",
  sid = "",
  selection = 0,
  busy = false;
let hostMode = null,
  selectedSet = null,
  restoredDraft = null;
const actions = new Map();
const files = composerFiles();
const historyActions = historyMenu(manageConversation);
let threads = [],
  turns = new Map(),
  older = null,
  historyQuery = "",
  historyOffset = null,
  historyResults = [],
  pageGeneration = 0,
  poll,
  guideIndex = -1;
const steps = [
  ["#new", "从一段新对话开始", "在这里新建对话。发送第一条问题后，对话会自动出现在历史中。"],
  [
    '[data-nav="sources"]',
    "先认识素材库",
    "这里列出当前智能体获准读取的资料。点击段号可以核对原文。",
  ],
  ["#agent-wrap", "选择智能体", "不同智能体有各自的资料范围和能力。切换后从一段新对话开始。"],
  ["#compose", "提问与选择模型", "点加号附上本地文件，在旁边切换模型。回答中的出处可打开原文。"],
  [
    "#config-nav",
    "按需配置能力",
    "模型负责回答，技能提供核对方法，工具负责读取资料。MCP 可接入外部工具，智能体决定每次对话可用的资料和能力。",
  ],
  [
    ".history-heading",
    "回到以前的问题",
    "点击最近对话旁的更多操作可以重命名或删除。全部历史支持搜索。",
  ],
];
function mobile(open) {
  document.body.classList.toggle("nav-open", open);
  el("scrim").hidden = !open;
}
function flash(err) {
  if (err.name !== "AbortError") toast(err.message || "操作失败，请重试。");
}
async function api(path, body, raw = false) {
  const opts = { cache: "no-store", credentials: "same-origin" };
  if (body !== undefined)
    Object.assign(opts, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    });
  const response = await fetch(new URL(path, apiBase), opts);
  if (!response.ok) {
    const problem = (await response.json()).error || {};
    if (response.status === 401 && hostMode === false) {
      sessionStorage.removeItem(key);
      location.replace("/login");
    }
    throw Object.assign(Error(problem.message || "请求失败，请重试。"), {
      code: problem.code,
      status: response.status,
    });
  }
  return raw ? response : response.json();
}
function reset() {
  selection++;
  clearTimeout(poll);
  sid = "";
  sessionStorage.removeItem(key);
  turns.clear();
  fillComposer(null);
  el("saved-drafts").replaceChildren();
  actions.clear();
  older = null;
  el("messages").replaceChildren();
  el("welcome").hidden = false;
  el("older").hidden = true;
  el("status").textContent = "";
}
function setOptions(select, entries, value) {
  select.replaceChildren(...entries.filter((x) => x.enabled).map((x) => option(x.id, x.name)));
  select.value = value;
  if (!select.value && select.options.length) select.selectedIndex = 0;
  return select.value;
}
function picks() {
  agent = setOptions(el("agent"), Object.values(cfg.agents), agent);
  model = setOptions(el("model"), Object.values(cfg.models), model || cfg.agents[agent]?.model_id);
  files.configure(hostMode === false && cfg.agents[agent]?.allow_uploads !== false, busy);
}
async function config() {
  cfg = await api("workspace");
  picks();
}
async function listRecent() {
  const data = await api("sessions");
  threads = data.items;
  el("recent").replaceChildren(
    ...threads.slice(0, 9).map((s) => {
      const label = s.title || "新对话";
      const row = node("div", null, "recent-item" + (s.id === sid ? " active" : ""));
      const more = btn(
        "对话操作 · " + label,
        (event) => historyActions.open(event.currentTarget, s),
        "icon",
        "more",
      );
      more.dataset.historyId = s.id;
      more.setAttribute("aria-haspopup", "menu");
      more.setAttribute("aria-expanded", "false");
      more.setAttribute("aria-controls", "history-popover");
      more.onkeydown = (event) => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          historyActions.open(more, s);
        }
      };
      row.append(
        btn(label, () => choose(s.id).catch(flash), "recent-row"),
        more,
      );
      return row;
    }),
  );
  if (!threads.length) el("recent").append(node("p", "暂无历史对话", "subtle"));
}
function navigate(where) {
  historyActions.close();
  view = where;
  pageGeneration++;
  mobile(false);
  document
    .querySelectorAll("[data-nav]")
    .forEach((b) => b.classList.toggle("active", b.dataset.nav === where));
  el("location").textContent =
    where === "chat"
      ? "助手对话"
      : where === "history"
      ? "历史对话"
      : where === "sources"
      ? "素材库"
      : sections[where][0];
  el("chat-view").hidden = where !== "chat";
  el("page").hidden = where === "chat";
  if (where === "history") historyPage().catch(flash);
  if (where === "sources") sourcesPage().catch(flash);
  if (sections[where])
    el("page").replaceChildren(
      configPage(where, cfg, api, async () => {
        await config();
        await listRecent();
        if (sid && !threads.some((s) => s.id === sid)) {
          try {
            await api("sessions/" + sid);
          } catch {
            reset();
          }
        }
        navigate(where);
      }),
    );
}
function newChat() {
  if (busy) {
    toast("请等当前回答完成。");
    return;
  }
  reset();
  navigate("chat");
  listRecent().catch(flash);
  el("message").focus();
}
async function choose(id) {
  if (busy) {
    toast("请等当前回答完成。");
    return;
  }
  reset();
  const token = selection;
  const snap = await api("sessions/" + id);
  if (token !== selection) return;
  sid = id;
  sessionStorage.setItem(key, sid);
  agent = snap.agent_id || agent;
  model = snap.model_id || cfg.agents[agent]?.model_id;
  picks();
  applySnapshot(snap);
  navigate("chat");
  await listRecent();
  scrollBottom();
}
function applySnapshot(snap, append = false, followRunning = !busy) {
  if (!append) actions.clear();
  for (const action of snap.actions) actions.set(action.action_id, action);
  if (!append) {
    turns.clear();
    older = snap.next_before;
  } else older = snap.next_before;
  for (const t of snap.turns) turns.set(t.root_turn_id, t);
  el("older").hidden = !older;
  renderTurns();
  clearTimeout(poll);
  if (
    (snap.turns.some((t) => t.status === "running" || t.revision_status === "running") ||
      snap.actions.some((a) => a.status === "executing")) &&
    followRunning
  ) {
    const token = selection;
    poll = setTimeout(() => {
      if (token === selection) reload().catch(flash);
    }, 700);
  }
}
async function reload(followRunning = !busy) {
  if (!sid) return;
  const token = selection;
  const snap = await api("sessions/" + sid);
  if (token === selection) applySnapshot(snap, false, followRunning);
}
function scrollBottom() {
  el("thread").scrollTop = el("thread").scrollHeight;
}
async function openSource(turn, ref) {
  try {
    const path = ["sessions", sid, "turns", turn.turn_id, "sources", ref.id, ref.section].join("/");
    showExcerpt(await api(path));
  } catch (error) {
    flash(error);
  }
}

function renderAnswer(turn) {
  const fragment = document.createDocumentFragment();
  const evidence = new Map((turn.evidence || []).map((ref) => [ref.source, ref]));
  function inline(parent, text) {
    for (const part of text.split(/(\*\*[^*\n]+\*\*|\[[A-Za-z0-9_-]+#[A-Za-z0-9_-]+\])/g)) {
      const ref = evidence.get(part.slice(1, -1));
      if (part.startsWith("[") && ref) {
        const link = btn(ref.section, () => openSource(turn, ref), "inline-ref");
        link.setAttribute("aria-label", "查看引用 " + ref.title + " " + ref.section);
        parent.append(link);
      } else if (part.startsWith("**") && part.endsWith("**")) {
        const strong = node("strong");
        inline(strong, part.slice(2, -2));
        parent.append(strong);
      } else {
        parent.append(document.createTextNode(part));
      }
    }
  }
  let activeList;
  for (const line of (turn.answer_text || "").split("\n")) {
    const text = line.trim();
    const bullet = text.match(/^([-*]|\d+\.)\s+(.+)$/);
    if (bullet) {
      const type = /^\d/.test(bullet[1]) ? "ol" : "ul";
      if (activeList?.localName !== type) {
        activeList = node(type);
        fragment.append(activeList);
      }
      const item = node("li");
      inline(item, bullet[2]);
      activeList.append(item);
      continue;
    }
    activeList = null;
    if (!text) continue;
    const heading = text.match(/^#{1,6}\s+(.+)$/);
    const block = node(heading ? "h3" : text.startsWith("> ") ? "blockquote" : "p");
    inline(block, heading ? heading[1] : text.replace(/^> /, ""));
    fragment.append(block);
  }
  return fragment;
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制回答");
  } catch {
    dialog("复制回答", (body) => {
      const t = node("textarea");
      t.value = text;
      t.readOnly = true;
      body.append(t);
      t.select();
    });
  }
}
function appendQuestion(row, text, filenames) {
  row.append(node("p", text, "question"));
  if (filenames.length) {
    const set = node("div", null, "sent-files");
    set.setAttribute("aria-label", "随问题发送的文件");
    for (const name of filenames) {
      const chip = node("span", null, "file-chip");
      chip.append(icon("file"), node("span", name));
      set.append(chip);
    }
    row.append(set);
  }
}
function turnRow(t, attached) {
  const row = node("article", null, "turn-card");
  row.dataset.turn = t.turn_id;
  row.dataset.root = t.root_turn_id;
  const author = node("div", null, "author"),
    mark = node("img");
  mark.src = "../assets/mark.svg";
  mark.alt = "";
  author.append(mark, node("span", cfg.agents[agent]?.name || "材料助手"));
  if (t.source_turn) author.append(node("span", "重新生成的回答", "revision-label"));
  if (t.model_label) author.append(node("span", t.model_label, "revision-label"));
  appendQuestion(
    row,
    t.input_text,
    (t.attachments || []).map((file) => file.title),
  );
  row.append(author);
  const answer = node("div", null, "answer");
  answer.append(renderAnswer(t));
  row.append(answer);
  if (t.outputs?.length) {
    const outputs = node("div", null, "output-files");
    outputs.setAttribute("aria-label", "本轮生成的文件");
    for (const file of t.outputs) {
      const link = node("a", null, "file-chip");
      const url = new URL("sessions/" + sid + "/turns/" + t.root_turn_id + "/file", apiBase);
      url.searchParams.set("path", file.path);
      const name = file.path.split("/").at(-1);
      link.href = url.href;
      link.download = name;
      link.setAttribute("aria-label", "下载 " + file.path + " 第 " + file.version + " 版");
      link.append(icon("file"), node("span", name + " · v" + file.version), icon("download"));
      outputs.append(link);
    }
    row.append(outputs);
  }
  if (t.display)
    row.append(
      recordList(t.display, (id, text) => {
        selectedSet = id;
        el("message").value = text;
        el("message").focus();
      }),
    );
  for (const action of actions.values()) {
    if (action.turn_id === t.turn_id || action.turn_id === t.root_turn_id) {
      row.append(actionCard(action, updateAction));
      attached.add(action.action_id);
    }
  }
  if (t.status !== "succeeded")
    row.append(
      node(
        "p",
        t.status === "running"
          ? "正在读取材料…"
          : "本轮未完成 · " + (errorNames[t.error_code] || "请重试"),
        "muted small",
      ),
    );
  if (t.evidence?.length) {
    const refs = node("div", null, "citations");
    refs.setAttribute("aria-label", "本轮读取的原文");
    for (const r of t.evidence)
      refs.append(btn(r.title + " · " + r.section, () => openSource(t, r), "cite"));
    row.append(refs);
  }
  if (
    t.status === "succeeded" &&
    ["running", "failed", "interrupted"].includes(t.revision_status)
  ) {
    const message =
      t.revision_status === "running" ? "正在重新生成…" : "上次重新生成未完成，已保留此前回答。";
    row.append(node("p", message, "revision-status"));
  }
  if (t.status !== "running" && t.revision_status !== "running") {
    const bar = node("div", null, "actions");
    bar.setAttribute("aria-label", "回答操作");
    if (t.status === "succeeded") {
      for (const [v, label, glyph] of [
        ["up", "点赞", "like"],
        ["down", "点踩", "dislike"],
      ]) {
        const b = btn(
          label,
          async () => {
            try {
              const res = await api("sessions/" + sid + "/turns/" + t.turn_id + "/feedback", {
                value: t.feedback === v ? null : v,
              });
              t.feedback = res.value;
              renderTurns();
            } catch (e) {
              flash(e);
            }
          },
          "tool-button",
          glyph,
        );
        b.setAttribute("aria-pressed", String(t.feedback === v));
        bar.append(b);
      }
      bar.append(btn("复制回答", () => copy(t.answer_text), "tool-button", "copy"));
      if (t.context) {
        bar.append(btn("本轮上下文", () => showContext(t), "context-button", "layers"));
      }
    }
    bar.append(btn("重新生成", () => send(t), "tool-button", "refresh"));
    row.append(bar);
  }
  return row;
}
function renderTurns() {
  const list = el("messages");
  const attached = new Set();
  list.replaceChildren();
  el("welcome").hidden = turns.size > 0;
  for (const t of [...turns.values()].sort((a, b) => a.ordinal - b.ordinal)) {
    list.append(turnRow(t, attached));
  }
  for (const action of actions.values()) {
    if (!attached.has(action.action_id)) list.append(actionCard(action, updateAction));
  }
}

async function updateAction(action, operation) {
  try {
    await api("sessions/" + sid + "/actions/" + action.action_id + "/" + operation, {});
  } catch (error) {
    flash(error);
  } finally {
    await reload().catch(flash);
  }
}

function showContext(turn) {
  const context = turn.context;
  const callCounts = new Map();
  for (const name of context.calls) callCounts.set(name, (callCounts.get(name) || 0) + 1);
  const names = Object.fromEntries(Object.entries(cfg.tools).map(([k, v]) => [k, v.name]));
  names.Skill = "查看技能";
  const invoked = [...callCounts].map(([name, count]) => (names[name] || name) + " × " + count);
  const facts = [
    ["历史问题", context.history_questions + " 条，仅用于理解指代"],
    ["授权资料", context.sources.join("、") || "无"],
    ["已装配技能", context.skills.join("、") || "无"],
    ["可用工具", context.tools.map((name) => names[name] || name).join("、") || "无"],
    ["实际调用", invoked.join("，") || "无"],
    ["读到的原文", turn.evidence.map((ref) => ref.title + " " + ref.section).join("、") || "无"],
  ];
  dialog("本轮上下文", (body) => {
    const details = node("dl", null, "context-details");
    for (const [title, value] of facts) details.append(node("dt", title), node("dd", value));
    body.append(details);
  });
}
const errorNames = {
  interrupted: "连接中断",
  missing_citation: "回答缺少出处",
  unverified_source: "引用尚未核实",
  mcp_offline: "MCP 服务连接失败",
  scope_changed: "权限已更新",
  model_failed: "模型未完成回复",
  agent_failed: "模型或工具暂不可用",
  turn_timeout: "本轮已超时",
  iteration_limit: "已达本轮迭代上限",
  tool_limit: "已达工具次数上限",
};
async function refreshQuestion(original, token, restoreFocus = false) {
  // An older page can stay open while its answer is being regenerated.
  const result = await api("sessions/" + sid + "?before=" + (original.ordinal + 1));
  if (token !== selection) return;
  const current = result.turns.find((t) => t.root_turn_id === original.root_turn_id);
  if (!current) return;
  turns.set(current.root_turn_id, current);
  const old = el("messages").querySelector('[data-root="' + current.root_turn_id + '"]');
  const row = turnRow(current, new Set());
  old?.replaceWith(row);
  if (restoreFocus) row.querySelector('[aria-label="重新生成"]')?.focus({ preventScroll: true });
  if (current.revision_status === "running" || current.status === "running") {
    clearTimeout(poll);
    poll = setTimeout(() => {
      if (token === selection) refreshQuestion(original, token).catch(flash);
    }, 700);
  }
}
function composerDraft() {
  const value = el("message").value,
    attachments = files.items;
  // An unchanged retry must refer to the same submission if its response was lost.
  if (
    restoredDraft &&
    restoredDraft.sid === sid &&
    restoredDraft.model === model &&
    restoredDraft.value === value &&
    restoredDraft.visibleSet === selectedSet &&
    restoredDraft.attachments.length === attachments.length &&
    attachments.every((file, i) => file === restoredDraft.attachments[i])
  )
    return restoredDraft;
  return {
    value,
    attachments,
    visibleSet: selectedSet,
    model,
    sid,
    requestId: crypto.randomUUID(),
  };
}
function fillComposer(draft) {
  restoredDraft = draft;
  el("message").value = draft?.value || "";
  selectedSet = draft?.visibleSet ?? null;
  files.replace(draft?.attachments || []);
}
function recoverDraft(draft) {
  if (!el("message").value && !files.items.length) {
    fillComposer(draft);
    return;
  }
  // Keep both drafts when the user has already started their next question.
  const name = draft.value.trim() || draft.attachments[0]?.name || "空白文本";
  const button = btn(
    "取回草稿 · " + name.slice(0, 28),
    () => {
      if (busy) return;
      const other = composerDraft();
      button.remove();
      fillComposer(draft);
      if (other.value || other.attachments.length) recoverDraft(other);
      el("message").focus();
    },
    "plain small",
  );
  el("saved-drafts").append(button);
}
async function send(original = null) {
  if (busy) return;
  const draft = original ? null : composerDraft();
  const text =
    original?.input_text ||
    draft?.value.trim() ||
    (draft?.attachments.length ? "请概括所附文件，并标注出处。" : "");
  if (!text) return;
  busy = true;
  clearTimeout(poll);
  files.configure(!hostMode && cfg.agents[agent]?.allow_uploads !== false, true);
  el("send").disabled = true;
  el("saved-drafts").inert = true;
  el("model").disabled = true;
  el("agent").disabled = true;
  el("status").textContent = "正在准备本轮上下文…";
  const token = selection;
  let accepted = false,
    pending,
    answer;
  try {
    el("welcome").hidden = true;
    if (original) {
      const row = el("messages").querySelector('[data-root="' + original.root_turn_id + '"]');
      row.setAttribute("aria-busy", "true");
      row.querySelector(".actions")?.setAttribute("inert", "");
      const status = node("p", "正在原处重新生成…", "revision-status");
      status.setAttribute("role", "status");
      row.append(status);
    } else {
      fillComposer(null);
      el("message").focus({ preventScroll: true });
      pending = node("article", null, "turn-card");
      appendQuestion(
        pending,
        text,
        draft.attachments.map((file) => file.name),
      );
      answer = node("p", null, "answer");
      pending.append(answer);
      el("messages").append(pending);
      scrollBottom();
    }
    if (!sid) {
      const created = await api("sessions", { agent_id: agent, model_id: model });
      if (token !== selection) return;
      sid = created.session_id;
      sessionStorage.setItem(key, sid);
    }
    const path =
      "sessions/" + sid + "/turns" + (original ? "/" + original.turn_id + "/regenerate" : "");
    const body = { request_id: draft?.requestId ?? crypto.randomUUID(), model_id: model };
    if (draft) {
      draft.sid = sid;
      body.text = text;
      if (hostMode) {
        const latest = [...turns.values()]
          .filter((t) => t.display)
          .sort((a, b) => b.ordinal - a.ordinal)[0];
        body.visible_set_id = draft.visibleSet || latest?.display.display_set_id || null;
      }
      if (draft.attachments.length) body.attachments = await packFiles(draft.attachments);
    }
    if (token !== selection) return;
    async function consume() {
      const r = await api(path, body, true);
      accepted = true;
      if (!r.headers.get("content-type")?.includes("event-stream")) return;
      await readEvents(r.body, (name, data) => {
        if (token !== selection) return;
        if (name === "tool.status" && data.phase === "start")
          el("status").textContent =
            "正在调用 " +
            (cfg.tools[data.name]?.name || (data.name === "Skill" ? "技能" : data.name)) +
            "…";
        if (name === "text.delta" && answer) answer.textContent += data.text;
        if (name === "turn.error") {
          el("status").textContent = data.message;
          toast(data.message);
        }
      });
    }
    try {
      await consume();
    } catch (e) {
      if (e instanceof TypeError) await consume();
      else throw e;
    }
    if (token === selection) el("status").textContent = "";
  } catch (e) {
    if (token === selection) {
      el("status").textContent = e.message;
      flash(e);
    }
  } finally {
    if (token === selection) {
      try {
        if (original) {
          await refreshQuestion(original, token, view === "chat");
        } else {
          await reload(true);
          scrollBottom();
        }
        await listRecent();
      } catch (e) {
        if (token === selection && (e.code === "scope_changed" || e.status === 404)) reset();
        flash(e);
      }
    }
    if (
      draft &&
      !accepted &&
      token === selection &&
      ![...turns.values()].some((turn) => turn.request_id === draft.requestId)
    ) {
      pending?.remove();
      el("welcome").hidden = turns.size > 0;
      recoverDraft(draft);
    }
    busy = false;
    files.configure(!hostMode && cfg.agents[agent]?.allow_uploads !== false, false);
    el("saved-drafts").inert = false;
    el("send").disabled = false;
    el("model").disabled = false;
    el("agent").disabled = false;
  }
}
function pageHead(title, desc) {
  const box = node("div", null, "page-inner"),
    head = node("div", null, "page-title"),
    intro = node("div");
  intro.append(node("h1", title), node("p", desc));
  head.append(intro);
  box.append(head);
  return box;
}
async function sourcesPage() {
  const rev = pageGeneration;
  const result = await api("materials?agent_id=" + encodeURIComponent(agent));
  if (rev !== pageGeneration) return;
  const box = pageHead("素材库", "当前智能体可以使用的教程原文。选择段号查看内容和文件版本。"),
    grid = node("div", null, "sources-grid");
  for (const r of result.items) {
    const card = node("article", null, "source-card"),
      glyph = node("span", null, "row-icon");
    glyph.append(icon("file"));
    card.append(glyph, node("h2", r.title), node("p", r.sections.length + " 个段落 · " + r.id));
    const refs = node("div", null, "citations");
    for (const s of r.sections)
      refs.append(
        btn(
          s,
          async () => {
            try {
              showExcerpt(
                await api("materials/" + r.id + "/" + s + "?agent_id=" + encodeURIComponent(agent)),
              );
            } catch (e) {
              flash(e);
            }
          },
          "cite",
        ),
      );
    card.append(refs);
    grid.append(card);
  }
  if (!result.items.length) grid.append(node("p", "当前智能体没有获准读取的材料。", "empty"));
  box.append(grid);
  el("page").replaceChildren(box);
}
async function refreshHistory() {
  await listRecent();
  if (view === "history") await historyPage();
}
async function manageConversation(s, operation, title) {
  if (operation === "delete" && busy && s.id === sid) throw Error("请等当前回答完成。");
  await api("sessions/" + s.id + "/" + operation, operation === "rename" ? { title } : {});
  if (operation === "delete" && s.id === sid) reset();
  await refreshHistory();
  toast(operation === "rename" ? "对话已重命名" : "对话已删除");
}
async function historyPage() {
  const box = pageHead("历史对话", "打开以前的材料核对记录，或修改对话标题。");
  const search = node("input", null, "search-field");
  search.type = "search";
  search.placeholder = "搜索对话标题";
  search.setAttribute("aria-label", "搜索对话标题");
  search.value = historyQuery;
  const list = node("div", null, "row-list");
  box.append(search, list);
  el("page").replaceChildren(box);
  let serial = 0;
  async function show(more = false) {
    const index = ++serial;
    const q = search.value;
    const data = await api(
      "sessions?q=" + encodeURIComponent(q) + "&offset=" + (more ? historyOffset : 0),
    );
    if (index !== serial || !box.isConnected) return;
    historyResults = more ? [...historyResults, ...data.items] : data.items;
    historyOffset = data.next_offset;
    list.replaceChildren();
    for (const s of historyResults) {
      const row = node("div", null, "data-row"),
        pic = node("span", null, "row-icon"),
        info = node("div", null, "data-info");
      pic.append(icon("history"));
      info.append(
        btn(s.title || "新对话", () => choose(s.id).catch(flash), ""),
        node(
          "small",
          new Date(s.updated_at).toLocaleString("zh-CN") +
            " · " +
            (cfg.agents[s.agent_id]?.name || "助手"),
        ),
      );
      row.append(
        pic,
        info,
        btn(
          "重命名" + s.title,
          (event) => historyActions.open(event.currentTarget, s, "rename"),
          "icon",
          "edit",
        ),
        btn(
          "删除" + s.title,
          (event) => historyActions.open(event.currentTarget, s, "delete"),
          "icon",
          "trash",
        ),
      );
      list.append(row);
    }
    if (!historyResults.length)
      list.append(node("p", q ? "没有匹配的对话。" : "还没有对话。", "empty"));
    if (historyOffset != null)
      list.append(btn("加载更多", () => show(true).catch(flash), "secondary"));
  }
  search.oninput = () => {
    historyQuery = search.value;
    show().catch(flash);
  };
  await show();
}
function positionGuide() {
  if (guideIndex < 0) return;
  const [selector, title, text] = steps[guideIndex],
    target = document.querySelector(selector);
  document.querySelectorAll(".guide-target").forEach((n) => n.classList.remove("guide-target"));
  if (innerWidth <= 760) mobile(!!target.closest("#rail"));
  target.classList.add("guide-target");
  const guide = el("guide");
  guide.hidden = false;
  el("guide-step").textContent = guideIndex + 1 + " / " + steps.length;
  el("guide-title").textContent = title;
  el("guide-text").textContent = text;
  el("guide-back").disabled = !guideIndex;
  el("guide-next").textContent = guideIndex === steps.length - 1 ? "完成引导" : "下一步";
  const r = target.getBoundingClientRect(),
    w = guide.offsetWidth,
    h = guide.offsetHeight,
    gap = 12,
    side = r.right + w + gap < innerWidth;
  guide.style.left =
    Math.max(gap, Math.min(innerWidth - w - gap, side ? r.right + gap : r.left)) + "px";
  guide.style.top =
    Math.max(
      gap,
      Math.min(
        innerHeight - h - gap,
        side ? r.top : r.bottom + gap + h < innerHeight ? r.bottom + gap : r.top - h - gap,
      ),
    ) + "px";
}
async function finishGuide() {
  guideIndex = -1;
  el("guide").hidden = true;
  document.querySelectorAll(".guide-target").forEach((n) => n.classList.remove("guide-target"));
  mobile(false);
  try {
    await api("workspace/guide", {});
    cfg.guided = true;
  } catch (e) {
    flash(e);
  }
}
function startGuide() {
  navigate("chat");
  guideIndex = 0;
  positionGuide();
  el("guide-next").focus();
}
async function boot() {
  const info = await api("bootstrap");
  csrf = info.csrf_token;
  el("owner").textContent = info.display_name;
  el("avatar").textContent = Array.from(info.display_name)[0];
  hostMode = !info.workspace;
  if (hostMode) {
    agent = "";
    model = "";
    cfg = {
      agents: { "": { id: "", name: "业务助手", enabled: true, model_id: "" } },
      models: { "": { id: "", name: info.model_name, enabled: true } },
      tools: {},
      guided: true,
    };
    picks();
    for (const selector of [
      '[data-nav="sources"]',
      "#config-nav",
      "#agent-wrap",
      "#help",
      "#logout",
      ".model-pick",
      "#suggestions",
    ]) {
      document.querySelector(selector).hidden = true;
    }
    el("config-nav").previousElementSibling.hidden = true;
    document.querySelector("#welcome h1").textContent = "有什么需要协助？";
    document.querySelector("#welcome p").textContent = "根据当前账号的访问权限，查询业务资料。";
    document.querySelector(".rail-foot small").textContent = "当前账号";
    document.title = "业务助手";
  } else {
    await config();
  }
  await listRecent();
  const last = sessionStorage.getItem(key);
  if (last) {
    try {
      await choose(last);
    } catch (e) {
      if (e.status === 404 || e.code === "scope_changed") reset();
      else throw e;
    }
  }
  navigate("chat");
  if (!cfg.guided) startGuide();
}
for (const [id, [label, glyph]] of Object.entries(sections)) {
  const b = node("button", label);
  b.dataset.nav = id;
  b.dataset.ico = glyph;
  el("config-nav").append(b);
}
for (const n of document.querySelectorAll("[data-ico]")) n.prepend(icon(n.dataset.ico));
for (const n of document.querySelectorAll("[data-nav]")) n.onclick = () => navigate(n.dataset.nav);
for (const n of document.querySelectorAll("[data-question]"))
  n.onclick = () => {
    el("message").value = n.dataset.question;
    el("message").focus();
  };
el("agent").onchange = () => {
  if (busy) {
    el("agent").value = agent;
    return;
  }
  agent = el("agent").value;
  model = cfg.agents[agent].model_id;
  picks();
  newChat();
};
el("model").onchange = () => (model = el("model").value);
el("menu").onclick = () => mobile(true);
el("scrim").onclick = () => mobile(false);
el("new").onclick = newChat;
el("help").onclick = startGuide;
el("guide-skip").onclick = finishGuide;
el("guide-back").onclick = () => {
  guideIndex--;
  positionGuide();
};
el("guide-next").onclick = () => {
  if (guideIndex === steps.length - 1) finishGuide();
  else {
    guideIndex++;
    positionGuide();
  }
};
addEventListener("resize", positionGuide);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && guideIndex >= 0) finishGuide();
});
el("dialog-close").onclick = () => el("dialog").close();
el("compose").onsubmit = (e) => {
  e.preventDefault();
  send();
};
el("message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    send();
  }
});
el("older").onclick = async () => {
  try {
    const token = selection;
    const snap = await api("sessions/" + sid + "?before=" + older);
    if (token === selection) applySnapshot(snap, true);
  } catch (e) {
    flash(e);
  }
};
el("logout").onclick = async () => {
  try {
    await api("/auth/logout", {});
    sessionStorage.removeItem(key);
    location.replace("/login");
  } catch (e) {
    flash(e);
  }
};
boot().catch(flash);
