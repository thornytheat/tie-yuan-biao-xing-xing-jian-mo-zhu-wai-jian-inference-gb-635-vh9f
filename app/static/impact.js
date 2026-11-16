/* impact.js — 影响评估页：提交一条变更做试算，结果就地显示，判定全在后端。 */
(function () {
  const state = { running: false, message: "" };

  function noticeNode() {
    return document.querySelector("[data-notice]");
  }

  function resultNode() {
    return document.querySelector("[data-propose]");
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

  function showResult(text, tone) {
    const node = resultNode();
    if (!node) {
      return;
    }
    node.textContent = text;
    node.dataset.tone = tone || "";
    node.hidden = false;
  }

  function clear() {
    const notice = noticeNode();
    if (notice) {
      notice.hidden = true;
      notice.textContent = "";
    }
    const result = resultNode();
    if (result) {
      result.hidden = true;
      result.textContent = "";
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
      throw new Error(body.message || `HTTP ${response.status}`);
    }
    return body;
  }

  function readFields(button) {
    const form = button.closest(".toolbar");
    const kind = form ? form.querySelector("[name=kind]").value : "";
    const table = form ? form.querySelector("[name=table]").value.trim() : "";
    const raw = form ? form.querySelector("[name=columns]").value : "";
    const columns = raw.split(/[,，、\s]+/).filter((item) => item.length > 0);
    return { kind, table, columns };
  }

  async function runPropose(button) {
    if (state.running) {
      return;
    }
    const fields = readFields(button);
    if (!fields.table || fields.columns.length === 0) {
      showNotice("表和列都要填。", "bad");
      return;
    }
    state.running = true;
    button.disabled = true;
    button.textContent = "计算中";
    clear();
    try {
      const body = await postJSON("/api/impact/propose", {
        changes: [{
          kind: fields.kind,
          table: fields.table,
          columns: fields.columns,
        }],
      });
      const summary = body.summary;
      const blocked = body.blocked.map((row) => row.code).join("、");
      const suffix = blocked ? `，阻断 ${blocked}` : "";
      showResult(
        `可执行 ${summary.executable} / 下游 ${summary.objects}`
          + ` / 中断 ${summary.break}${suffix}`,
        summary.blocked ? "warn" : "ok",
      );
    } catch (error) {
      showNotice(String(error.message), "bad");
    } finally {
      state.running = false;
      button.disabled = false;
      button.textContent = "试算";
    }
  }

  function bind() {
    const button = document.querySelector("[data-action=propose]");
    if (!button) {
      return;
    }
    button.addEventListener("click", (event) => {
      event.preventDefault();
      runPropose(button);
    });
    const field = document.querySelector("[name=columns]");
    if (field) {
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          runPropose(button);
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
