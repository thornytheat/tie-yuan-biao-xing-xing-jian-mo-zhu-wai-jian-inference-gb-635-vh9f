/* temporal.js — 双时间页：提交一条更正做上游更新试算，判定全在后端。 */
(function () {
  const state = { running: false };

  function node(selector) {
    return document.querySelector(selector);
  }

  function show(selector, text, tone) {
    const target = node(selector);
    if (!target) {
      return;
    }
    target.textContent = text;
    target.dataset.tone = tone || "";
    target.hidden = false;
  }

  function clear() {
    for (const selector of ["[data-notice]", "[data-propose]"]) {
      const target = node(selector);
      if (target) {
        target.hidden = true;
        target.textContent = "";
      }
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

  function readCorrection(button) {
    const form = button.closest(".toolbar");
    const correction = {};
    for (const name of ["snapshot_date", "store_code", "product_code",
                        "on_hand_qty", "updated_at"]) {
      const field = form ? form.querySelector(`[name=${name}]`) : null;
      correction[name] = field ? field.value.trim() : "";
    }
    return correction;
  }

  async function runPropose(button) {
    if (state.running) {
      return;
    }
    state.running = true;
    button.disabled = true;
    button.textContent = "重算中";
    clear();
    try {
      const body = await postJSON("/api/temporal/propose", {
        corrections: [readCorrection(button)],
      });
      const changes = body.current_changes
        .map((row) => `${row.grain_text}: ${row.old ?? "—"} → ${row.new}`)
        .join("；");
      show(
        "[data-propose]",
        `新增版本 ${body.added} · 重复 ${body.duplicates} · 当前变化 `
          + `${body.current_changes.length} 条${changes ? `（${changes}）` : ""}`,
        body.added ? "ok" : "warn",
      );
    } catch (error) {
      show("[data-notice]", String(error.message), "bad");
    } finally {
      state.running = false;
      button.disabled = false;
      button.textContent = "并入重算";
    }
  }

  function bind() {
    const button = node("[data-action=propose]");
    if (!button) {
      return;
    }
    button.addEventListener("click", (event) => {
      event.preventDefault();
      runPropose(button);
    });
  }

  document.addEventListener("DOMContentLoaded", bind);
})();
