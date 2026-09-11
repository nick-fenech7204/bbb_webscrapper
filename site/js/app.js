// Static site: no backend, no build step. Reads pre-published JSON
// (data/manifest.json + one file per industry+metro dataset, written by
// scripts/publish_site_data.py) and routes / filters / sorts / exports in
// the browser.
//
// Routes (hash):
//   #/                       -> landing: intro + live stats + CTA
//   #/lists                  -> a card per lead list, filterable
//   #/<dataset-id>           -> that list: one combined lead + intelligence
//                                table (2026-09-11: used to be two separate
//                                views/tabs -- merged into one). A trailing
//                                /intel from an old link still resolves
//                                here, just ignored, so nothing old 404s.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const landingView = $("landing-view");
  const heroStats = $("hero-stats");
  const listsView = $("lists-view");
  const datasetView = $("dataset-view");
  const loadingEl = $("loading");
  const homeCards = $("home-cards");
  const homeEmptyState = $("home-empty-state");
  const homeResultCount = $("home-result-count");
  const filterIndustry = $("filter-industry");
  const filterMetro = $("filter-metro");
  const filterClearBtn = $("filter-clear");
  const dsTitle = $("ds-title");
  const dsSub = $("ds-sub");
  const intelNote = $("intel-note");
  const legend = $("legend");
  const searchBox = $("search-box");
  const resultCount = $("result-count");
  const exportToggle = $("export-toggle");
  const exportPanel = $("export-panel");
  const scrollHint = $("scroll-hint");
  const headRow = $("head-row");
  const tableBody = $("results-body");
  const emptyState = $("empty-state");

  let manifest = null;
  const cache = new Map();   // dataset id -> records[]
  let ds = null;             // current manifest entry
  let renderedDatasetId = null;   // last dataset id actually rendered -> detects a real change
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
  function cityCell(r) {
    const text = (r.city ?? "") + (r.state ? ", " + r.state : "");
    return text ? `<span title="${esc(text)}">${esc(text)}</span>` : "";
  }
  function websiteCell(r) {
    if (!r.website) return "";
    const label = r.website.replace(/^https?:\/\//, "");
    // A long URL just wraps onto another line like everything else in the
    // table now -- title="" is a minor bonus (the full URL on one line on
    // hover), not load-bearing the way it was when this truncated instead.
    return `<a href="${esc(r.website)}" target="_blank" rel="noopener" title="${esc(r.website)}">${esc(label)}</a>`;
  }
  // BBB grade + accreditation combined into one compact cell (was two
  // columns) -- a checkmark reads faster here than a whole separate column.
  function bbbCell(r) {
    const grade = r.rating ? esc(r.rating) : `<span class="muted">${dash}</span>`;
    const check = isTrue(r.accredited)
      ? ' <span class="accredited-check" title="BBB accredited">&#10003;</span>' : "";
    return grade + check;
  }
  // Yelp rating + review count combined into one cell (was two columns).
  function yelpCell(r) {
    if (!r.on_yelp) return `<span class="muted">${dash}</span>`;
    const rating = num(r.yelp_rating);
    const count = num(r.yelp_review_count);
    const text = !count ? "on Yelp" : `${rating}★ (${count})`;
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
  // Badge text is a short version of the real contact_readiness value --
  // narrower column, full wording still lives in the title tooltip and,
  // unabbreviated, in every export.
  const REACH_SHORT = {
    "Phone + named contact": "Phone + contact",
    "Contact/email only, no phone": "No phone",
    "No direct contact info": "Unreachable",
  };
  function reachCell(r) {
    const label = r.contact_readiness;
    if (!label) return `<span class="muted">${dash}</span>`;
    const cls = label === "Phone + named contact" ? "badge-yes"
      : label === "Phone only" ? "badge-soft" : "badge-flag";
    const short = REACH_SHORT[label] || label;
    return `<span class="badge ${cls}" title="${esc(label)}">${esc(short)}</span>`;
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
    if (!out.length) return `<span class="muted">${dash}</span>`;
    // .badge-group gives real flex `gap` between badges, both between two
    // on the same line and between wrapped rows -- a plain text-node space
    // (the old `.join(" ")`) collapses right at a line-wrap boundary, which
    // is what let two badges' rounded pill edges sit flush against each
    // other with no visible gap there.
    return `<span class="badge-group">${out.join("")}</span>`;
  }
  const plain = (key) => (r) => esc(r[key] ?? "");
  // Same, but with a title="" -- a minor bonus (the full value on one
  // line on hover) for a cell whose value might wrap onto a couple of
  // lines in the table itself.
  const plainTitled = (key) => (r) => {
    const v = r[key] ?? "";
    return v ? `<span title="${esc(v)}">${esc(v)}</span>` : "";
  };

  // One combined table now (2026-09-11 -- used to be separate Lead
  // records / Intelligence tabs with their own column sets; merged into
  // one so everything is visible without switching views). Trimmed and
  // combined from the old 11+11 columns down to 13 total specifically to
  // keep this fitting in real screen width: BBB grade+accredited share a
  // cell (bbbCell), Yelp rating+count share a cell (yelpCell), and
  // years-in-business dropped from the visible table (still in every
  // export) as the least scan-critical field. `w` is a pixel width, not a
  // percentage -- see the big comment in style.css for how these columns
  // fit real screen widths without forcing horizontal scroll.
  const COLUMNS = [
    { key: "name", label: "Business", cls: "name-cell", w: 200, render: nameCell },
    { key: "city", label: "City", w: 110, render: cityCell },
    { key: "rating", label: "BBB", w: 75, render: bbbCell },
    { key: "bbb_complaints_total", label: "BBB complaints", w: 105, cls: "num-cell", render: intCell("bbb_complaints_total") },
    { key: "yelp_rating", label: "Yelp", w: 110, cls: "num-cell", render: yelpCell },
    { key: "reputation_score", label: "Reputation (of 100)", w: 110, render: scoreCell("reputation_score", 100) },
    { key: "lead_priority_score", label: "Lead priority (of 130)", w: 110, render: scoreCell("lead_priority_score", 130) },
    { key: "contact_readiness_score", label: "Reach", w: 135, render: reachCell },
    { key: "phone", label: "Phone", w: 105, render: plain("phone") },
    { key: "principal_contact", label: "Contact", w: 130, render: plainTitled("principal_contact") },
    { key: "reputation_divergence_flag", label: "Flags", w: 190, render: flagsCell },
    { key: "website", label: "Website", w: 110, render: websiteCell },
    { key: "last_updated", label: "Last updated", w: 90, render: plain("last_updated") },
  ];
  const columns = () => COLUMNS;

  // ---------- router ----------
  function parseHash() {
    return (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  }

  async function route() {
    if (!manifest) return;
    const parts = parseHash();
    if (parts.length === 0) { showLanding(); return; }
    if (parts[0] === "lists") { showLists(); return; }
    const entry = manifest.datasets.find((d) => d.id === parts[0]);
    if (!entry) { showLists(); return; }  // unknown id -> the index, not the marketing page
    await showDataset(entry);  // parts[1] (an old /intel link) is ignored -- one combined view now
  }

  // ---------- landing ----------
  function showLanding() {
    ds = null;
    listsView.hidden = true;
    datasetView.hidden = true;
    loadingEl.hidden = true;
    landingView.hidden = false;
    document.title = "LossLess — Lead Generation Platform";

    const datasets = manifest.datasets;
    const totalBiz = datasets.reduce((sum, d) => sum + d.record_count, 0);
    const totalYelp = datasets.reduce((sum, d) => sum + d.yelp_matched, 0);
    const metros = new Set(datasets.map((d) => d.metro)).size;
    const stats = [
      [datasets.length.toLocaleString(), `lead list${datasets.length === 1 ? "" : "s"}`],
      [totalBiz.toLocaleString(), "businesses tracked"],
      [metros.toLocaleString(), `market${metros === 1 ? "" : "s"}`],
      [totalYelp.toLocaleString(), "matched to Yelp"],
    ];
    heroStats.innerHTML = stats.map(([num, label]) =>
      `<div class="stat"><span class="stat-num">${esc(num)}</span><span class="stat-label">${esc(label)}</span></div>`
    ).join("");
  }

  // ---------- lists ----------
  // Options are built once off the full manifest and left alone after
  // that -- the two dropdowns stay independent (each always lists every
  // industry/metro, not narrowed by the other's current pick) since with
  // a modest number of lists that's simpler and just as usable as the
  // options narrowing each other would be. Selections persist in the
  // <select> elements themselves across navigating away and back, same as
  // a dataset table's sort state does.
  let homeFiltersReady = false;
  function populateHomeFilters() {
    if (homeFiltersReady) return;
    homeFiltersReady = true;
    const industries = [...new Set(manifest.datasets.map((d) => d.industry))].sort();
    const metros = [...new Set(manifest.datasets.map((d) => d.metro))].sort();
    filterIndustry.innerHTML += industries.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
    filterMetro.innerHTML += metros.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
    filterIndustry.addEventListener("change", renderHomeCards);
    filterMetro.addEventListener("change", renderHomeCards);
    filterClearBtn.addEventListener("click", () => {
      filterIndustry.value = "";
      filterMetro.value = "";
      renderHomeCards();
    });
  }

  function filteredDatasets() {
    const industry = filterIndustry.value;
    const metro = filterMetro.value;
    return manifest.datasets.filter((d) =>
      (!industry || d.industry === industry) && (!metro || d.metro === metro));
  }

  function renderHomeCards() {
    const total = manifest.datasets.length;
    const rows = filteredDatasets();
    homeEmptyState.hidden = rows.length !== 0;
    filterClearBtn.hidden = !(filterIndustry.value || filterMetro.value);
    homeResultCount.textContent = rows.length === total
      ? `${rows.length.toLocaleString()} list${rows.length === 1 ? "" : "s"}`
      : `${rows.length.toLocaleString()} of ${total.toLocaleString()} lists`;

    homeCards.innerHTML = rows.map((d) => {
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
  }

  function showLists() {
    ds = null;
    landingView.hidden = true;
    datasetView.hidden = true;
    loadingEl.hidden = true;
    listsView.hidden = false;
    document.title = "Lead lists — LossLess";

    populateHomeFilters();
    renderHomeCards();

    const asOf = manifest.generated_at
      ? new Date(manifest.generated_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : null;
    listsView.querySelector(".view-sub").innerHTML =
      `One list per market: BBB details, matched Yelp data, and lead scoring in a ` +
      `single sortable, filterable <strong>table</strong>.` +
      (asOf ? ` <span class="muted">Data published ${asOf}.</span>` : "");
  }

  // ---------- dataset ----------
  async function showDataset(entry) {
    landingView.hidden = true;
    listsView.hidden = true;
    ds = entry;
    const changed = ds.id !== renderedDatasetId;
    renderedDatasetId = ds.id;

    legend.hidden = false;
    document.title = `${ds.industry} — ${ds.metro} — LossLess`;

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

    intelNote.hidden = ds.has_yelp !== false;
    if (!intelNote.hidden) {
      intelNote.textContent =
        "No Yelp match data for this list — the scores here use BBB signal only " +
        "(grade, reviews, complaints). Re-run the batch for this market with Yelp enabled to add the Yelp columns.";
    }

    if (changed) {
      sortKey = "lead_priority_score";
      sortDir = -1;
      // .table-wrap scrolls independently now (a bounded, self-scrolling
      // panel, not the whole page -- see its comment in style.css), so
      // switching dataset has to reset ITS scroll explicitly too --
      // replacing the row HTML alone doesn't reset a container's own
      // scroll position, and leftover scroll would show the newly-sorted
      // table starting mid-list instead of at its real top row.
      const tw = document.querySelector(".table-wrap");
      if (tw) { tw.scrollTop = 0; tw.scrollLeft = 0; }
    }
    exportToggle.disabled = records.length === 0;
    buildHead();
    renderTable();
    window.scrollTo(0, 0);
  }

  function buildHead() {
    const cols = columns();
    // min-width, not width: on a normal/narrow screen this is the table's
    // real width (table-layout:fixed sizes columns from their own widths,
    // which sum to this), and .table-wrap scrolls sideways past it same
    // as always. On a wide/ultrawide monitor where .table-wrap itself is
    // wider than this sum, style.css's `table { width: 100% }` takes over
    // instead -- fixed layout distributes that extra width proportionally
    // across every column rather than leaving it blank past the table's
    // right edge (min-width is only a floor, never a ceiling, so it
    // doesn't fight that).
    const totalWidth = cols.reduce((sum, c) => sum + c.w, 0);
    document.getElementById("results-table").style.minWidth = `${totalWidth}px`;
    headRow.innerHTML = cols.map((c) =>
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
    syncScrollHint();
  }

  // Only claim the table scrolls sideways when it actually does at the
  // viewer's own screen width -- reading scrollWidth/clientWidth forces
  // the layout the browser already needs to do, so this is a real
  // measurement each render, not a guess. A static "scroll for more" line
  // that isn't true on a wide monitor is worse than saying nothing.
  function syncScrollHint() {
    const tw = document.querySelector(".table-wrap");
    scrollHint.hidden = !tw || tw.scrollWidth <= tw.clientWidth;
  }

  // ---------- export ----------
  const exportBasename = () => (ds ? ds.id : "export");

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // CSV/Excel ship the FULL record (every field), not just the columns
  // visible in the current view -- those are data-interchange formats, the
  // download is the complete data a rep might want in a spreadsheet.
  // Nested list/dict fields (categories, contacts...) flatten to JSON text
  // since neither a CSV nor a spreadsheet cell holds a real array.
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

  // PDF/Text are read, not processed further -- plain-text, same filter +
  // sort as on screen. Reusing each column's HTML render() and stripping
  // tags (rather than a second parallel "as text" function per column)
  // keeps one source of truth: whatever a cell displays is exactly what
  // gets exported.
  const scratchEl = document.createElement("div");
  function cellText(col, r) {
    scratchEl.innerHTML = col.render(r);
    return scratchEl.textContent.replace(/\s+/g, " ").trim();
  }
  function visibleRowsForExport() {
    const cols = columns();
    const rows = filteredRecords().map((r) => cols.map((c) => cellText(c, r)));
    return { cols, rows };
  }
  const exportTitle = () => (ds ? `${ds.industry} — ${ds.metro}` : "Export");

  // jsPDF's built-in fonts only cover the WinAnsi codepage -- the ★ Yelp
  // rating star, ✓ accreditation check, and ↗ external-link arrow used on
  // screen are well outside it and came out as missing-glyph boxes/blanks
  // in the PDF. Swap them for plain ASCII before anything reaches jsPDF.
  // Text export doesn't need this -- a real UTF-8 .txt file has no such
  // font limitation, so it keeps the on-screen glyphs as-is.
  const PDF_UNSAFE = [[/★/g, ""], [/✓/g, " (Accred.)"], [/\s*↗/g, ""], [/—/g, "-"]];
  const pdfSafe = (s) => PDF_UNSAFE.reduce((acc, [re, rep]) => acc.replace(re, rep), s)
    .replace(/\s+/g, " ").trim();

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
    downloadBlob(new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" }), `${exportBasename()}.csv`);
  }

  function exportXlsx() {
    if (typeof XLSX === "undefined") {
      alert("Excel export didn't load (probably a blocked script) -- try another format, or reload the page.");
      return;
    }
    const rows = exportableRows();
    if (!rows.length) return;
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Leads");
    XLSX.writeFile(wb, `${exportBasename()}.xlsx`);
  }

  // The PDF is a print-style summary sheet, not the full table -- the
  // essentials for "who do I call and why" at a size that's actually
  // readable on a landscape page, not all 13 on-screen columns crammed
  // in and wrapped down to a few characters wide. Full data is always in
  // CSV/Excel regardless, and Text keeps the complete current column set
  // if that's what's wanted in a plain-text form.
  const PDF_COLUMN_KEYS = [
    "name", "city", "rating", "yelp_rating", "lead_priority_score",
    "contact_readiness_score", "phone", "principal_contact",
    "reputation_divergence_flag",
  ];
  // mm, sums to a landscape A4 page's usable width (297mm - 2x10mm
  // margin) -- explicit so autoTable never has to auto-shrink a column to
  // fit, which is what caused the illegible wrapping before. BBB (index 2)
  // needs more than its grade alone (e.g. "A+") suggests: an accredited
  // business gets "A+ (Accred.)" appended (see PDF_UNSAFE below), and that
  // needs real room too, not just the couple of characters "A+" implies.
  const PDF_COLUMN_WIDTHS = [42, 24, 26, 26, 20, 32, 24, 32, 41];

  function exportPdf() {
    if (typeof window.jspdf === "undefined") {
      alert("PDF export didn't load (probably a blocked script) -- try another format, or reload the page.");
      return;
    }
    const cols = PDF_COLUMN_KEYS.map((key) => COLUMNS.find((c) => c.key === key));
    const rows = filteredRecords().map((r) => cols.map((c) => pdfSafe(cellText(c, r))));
    if (!rows.length) return;

    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: "landscape" });
    doc.setFontSize(13);
    doc.text(pdfSafe(exportTitle()), 14, 12);
    const columnStyles = {};
    PDF_COLUMN_WIDTHS.forEach((w, i) => { columnStyles[i] = { cellWidth: w }; });
    doc.autoTable({
      head: [cols.map((c) => pdfSafe(c.label))],
      body: rows,
      startY: 18,
      styles: { fontSize: 9, cellPadding: 3, overflow: "linebreak" },
      headStyles: { fillColor: [23, 97, 74], fontSize: 9 },
      columnStyles,
      margin: { left: 10, right: 10 },
    });
    doc.save(`${exportBasename()}.pdf`);
  }

  function exportTxt() {
    const { cols, rows } = visibleRowsForExport();
    if (!rows.length) return;
    const rule = "-".repeat(40);
    const blocks = rows.map((vals) => cols.map((c, i) => `${c.label}: ${vals[i] || dash}`).join("\n"));
    const text = `${exportTitle()}\n${rows.length.toLocaleString()} record(s)\n\n${rule}\n\n` +
      blocks.join(`\n\n${rule}\n\n`);
    downloadBlob(new Blob([text], { type: "text/plain;charset=utf-8" }), `${exportBasename()}.txt`);
  }

  const EXPORTERS = { csv: exportCsv, xlsx: exportXlsx, pdf: exportPdf, txt: exportTxt };

  function closeExportPanel() {
    exportPanel.hidden = true;
    exportToggle.setAttribute("aria-expanded", "false");
  }

  // ---------- init ----------
  async function init() {
    searchBox.addEventListener("input", renderTable);
    window.addEventListener("hashchange", route);
    window.addEventListener("resize", () => { if (!datasetView.hidden) syncScrollHint(); });

    exportToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const opening = exportPanel.hidden;
      exportPanel.hidden = !opening;
      exportToggle.setAttribute("aria-expanded", String(opening));
    });
    exportPanel.addEventListener("click", (e) => {
      const btn = e.target.closest(".export-item");
      if (!btn) return;
      closeExportPanel();
      EXPORTERS[btn.dataset.format]?.();
    });
    document.addEventListener("click", (e) => {
      if (!exportPanel.hidden && !exportPanel.contains(e.target) && e.target !== exportToggle) closeExportPanel();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeExportPanel(); });

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
