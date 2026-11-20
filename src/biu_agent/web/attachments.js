import { btn, el, icon, node, toast } from "./controls.js";

const maxBytes = 2 * 1024 * 1024;
const supported = /\.(txt|md|csv|json|log|docx|pdf)$/i;

function encode(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: reader.result.split(",")[1] });
    reader.onerror = () => reject(Error("无法读取「" + file.name + "」，请重新选择。"));
    reader.readAsDataURL(file);
  });
}

export function packFiles(items) {
  return Promise.all(items.map(encode));
}

export function composerFiles() {
  const input = el("file-input"),
    pick = el("attach"),
    area = el("draft-files");
  let queue = [],
    locked = false,
    allowed = false;

  function render() {
    area.replaceChildren();
    area.hidden = !queue.length;
    el("message").required = !queue.length;
    pick.hidden = !allowed;
    pick.disabled = locked;
    for (const file of queue) {
      const chip = node("span", null, "file-chip");
      const remove = btn(
        "移除 " + file.name,
        () => {
          queue = queue.filter((item) => item !== file);
          render();
          pick.focus();
        },
        "icon",
        "close",
      );
      remove.disabled = locked;
      chip.append(icon("file"), node("span", file.name), remove);
      area.append(chip);
    }
  }
  pick.onclick = () => input.click();
  input.onchange = () => {
    for (const file of input.files) {
      if (queue.length >= 4) {
        toast("每条问题最多可附 4 个文件。");
        break;
      }
      if (!supported.test(file.name)) {
        toast("支持 TXT、MD、CSV、JSON、LOG、DOCX 和文字型 PDF。");
        continue;
      }
      if (!file.size || file.size > maxBytes || Array.from(file.name).length > 120) {
        toast("请选择非空文件，每个最大 2 MiB，文件名最多 120 字。");
        continue;
      }
      if (
        !queue.some(
          (f) =>
            f.name === file.name && f.size === file.size && f.lastModified === file.lastModified,
        )
      )
        queue.push(file);
    }
    input.value = "";
    render();
  };
  return {
    get items() {
      return queue.slice();
    },
    replace(items) {
      queue = allowed ? items.slice() : [];
      render();
    },
    configure(visible, busy) {
      allowed = visible;
      locked = busy;
      if (!allowed) queue = [];
      render();
    },
  };
}
