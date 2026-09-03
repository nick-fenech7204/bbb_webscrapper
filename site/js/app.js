// Static site logic: no backend, no build step -- everything here reads
// pre-published JSON files (site/data/manifest.json + one file per
// industry+metro dataset, written by scripts/publish_site_data.py) and
// filters/sorts/exports entirely in the browser.
(() => {
  "use strict";

  const metroSelect = document.getElementById("metro-select");
  const industrySelect = document.getElementById("industry-select");
  const searchBox = document.getElementById("search-box");
  const exportBtn = document.getElementById("export-btn");
  const statusEl = document.getElementById("status");
  const tableBody = document.getElementById("results-body");
  const emptyState = document.getElementById("empty-state");
  const table = document.getElementById("results-table");

  let manifest = null;
  let currentRecords = [];
  let sortKey = null;
  let sortDir = 1; // 1 asc, -1 desc

  const CURRENCY_LIKE_TRUE = new Set(["true", "1", "yes"]);

  function isTrue(value) {
    if (typeof value === "boolean") return value;
    return CURRENCY_LIKE_TRUE.has(String(value).toLowerCase());
  }

  function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  async function init() {
    try {
      const res = await fetch("data/manifest.json", { cache: "no-store" });
      if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
      manifest = await res.json();
    } catch (err) {
      statusEl.textContent =
        "Couldn't load available data (data/manifest.json missing or invalid). " +
        "Run scripts/publish_site_data.py to publish a dataset first.";
      console.error(err);
      return;
    }

    if (!manifest.datasets || manifest.datasets.length === 0) {
      statusEl.textContent = "No datasets published yet -- run scripts/publish_site_data.py.";
      return;
    }

    populateMetros();
    metroSelect.disabled = false;
    industrySelect.disabled = false;
    searchBox.disabled = false;
    statusEl.textContent = `${manifest.datasets.length} dataset(s) available. Pick a metro and industry.`;

    metroSelect.addEventListener("change", onMetroChange);
    industrySelect.addEventListener("change", onIndustryChange);
    searchBox.addEventListener("input", renderTable);
    exportBtn.addEventListener("click", exportCsv);
    table.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => onSort(th.dataset.key));
    });
  }

  function uniqueSorted(values) {
    return Array.from(new Set(values)).sort();
  }

  function populateMetros() {
    const metros = uniqueSorted(manifest.datasets.map((d) => d.metro));
    metroSelect.innerHTML =
      '<option value="">Choose a metro…</option>' +
      metros.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
  }

  function onMetroChange() {
    const metro = metroSelect.value;
    const industries = manifest.datasets.filter((d) => d.metro === metro);
    if (!metro) {
      industrySelect.innerHTML = '<option value="">Select a metro first</option>';
      clearResults("Pick a metro and industry to see results.");
      return;
    }
    industrySelect.innerHTML =
      '<option value="">Choose an industry…</option>' +
      industries
        .map((d) => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.industry)} (${d.record_count})</option>`)
        .join("");
    clearResults("Pick an industry to see results.");
  }

  async function onIndustryChange() {
    const id = industrySelect.value;
    if (!id) {
      clearResults("Pick an industry to see results.");
      return;
    }
    const dataset = manifest.datasets.find((d) => d.id === id);
    statusEl.textContent = `Loading ${dataset.industry} in ${dataset.metro}…`;
    try {
      const res = await fetch(`data/${dataset.file}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      currentRecords = await res.json();
    } catch (err) {
      statusEl.textContent = `Couldn't load ${dataset.file}.`;
      console.error(err);
      currentRecords = [];
      return;
    }
    exportBtn.disabled = currentRecords.length === 0;
    const asOf = manifest.generated_at ? new Date(manifest.generated_at).toLocaleDateString() : "unknown date";
    statusEl.textContent =
      `${currentRecords.length} businesses -- ${dataset.industry} in ${dataset.metro}. ` +
      `Data published ${asOf}.`;
    renderTable();
  }

  function clearResults(message) {
    currentRecords = [];
    tableBody.innerHTML = "";
    emptyState.hidden = true;
    exportBtn.disabled = true;
    statusEl.textContent = message;
  }

  function filteredRecords() {
    const q = searchBox.value.trim().toLowerCase();
    let rows = currentRecords;
    if (q) {
      rows = rows.filter((r) => {
        const categories = Array.isArray(r.categories) ? r.categories.join(" ") : "";
        return [r.name, r.city, r.state, r.primary_category_name, categories]
          .join(" ")
          .toLowerCase()
          .includes(q);
      });
    }
    if (sortKey) {
      rows = [...rows].sort((a, b) => {
        const av = a[sortKey] ?? "";
        const bv = b[sortKey] ?? "";
        if (typeof av === "number" || typeof bv === "number") {
          return (Number(av) - Number(bv)) * sortDir;
        }
        return String(av).localeCompare(String(bv)) * sortDir;
      });
    }
    return rows;
  }

  function onSort(key) {
    if (sortKey === key) {
      sortDir *= -1;
    } else {
      sortKey = key;
      sortDir = 1;
    }
    table.querySelectorAll("th[data-key]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === sortKey && sortDir === 1);
      th.classList.toggle("sorted-desc", th.dataset.key === sortKey && sortDir === -1);
    });
    renderTable();
  }

  function renderTable() {
    const rows = filteredRecords();
    emptyState.hidden = rows.length !== 0;
    tableBody.innerHTML = rows
      .map((r) => {
        const website = r.website
          ? `<a href="${escapeHtml(r.website)}" target="_blank" rel="noopener">${escapeHtml(r.website.replace(/^https?:\/\//, ""))}</a>`
          : "";
        const accreditedBadge = isTrue(r.accredited)
          ? '<span class="badge badge-yes">Accredited</span>'
          : '<span class="badge badge-no">—</span>';
        const nameCell = r.profile_url
          ? `<a href="${escapeHtml(r.profile_url)}" target="_blank" rel="noopener">${escapeHtml(r.name)}</a>`
          : escapeHtml(r.name);
        return `<tr>
          <td class="name-cell">${nameCell}</td>
          <td>${escapeHtml(r.city)}${r.state ? ", " + escapeHtml(r.state) : ""}</td>
          <td>${escapeHtml(r.rating)}</td>
          <td>${accreditedBadge}</td>
          <td>${escapeHtml(r.phone)}</td>
          <td>${website}</td>
          <td>${escapeHtml(r.principal_contact)}</td>
          <td>${escapeHtml(r.years_in_business)}</td>
        </tr>`;
      })
      .join("");
  }

  function exportCsv() {
    const rows = filteredRecords();
    if (rows.length === 0) return;
    const columns = Object.keys(rows[0]);
    const csvEscape = (v) => {
      if (v == null) return "";
      const s = typeof v === "object" ? JSON.stringify(v) : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [columns.join(",")];
    for (const row of rows) {
      lines.push(columns.map((c) => csvEscape(row[c])).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${industrySelect.options[industrySelect.selectedIndex]?.text || "export"}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  init();
})();
