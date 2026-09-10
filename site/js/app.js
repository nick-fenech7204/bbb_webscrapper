// Static site logic: no backend, no build step. Reads pre-published JSON
// (site/data/manifest.json + one file per industry+metro dataset, written by
// scripts/publish_site_data.py) and filters / sorts / exports in the browser.
//
// Two views share this file, chosen by <body data-view="leads|intel">:
//   leads -> the standard BBB lead record
//   intel -> BBB + matched Yelp + our derived-intelligence columns
(() => {
  "use strict";

  const VIEW = document.body.dataset.view === "intel" ? "intel" : "leads";

  const metroSelect = document.getElementById("metro-select");
  const industrySelect = document.getElementById("industry-select");
  const searchBox = document.getElementById("search-box");
  const exportBtn = document.getElementById("export-btn");
  const statusEl = document.getElementById("status");
  const headRow = document.getElementById("head-row");
  const tableBody = document.getElementById("results-body");
  const emptyState = document.getElementById("empty-state");
  const table = document.getElementById("results-table");
  const intelNote = document.getElementById("intel-note");

  let manifest = null;
  let currentRecords = [];
  let currentDataset = null;
  let sortKey = null;
  let sortDir = 1; // 1 asc, -1 desc

  const TRUEISH = new Set(["true", "1", "yes", "y", "t"]);
  const isTrue = (v) => (typeof v === "boolean" ? v : TRUEISH.has(String(v).toLowerCase()));

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
  const dash = "—";

  function nameCell(r) {
    const label = esc(r.name);
    return r.profile_url
      ? `<a href="${esc(r.profile_url)}" target="_blank" rel="noopener">${label}</a>`
      : label;
  }

  function cityCell(r) {
    return esc(r.city) + (r.state ? ", " + esc(r.state) : "");
  }

  function websiteCell(r) {
    if (!r.website) return "";
    return `<a href="${esc(r.website)}" target="_blank" rel="noopener">${esc(
      r.website.replace(/^https?:\/\//, "")
    )}</a>`;
  }

  function accreditedCell(r) {
    return isTrue(r.accredited)
      ? '<span class="badge badge-yes">Accredited</span>'
      : `<span class="badge badge-no">${dash}</span>`;
  }

  function yelpCell(r) {
    if (!r.on_yelp || num(r.yelp_rating) === null) {
      return `<span class="badge badge-no">not on Yelp</span>`;
    }
    const stars = num(r.yelp_rating);
    const count = num(r.yelp_review_count);
    const text = `${stars}★${count !== null ? ` (${count})` : ""}`;
    return r.yelp_url
      ? `<a href="${esc(r.yelp_url)}" target="_blank" rel="noopener">${esc(text)} ↗</a>`
      : esc(text);
  }

  function scoreCell(key, max) {
    return (r) => {
      const v = num(r[key]);
      if (v === null) return `<span class="muted">${dash}</span>`;
      const pct = Math.max(0, Math.min(100, (v / max) * 100));
      return `<span class="score"><span class="score-bar" style="width:${pct}%"></span>` +
        `<span class="score-val">${v}</span></span>`;
    };
  }

  function flagsCell(r) {
    const out = [];
    if (isTrue(r.reputation_divergence_flag))
      out.push('<span class="badge badge-flag" title="BBB grade A- or better, but Yelp rating under 3">BBB&#8593; Yelp&#8595;</span>');
    if (isTrue(r.accredited_but_low_rated))
      out.push('<span class="badge badge-flag">Accredited, low-rated</span>');
    if (isTrue(r.low_review_volume_flag))
      out.push('<span class="badge badge-soft">Few reviews</span>');
    return out.join(" ") || `<span class="muted">${dash}</span>`;
  }

  function plain(key) {
    return (r) => esc(r[key] ?? "");
  }

  function gapCell(r) {
    const v = num(r.rating_gap_bbb_minus_yelp);
    if (v === null) return `<span class="muted">${dash}</span>`;
    const sign = v > 0 ? "+" : "";
    return `<span class="${v >= 1 ? "gap-pos" : ""}">${sign}${v}</span>`;
  }

  const LEADS_COLUMNS = [
    { key: "name", label: "Business", cls: "name-cell", render: nameCell },
    { key: "city", label: "City", render: cityCell },
    { key: "rating", label: "BBB rating", render: plain("rating") },
    { key: "accredited", label: "Accredited", render: accreditedCell },
    { key: "phone", label: "Phone", render: plain("phone") },
    { key: "website", label: "Website", render: websiteCell },
    { key: "principal_contact", label: "Contact", render: plain("principal_contact") },
    { key: "years_in_business", label: "Years", render: plain("years_in_business") },
    { key: "last_updated", label: "Last updated", render: plain("last_updated") },
  ];

  const INTEL_COLUMNS = [
    { key: "name", label: "Business", cls: "name-cell", render: nameCell },
    { key: "city", label: "City", render: cityCell },
    { key: "rating", label: "BBB rating", render: plain("rating") },
    { key: "yelp_rating", label: "Yelp", render: yelpCell },
    { key: "rating_gap_bbb_minus_yelp", label: "BBB−Yelp gap", render: gapCell },
    { key: "review_need_score", label: "Review-need", render: scoreCell("review_need_score", 100) },
    { key: "lead_priority_score", label: "Lead priority", render: scoreCell("lead_priority_score", 130) },
    { key: "reputation_divergence_flag", label: "Flags", render: flagsCell },
    { key: "last_updated", label: "Last updated", render: plain("last_updated") },
  ];

  const COLUMNS = VIEW === "intel" ? INTEL_COLUMNS : LEADS_COLUMNS;
  const DEFAULT_SORT = VIEW === "intel" ? { key: "lead_priority_score", dir: -1 } : null;

  function buildHead() {
    headRow.innerHTML = COLUMNS.map(
      (c) => `<th data-key="${c.key}">${esc(c.label)}</th>`
    ).join("");
    headRow.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => onSort(th.dataset.key));
    });
  }

  async function init() {
    buildHead();
    try {
      const res = await fetch("data/manifest.json", { cache: "no-store" });
      if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
      manifest = await res.json();
    } catch (err) {
      statusEl.textContent =
        "Couldn't load available data (data/manifest.json missing or invalid).";
      console.error(err);
      return;
    }
    if (!manifest.datasets || manifest.datasets.length === 0) {
      statusEl.textContent = "No datasets published yet.";
      return;
    }

    populateMetros();
    metroSelect.disabled = false;
    industrySelect.disabled = false;
    searchBox.disabled = false;
    statusEl.textContent = `${manifest.datasets.length} dataset(s) available. Pick a location and industry.`;

    metroSelect.addEventListener("change", onMetroChange);
    industrySelect.addEventListener("change", onIndustryChange);
    searchBox.addEventListener("input", renderTable);
    exportBtn.addEventListener("click", exportCsv);
  }

  const uniqueSorted = (vals) => Array.from(new Set(vals)).sort();

  function populateMetros() {
    const metros = uniqueSorted(manifest.datasets.map((d) => d.metro));
    metroSelect.innerHTML =
      '<option value="">Choose a location…</option>' +
      metros.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  }

  function onMetroChange() {
    const metro = metroSelect.value;
    if (!metro) {
      industrySelect.innerHTML = '<option value="">Select a location first</option>';
      clearResults("Pick a location and industry to see results.");
      return;
    }
    const industries = manifest.datasets.filter((d) => d.metro === metro);
    industrySelect.innerHTML =
      '<option value="">Choose an industry…</option>' +
      industries
        .map((d) => `<option value="${esc(d.id)}">${esc(d.industry)} (${d.record_count})</option>`)
        .join("");
    clearResults("Pick an industry to see results.");
  }

  async function onIndustryChange() {
    const id = industrySelect.value;
    if (!id) return clearResults("Pick an industry to see results.");

    currentDataset = manifest.datasets.find((d) => d.id === id);
    statusEl.textContent = `Loading ${currentDataset.industry} in ${currentDataset.metro}…`;
    try {
      const res = await fetch(`data/${currentDataset.file}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      currentRecords = await res.json();
    } catch (err) {
      statusEl.textContent = `Couldn't load ${currentDataset.file}.`;
      console.error(err);
      currentRecords = [];
      return;
    }

    if (DEFAULT_SORT) {
      sortKey = DEFAULT_SORT.key;
      sortDir = DEFAULT_SORT.dir;
      markSortedHeader();
    }
    exportBtn.disabled = currentRecords.length === 0;

    const asOf = manifest.generated_at
      ? new Date(manifest.generated_at).toLocaleDateString()
      : "unknown date";
    statusEl.textContent =
      `${currentRecords.length} businesses — ${currentDataset.industry} in ${currentDataset.metro}. ` +
      `Published ${asOf}.`;

    if (intelNote) {
      if (VIEW === "intel" && currentDataset.has_intel === false) {
        intelNote.hidden = false;
        intelNote.textContent =
          "This dataset was scraped without Yelp enrichment — the Yelp and " +
          "intelligence columns are empty. Re-run the batch for this metro with Yelp enabled.";
      } else {
        intelNote.hidden = true;
      }
    }
    renderTable();
  }

  function clearResults(message) {
    currentRecords = [];
    currentDataset = null;
    tableBody.innerHTML = "";
    emptyState.hidden = true;
    exportBtn.disabled = true;
    if (intelNote) intelNote.hidden = true;
    statusEl.textContent = message;
  }

  function filteredRecords() {
    const q = searchBox.value.trim().toLowerCase();
    let rows = currentRecords;
    if (q) {
      rows = rows.filter((r) => {
        const cats = Array.isArray(r.categories) ? r.categories.join(" ") : "";
        return [r.name, r.city, r.state, r.primary_category_name, r.yelp_name, cats]
          .join(" ").toLowerCase().includes(q);
      });
    }
    if (sortKey) {
      rows = [...rows].sort((a, b) => {
        let av = a[sortKey];
        let bv = b[sortKey];
        const an = num(av);
        const bn = num(bv);
        const numeric = an !== null || bn !== null;
        if (numeric) {
          if (an === null) return 1; // nulls last regardless of dir
          if (bn === null) return -1;
          return (an - bn) * sortDir;
        }
        return String(av ?? "").localeCompare(String(bv ?? "")) * sortDir;
      });
    }
    return rows;
  }

  function onSort(key) {
    if (sortKey === key) sortDir *= -1;
    else { sortKey = key; sortDir = 1; }
    markSortedHeader();
    renderTable();
  }

  function markSortedHeader() {
    headRow.querySelectorAll("th[data-key]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === sortKey && sortDir === 1);
      th.classList.toggle("sorted-desc", th.dataset.key === sortKey && sortDir === -1);
    });
  }

  function renderTable() {
    const rows = filteredRecords();
    emptyState.hidden = rows.length !== 0;
    tableBody.innerHTML = rows
      .map((r) => "<tr>" + COLUMNS.map(
        (c) => `<td${c.cls ? ` class="${c.cls}"` : ""}>${c.render(r)}</td>`
      ).join("") + "</tr>")
      .join("");
  }

  function exportCsv() {
    const rows = filteredRecords();
    if (rows.length === 0) return;
    const columns = Object.keys(rows[0]);
    const cell = (v) => {
      if (v == null) return "";
      const s = typeof v === "object" ? JSON.stringify(v) : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [columns.join(",")];
    for (const row of rows) lines.push(columns.map((c) => cell(row[c])).join(","));
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(currentDataset && currentDataset.id) || "export"}--${VIEW}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  init();
})();
