/* spec.js — 规格单页：上标与脚注互相定位，导出按钮带批次号。 */
(function () {
  const state = { active: null };

  function footnoteAt(index) {
    return document.getElementById(`fn-${index}`);
  }

  function highlight(index) {
    document.querySelectorAll("li.fn-item").forEach((item) => {
      item.style.background = "";
    });
    const target = footnoteAt(index);
    if (!target) {
      return;
    }
    target.style.background = "var(--accent-wash)";
    state.active = index;
  }

  function bindSuperscripts() {
    document.querySelectorAll("sup.fn a").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        highlight(link.dataset.fn);
      });
    });
  }

  function bindExports() {
    const batchId = document.body.dataset.batch;
    document.querySelectorAll("[data-export]").forEach((link) => {
      if (batchId) {
        link.href = `${link.dataset.export}?batch_id=${encodeURIComponent(batchId)}`;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindSuperscripts();
    bindExports();
  });
})();
