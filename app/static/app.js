/* app.js — 导入触发与请求失败反馈。状态集中在 state 里，不用全局散变量。 */
(function () {
  const state = {
    running: false,
    message: "",
  };

  function noticeNode() {
    return document.querySelector("[data-notice]");
  }

  function showNotice(text, tone) {
    const node = noticeNode();
    if (!node) {
      return;
    }
    node.textContent = text;
    node.dataset.tone = tone || "";
    node.hidden = false;
  }

  function clearNotice() {
    const node = noticeNode();
    if (node) {
      node.hidden = true;
      node.textContent = "";
    }
  }

  async function postJSON(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.message || `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return body;
  }

  async function runImport(button) {
    if (state.running) {
      return;
    }
    const form = button.closest("form");
    const source = form ? form.querySelector("[name=source]").value : "sample";
    const capField = form ? form.querySelector("[name=sample_cap]") : null;
    const sampleCap = capField ? Number(capField.value) : 500;
    state.running = true;
    button.disabled = true;
    button.textContent = "运行中";
    clearNotice();
    try {
      const body = await postJSON("/api/import", {
        source: source,
        sample_cap: sampleCap,
      });
      const replayed = body.replayed ? "复用" : "新批次";
      showNotice(`${replayed} ${body.batch_id}`, "ok");
      window.setTimeout(() => window.location.reload(), 320);
    } catch (error) {
      showNotice(String(error.message), "bad");
    } finally {
      state.running = false;
      button.disabled = false;
      button.textContent = "运行接入";
    }
  }

  function bind() {
    const button = document.querySelector("[data-action=import]");
    if (button) {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        runImport(button);
      });
    }
    const print = document.querySelector("[data-action=print]");
    if (print) {
      print.addEventListener("click", () => window.print());
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
