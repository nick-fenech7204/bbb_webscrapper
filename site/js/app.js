// Static site: no backend, no build step. Reads pre-published JSON
// (data/manifest.json + one file per industry+metro dataset, written by
// scripts/publish_site_data.py) and routes / filters / sorts / exports in
// the browser.
//
// Routes (hash):
//   #/                       -> home: a card per lead list
//   #/<dataset-id>           -> that list, Lead records view
//   #/<dataset-id>/intel     -> that list, Intelligence view
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const homeView = $("home-view");
  const datasetView = $("dataset-view");
  const loadingEl = $("loading");
  const homeCards = $("home-cards");
  const dsTitle = $("ds-title");
  const dsSub = $("ds-sub");
  const tabLeads = $("tab-leads");
  const tabIntel = $("tab-intel");
  const intelNote = $("intel-note");
  const legend = $("legend");
  const searchBox = $("search-box");
  const resultCount = $("result-count");
  const exportCsvBtn = $("export-csv-btn");
  const exportXlsxBtn = $("export-xlsx-btn");
  const headRow = $("head-row");
  const tableBody = $("results-body");
  const emptyState = $("empty-state");

  let manifest = null;
  const cache = new Map();   // dataset id -> records[]
  let ds = null;             // current manifest entry
  let view = "leads";
  let renderedKey = null;    // "<id>|<view>" last rendered -> detects a real change
  let records = [];
  let sortKey = null;
  let sortDir = 1;

  // ---------- helpers ----------
  const TRUEISH = new Set(["true", "1", "yes", "y", "t"]);
  const isTrue = (v) => (typeof v === "boolean" ? v : TRUEISH.has(String(v).toLowerCase()));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));
  const dash = "—";

  // ---------- cell renderers ----------
  function nameCell(r) {
    const label = esc(r.name);
    return r.profile_url
      ? `<a href="${esc(r.profile_url)}" target="_blank" rel="noopener">${label}</a>` : label;
  }
  function cityCell(r) { return esc(r.city) + (r.state ? ", " + esc(r.state) : ""); }
  function websiteCell(r) {
    if (!r.website) return "";
    const label = r.website.replace(/^https?:\/\//, "");
    // class="trunc" -- some scraped URLs carry long UTM/tracking query
    // strings that would otherwise blow out the whole column's width.
    return `<a class="trunc" href="${esc(r.website)}" target="_blank" rel="noopener" title="${esc(r.website)}">${esc(label)}</a>`;
  }
  function accreditedCell(r) {
    return isTrue(r.accredited)
      ? '<span class="badge badge-yes">Accredited</span>'
      : `<span class="muted">${dash}</span>`;
  }
  function yelpRatingCell(r) {
    if (!r.on_yelp) return `<span class="muted">${dash}</span>`;
    const rating = num(r.yelp_rating);
    const count = num(r.yelp_review_count);
    const text = !count ? "on Yelp" : `${rating}★`;
    return r.yelp_url
      ? `<a href="${esc(r.yelp_url)}" target="_blank" rel="noopener">${esc(text)} ↗</a>` : esc(text);
  }
  function intCell(key) {
    return (r) => {
      const v = num(r[key]);
      return v === null ? `<span class="muted">${dash}</span>` : String(v);
    };
  }
  // Reach: can this business actually be contacted today? Phone + a named
  // BBB contact is the best case, a bare phone is fine, anything less gets
  // flagged -- the badge color mirrors that (see contact_readiness_score
  // in bbb_scraper/match/merge.py for how the label itself is decided).
  function reachCell(r) {
    const label = r.contact_readiness;
    if (!label) return `<span class="muted">${dash}</span>`;
    const cls = label === "Phone + named contact" ? "badge-yes"
      : label === "Phone only" ? "badge-soft" : "badge-flag";
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }
  function scoreCell(key, max) {
    return (r) => {
      const v = num(r[key]);
      if (v === null) return `<span class="muted">${dash}</span>`;
      const pct = Math.max(2, Math.min(100, (v / max) * 100));
      return `<span class="score"><span class="score-bar" style="width:${pct}%"></span><span class="score-val">${v}</span></span>`;
    };
  }
  function flagsCell(r) {
    const out = [];
    if (isTrue(r.reputation_divergence_flag))
      out.push('<span class="badge badge-flag" title="BBB grade looks clean but the actual customer feedback doesn\'t">Reputation gap</span>');
    if (isTrue(r.accredited_but_low_rated))
      out.push('<span class="badge badge-flag">Accredited, low-rated</span>');
    if (isTrue(r.low_review_volume_flag))
      out.push('<span class="badge badge-soft">Few reviews</span>');
    return out.join(" ") || `<span class="muted">${dash}</span>`;
  }
  const plain = (key) => (r) => esc(r[key] ?? "");

  // `w` is a pixel width, not a percentage -- the table is allowed to be
  // wider than its container (.table-wrap scrolls it sideways) instead of
  // every column being crushed to fit. See the big comment in style.css.
  const LEADS_COLUMNS = [
    { key: "name", label: "Business", cls: "name-cell", w: 260, render: nameCell },
    { key: "city", label: "City", w: 150, render: cityCell },
    { key: "rating", label: "BBB grade", w: 100, render: plain("rating") },
    { key: "accredited", label: "Accredited", w: 120, render: accreditedCell },
    { key: "bbb_complaints_total", label: "BBB complaints", w: 130, cls: "num-cell", render: intCell("bbb_complaints_total") },
    { key: "phone", label: "Phone", w: 150, render: plain("phone") },
    { key: "website", label: "Website", w: 190, render: websiteCell },
    { key: "principal_contact", label: "Contact", w: 180, render: plain("principal_contact") },
    { key: "contact_readiness_score", label: "Reach", w: 190, render: reachCell },
    { key: "years_in_business", label: "Years", w: 90, cls: "num-cell", render: plain("years_in_business") },
    { key: "last_updated", label: "Last updated", w: 130, render: plain("last_updated") },
  ];
  const INTEL_COLUMNS = [
    { key: "name", label: "Business", cls: "name-cell", w: 260, render: nameCell },
    { key: "city", label: "City", w: 140, render: cityCell },
    { key: "rating", label: "BBB grade", w: 100, render: plain("rating") },
    { key: "bbb_complaints_total", label: "BBB complaints", w: 130, cls: "num-cell", render: intCell("bbb_complaints_total") },
    { key: "yelp_rating", label: "Yelp ★", w: 110, cls: "num-cell", render: yelpRatingCell },
    { key: "yelp_review_count", label: "Yelp #", w: 100, cls: "num-cell", render: intCell("yelp_review_count") },
    { key: "reputation_score", label: "Reputation", w: 170, render: scoreCell("reputation_score", 100) },
    { key: "lead_priority_score", label: "Lead priority", w: 170, render: scoreCell("lead_priority_score", 130) },
    { key: "contact_readiness_score", label: "Reach", w: 190, render: reachCell },
    { key: "reputation_divergence_flag", label: "Flags", w: 300, render: flagsCell },
    { key: "last_updated", label: "Last updated", w: 130, render: plain("last_updated") },
  ];
  const columns = () => (view === "intel" ? INTEL_COLUMNS : LEADS_COLUMNS);

  // ---------- router ----------
  function parseHash() {
    const parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
    return { id: parts[0] || null, view: parts[1] === "intel" ? "intel" : "leads" };
  }

  async function route() {
    if (!manifest) return;
    const { id, view: v } = parseHash();
    const entry = id && manifest.datasets.find((d) => d.id === id);
    if (!entry) { showHome(); return; }
    view = v;
    await showDataset(entry);
  }

  // ---------- home ----------
  function showHome() {
    ds = null;
    datasetView.hidden = true;
    loadingEl.hidden = true;
    homeView.hidden = false;
    document.title = "Lead Intelligence — BBB + Yelp";

    const asOf = manifest.generated_at
      ? new Date(manifest.generated_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : null;

    homeCards.innerHTML = manifest.datasets.map((d) => {
      const chips = [`${d.record_count.toLocaleString()} businesses`];
      chips.push(d.has_yelp ? `${d.yelp_matched} matched to Yelp` : "BBB only");
      if (d.top_lead_score) chips.push(`top lead ${d.top_lead_score}`);
      return `<a class="lead-card" href="#/${encodeURIComponent(d.id)}">
        <div class="lead-card-body">
          <h3>${esc(d.industry)}</h3>
          <p class="lead-card-loc">${esc(d.metro)}</p>
          <div class="chips">${chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>
        </div>
        <div class="lead-card-foot"><span>View list</span><span aria-hidden="true">&rarr;</span></div>
      </a>`;
    }).join("");

    homeView.querySelector(".view-sub").innerHTML =
      `One list per market. Each has a browsable <strong>lead table</strong> and a scored ` +
      `<strong>intelligence</strong> view.` + (asOf ? ` <span class="muted">Data published ${asOf}.</span>` : "");
  }

  // ---------- dataset ----------
  async function showDataset(entry) {
    homeView.hidden = true;
    ds = entry;
    const key = `${ds.id}|${view}`;
    const changed = key !== renderedKey;
    renderedKey = key;

    tabLeads.href = `#/${encodeURIComponent(ds.id)}`;
    tabIntel.href = `#/${encodeURIComponent(ds.id)}/intel`;
    tabLeads.classList.toggle("active", view === "leads");
    tabIntel.classList.toggle("active", view === "intel");
    legend.hidden = false;
    legend.querySelectorAll("li[data-view]").forEach((li) => {
      li.hidden = !(li.dataset.view === "both" || li.dataset.view === view);
    });
    document.title = `${ds.industry} — ${ds.metro} — Lead Intelligence`;

    if (!cache.has(ds.id)) {
      datasetView.hidden = true;
      loadingEl.hidden = false;
      try {
        const res = await fetch(`data/${ds.file}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        cache.set(ds.id, await res.json());
      } catch (err) {
        loadingEl.textContent = `Couldn't load ${ds.file}.`;
        console.error(err);
        return;
      }
    }
    records = cache.get(ds.id);
    loadingEl.hidden = true;
    datasetView.hidden = false;

    dsTitle.textContent = `${ds.industry} — ${ds.metro}`;
    const asOf = manifest.generated_at ? new Date(manifest.generated_at).toLocaleDateString() : "unknown";
    dsSub.textContent =
      `${records.length.toLocaleString()} businesses` +
      (ds.has_yelp ? `, ${ds.yelp_matched} matched to Yelp` : ", BBB only") +
      ` · published ${asOf}`;

    intelNote.hidden = !(view === "intel" && ds.has_yelp === false);
    if (!intelNote.hidden) {
      intelNote.textContent =
        "No Yelp match data for this list — the scores here use BBB signal only " +
        "(grade, reviews, complaints). Re-run the batch for this market with Yelp enabled to add the Yelp columns.";
    }

    if (changed) {
      sortKey = view === "intel" ? "lead_priority_score" : null;
      sortDir = -1;
    }
    exportCsvBtn.disabled = records.length === 0;
    exportXlsxBtn.disabled = records.length === 0;
    buildHead();
    renderTable();
    window.scrollTo(0, 0);
  }

  function buildHead() {
    headRow.innerHTML = columns().map((c) =>
      `<th data-key="${c.key}"${c.cls ? ` class="${c.cls}"` : ""} style="width:${c.w}px">${esc(c.label)}</th>`
    ).join("");
    headRow.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => onSort(th.dataset.key));
    });
    markSortedHeader();
  }

  function filteredRecords() {
    const q = searchBox.value.trim().toLowerCase();
    let rows = records;
    if (q) {
      rows = rows.filter((r) => {
        const cats = Array.isArray(r.categories) ? r.categories.join(" ") : "";
        return [r.name, r.city, r.state, r.primary_category_name, r.yelp_name, cats]
          .join(" ").toLowerCase().includes(q);
      });
    }
    if (sortKey) {
      rows = [...rows].sort((a, b) => {
        const av = a[sortKey], bv = b[sortKey];
        const an = num(av), bn = num(bv);
        if (an !== null || bn !== null) {
          if (an === null) return 1;
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
    const cols = columns();
    tableBody.innerHTML = rows.map((r) =>
      "<tr>" + cols.map((c) => `<td${c.cls ? ` class="${c.cls}"` : ""}>${c.render(r)}</td>`).join("") + "</tr>"
    ).join("");
    resultCount.textContent = rows.length === records.length
      ? `${rows.length.toLocaleString()} record${rows.length === 1 ? "" : "s"}`
      : `${rows.length.toLocaleString()} of ${records.length.toLocaleString()} records`;
  }

  // Both exports ship the FULL record (every field), not just the columns
  // visible in the current view -- the on-screen table is a curated look,
  // the download is the complete data a rep might want in a spreadsheet.
  // Nested list/dict fields (categories, contacts...) flatten to JSON text
  // since neither CSV nor a spreadsheet cell holds a real array.
  function exportableRows() {
    return filteredRecords().map((row) => {
      const out = {};
      for (const k of Object.keys(row)) {
        const v = row[k];
        out[k] = v != null && typeof v === "object" ? JSON.stringify(v) : v;
      }
      return out;
    });
  }

  function exportCsv() {
    const rows = exportableRows();
    if (!rows.length) return;
    const cols = Object.keys(rows[0]);
    const cell = (v) => {
      if (v == null) return "";
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [cols.join(",")];
    for (const row of rows) lines.push(cols.map((c) => cell(row[c])).join(","));
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${ds ? ds.id : "export"}--${view}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function exportXlsx() {
    if (typeof XLSX === "undefined") {
      alert("Excel export didn't load (probably a blocked script) -- use Export CSV instead, or reload the page.");
      return;
    }
    const rows = exportableRows();
    if (!rows.length) return;
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Leads");
    XLSX.writeFile(wb, `${ds ? ds.id : "export"}--${view}.xlsx`);
  }

  // ---------- init ----------
  async function init() {
    searchBox.addEventListener("input", renderTable);
    exportCsvBtn.addEventListener("click", exportCsv);
    exportXlsxBtn.addEventListener("click", exportXlsx);
    window.addEventListener("hashchange", route);

    try {
      const res = await fetch("data/manifest.json", { cache: "no-store" });
      if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
      manifest = await res.json();
    } catch (err) {
      loadingEl.textContent = "Couldn't load available data (data/manifest.json missing or invalid).";
      console.error(err);
      return;
    }
    if (!manifest.datasets || !manifest.datasets.length) {
      loadingEl.textContent = "No lead lists published yet.";
      return;
    }
    route();
  }

  init();
})();
