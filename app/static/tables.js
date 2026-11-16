/* tables.js — 宽表的排序与过滤，只做展示，不参与判定。 */
(function () {
  const state = { sort: {}, filter: {} };

  function numberish(text) {
    const clean = text.replace(/[,%\s]/g, "");
    const value = Number(clean);
    return Number.isFinite(value) ? value : null;
  }

  function sortTable(table, index, top) {
    const body = table.tBodies[0];
    const rows = Array.from(body.rows);
    const descending = state.sort[table.dataset.key] === false;
    rows.sort((left, right) => {
      const a = left.cells[index].textContent.trim();
      const b = right.cells[index].textContent.trim();
      const na = numberish(a);
      const nb = numberish(b);
      if (na !== null && nb !== null) {
        return descending ? nb - na : na - nb;
      }
      return descending ? b.localeCompare(a, "zh-Hans-CN") : a.localeCompare(b, "zh-Hans-CN");
    });
    state.sort[table.dataset.key] = !descending;
    rows.forEach((row) => body.appendChild(row));
  }

  function bindSortable() {
    document.querySelectorAll("table[data-sortable]").forEach((table) => {
      if (!table.dataset.key) {
        table.dataset.key = `t${Math.floor(Math.random() * 1e6)}`;
      }
      Array.from(table.tHead.rows[0].cells).forEach((cell, index) => {
        cell.style.cursor = "pointer";
        cell.title = "按此列排序";
        cell.addEventListener("click", () => sortTable(table, index, true));
      });
    });
  }

  function applyFilter(input) {
    const key = input.dataset.filterTarget;
    const needle = input.value.trim().toLowerCase();
    state.filter[key] = needle;
    document.querySelectorAll(`[data-filter-scope="${key}"] tbody tr`).forEach((row) => {
      const text = row.textContent.toLowerCase();
      row.hidden = needle !== "" && !text.includes(needle);
    });
  }

  function bindFilters() {
    document.querySelectorAll("[data-filter-target]").forEach((input) => {
      input.addEventListener("input", () => applyFilter(input));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindSortable();
    bindFilters();
  });
})();
