import { btn, node } from "./controls.js";

// The panel lives outside the scrolling sidebar so its edge can stay attached to the trigger.
export function historyMenu(apply) {
  const panel = node("section", null, "history-popover");
  panel.id = "history-popover";
  panel.hidden = true;
  document.body.append(panel);
  let anchor, session, mode, frame;

  function close(restoreFocus = false) {
    if (!anchor) return;
    const previous = anchor;
    anchor = null;
    cancelAnimationFrame(frame);
    previous.setAttribute("aria-expanded", "false");
    panel.hidden = true;
    panel.replaceChildren();
    if (restoreFocus) {
      const nearby = previous.isConnected
        ? previous
        : document.querySelector('[data-history-id="' + session.id + '"]') ||
          document.querySelector("#recent .icon") ||
          document.getElementById("new");
      nearby?.focus({ preventScroll: true });
    }
  }

  function position() {
    if (!anchor) return;
    cancelAnimationFrame(frame);
    const r = anchor.getBoundingClientRect();
    const rail = anchor.closest("#recent")?.getBoundingClientRect();
    if (
      !anchor.isConnected ||
      !r.width ||
      (rail && (r.bottom <= rail.top || r.top >= rail.bottom))
    ) {
      close();
      return;
    }
    const gap = 4,
      margin = 6,
      w = panel.offsetWidth,
      h = panel.offsetHeight;
    const bottom = window.visualViewport?.height ?? innerHeight;
    const width = document.documentElement.clientWidth;
    panel.style.left = Math.max(margin, Math.min(width - w - margin, r.right - w)) + "px";
    const top = r.bottom + gap + h <= bottom - margin ? r.bottom + gap : r.top - gap - h;
    panel.style.top = Math.max(margin, Math.min(bottom - h - margin, top)) + "px";
    const drawer = anchor.closest("#rail");
    if (drawer?.getAnimations().some((animation) => animation.playState === "running"))
      frame = requestAnimationFrame(position);
  }

  function display(nextMode) {
    mode = nextMode;
    const isMenu = mode === "menu";
    panel.classList.toggle("form-panel", !isMenu);
    panel.setAttribute("role", isMenu ? "menu" : "dialog");
    panel.setAttribute(
      "aria-label",
      isMenu ? "对话操作" : mode === "rename" ? "重命名对话" : "删除对话",
    );
    panel.replaceChildren();
    let focus;
    if (isMenu) {
      const rename = btn("重命名", () => display("rename"), "history-option", "edit");
      const remove = btn("删除", () => display("delete"), "history-option destructive", "trash");
      for (const button of [rename, remove]) button.setAttribute("role", "menuitem");
      panel.append(rename, remove);
      focus = rename;
    } else {
      const form = node("form"),
        controls = node("div", null, "history-buttons");
      const error = node("p", null, "history-error");
      error.setAttribute("role", "alert");
      let input;
      if (mode === "rename") {
        const label = node("label", "对话标题", "history-field");
        input = node("input");
        input.value = session.title;
        input.required = true;
        input.maxLength = 80;
        label.append(input);
        form.append(label);
      } else {
        form.append(
          node("strong", "删除这段对话？"),
          node(
            "p",
            "「" + (session.title || "新对话") + "」及其附件将从历史中移除。",
            "history-detail",
          ),
        );
      }
      const cancel = btn("取消", () => close(true), "plain small");
      const submit = node(
        "button",
        input ? "保存" : "删除对话",
        input ? "primary small" : "danger small",
      );
      submit.type = "submit";
      controls.append(cancel, submit);
      form.append(error, controls);
      panel.append(form);
      focus = input || cancel;
      form.onsubmit = async (event) => {
        event.preventDefault();
        const title = input?.value.trim();
        if (input && !title) {
          error.textContent = "请输入对话标题。";
          input.focus();
          position();
          return;
        }
        submit.disabled = true;
        try {
          await apply(session, input ? "rename" : "delete", title);
          if (form.isConnected) close(true);
        } catch (err) {
          if (form.isConnected) {
            error.textContent = err.message;
            submit.disabled = false;
            position();
          }
        }
      };
    }
    panel.hidden = false;
    position();
    focus.focus({ preventScroll: true });
    if (focus.tagName === "INPUT") focus.select();
  }

  function open(trigger, s, initial = "menu") {
    if (anchor === trigger && mode === initial) {
      close(true);
      return;
    }
    close();
    anchor = trigger;
    session = s;
    anchor.setAttribute("aria-expanded", "true");
    display(initial);
  }

  document.addEventListener(
    "pointerdown",
    (event) => {
      if (anchor && !panel.contains(event.target) && !anchor.contains(event.target)) close();
    },
    true,
  );
  panel.addEventListener("focusout", (event) => {
    if (
      event.relatedTarget &&
      !panel.contains(event.relatedTarget) &&
      event.relatedTarget !== anchor
    )
      close();
  });
  panel.onkeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    const items = [...panel.querySelectorAll("button:enabled,input")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Tab") {
      if ((event.shiftKey && index === 0) || (!event.shiftKey && index === items.length - 1))
        close(true);
    } else if (mode === "menu" && ["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
          ? items.length - 1
          : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next].focus({ preventScroll: true });
    }
  };
  addEventListener("resize", position);
  document.getElementById("rail").addEventListener("transitionrun", position);
  document.addEventListener("scroll", position, true);
  window.visualViewport?.addEventListener("resize", position);
  return { open, close };
}
