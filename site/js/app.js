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
  const filterSort = $("filter-sort");
  const filterClearBtn = $("filter-clear");
  const dsTitle = $("ds-title");
  const dsSub = $("ds-sub");
  const intelNote = $("intel-note");
  const legend = $("legend");
  const searchBox = $("search-box");
  const mobileSort = $("mobile-sort");
  const resultCount = $("result-count");
  const exportToggle = $("export-toggle");
  const exportPanel = $("export-panel");
  const scrollHint = $("scroll-hint");
  const headRow = $("head-row");
  const tableBody = $("results-body");
  const emptyState = $("empty-state");
  const mobileCards = $("mobile-cards");

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
  // Returns null for anything that isn't genuinely numeric -- a plain
  // `Number(v)` on a non-empty non-numeric string (a name, a phone number,
  // an ISO date) produces NaN, not null, which used to sneak this straight
  // into filteredRecords()'s numeric sort branch below instead of falling
  // through to the string branch (NaN !== null is true). That made every
  // text column's comparator always return NaN -- Array.prototype.sort
  // treats NaN as "no order," so clicking any text column header (Business,
  // City, Top complaint, Latest review, Phone, Contact, Website) silently
  // left the row order unchanged. Caught 2026-09-16 in a full-project audit.
  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  };
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
  // Human phrase per bbb_scraper.webcheck status -- see its docstring for
  // why only these three ever set website_dead_flag (403/5xx/etc. are
  // deliberately NOT asserted dead, so they never reach this map).
  const WEBSITE_DEAD_REASON = {
    dead_404: "Page not found (404)",
    dead_unreachable: "Site doesn't load",
    dead_parked: "Looks like a parked/for-sale domain",
  };
  // Just the bare domain for display (no www., no path/query/hash) -- the
  // link itself (href, title, "Website down" detection) always keeps
  // using the real, full r.website URL unchanged, so it still opens
  // exactly the right page. Real captured data included plenty of
  // https://www.example.com/some-page/?utm=... style URLs that read as
  // clutter in a table cell; a rep just needs "is this a real, working
  // site" at a glance, not the specific page BBB happened to link to.
  function cleanDomain(url) {
    try {
      return new URL(url).hostname.replace(/^www\./i, "");
    } catch {
      // Malformed/relative URL (real BBB data is not always a clean
      // absolute URL) -- best-effort strip rather than showing nothing.
      return url.replace(/^https?:\/\//i, "").replace(/^www\./i, "").split(/[/?#]/)[0];
    }
  }
  function websiteCell(r) {
    if (!r.website) return "";
    const label = cleanDomain(r.website);
    if (isTrue(r.website_dead_flag)) {
      const reason = WEBSITE_DEAD_REASON[r.website_status] || "Website appears down";
      // Still a real link (a rep may want to double-check by hand) -- just
      // badge-styled instead of looking like an ordinary working link.
      return `<a href="${esc(r.website)}" target="_blank" rel="noopener" class="badge badge-flag" title="${esc(reason)} — ${esc(r.website)}">Website down</a>`;
    }
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
  // Yelp + Angi ratings share one cell (2026-09-14: was Yelp-only, same
  // "combine related cells" reasoning as bbbCell above -- a whole extra
  // column per 3rd-party source doesn't fit real screen width, and these
  // two are the same *kind* of signal: an outside homeowner-review
  // platform, exactly like lead_priority_score's _rating_band treats them).
  function oneRatingLink(label, url, rating, count, title) {
    const text = !count ? `on ${label}` : `${rating}★ (${count})`;
    const titleAttr = title ? ` title="${esc(title)}"` : "";
    return url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener"${titleAttr}>${esc(text)} ↗</a>` : esc(text);
  }
  function yelpCell(r) {
    const parts = [];
    if (r.on_yelp) {
      // 2026-09-17: yelp_rating/yelp_review_count/yelp_url can also come
      // from MapQuest's own Yelp-sourced data (a second real match pass,
      // separate from the official Yelp Fusion API one) when that's the
      // only place this business's Yelp data showed up -- see
      // publish_site_data.py's select_public_fields_from_master. yelp_url
      // is a genuine yelp.com link either way (mapquest_rating_url is a
      // real yelp.com/biz/... URL, confirmed live -- see
      // bbb_scraper/mapquest/client.py's module docstring), so no "wrong
      // site" warning is needed -- the title just notes how the match was
      // found, for anyone curious why a business shows a Yelp rating with
      // no official-match badge elsewhere.
      const viaMapquest = isTrue(r.yelp_via_mapquest);
      const title = viaMapquest ? "Yelp rating found via MapQuest" : "";
      parts.push(oneRatingLink("Yelp", r.yelp_url, num(r.yelp_rating), num(r.yelp_review_count), title));
    }
    if (isTrue(r.on_angi)) parts.push(oneRatingLink("Angi", r.angi_url, num(r.angi_rating), num(r.angi_review_count)));
    // BBB's own customer-review average (2026-09-17, Nick's call: treat it
    // the same as Yelp/Angi here, not second-class -- it already fed
    // lead_priority_score via the same _rating_band curve, but had no
    // visible column of its own until now). Server-side null (under 3
    // reviews behind it, or none at all -- see merge.py's
    // _bbb_review_avg) means simply not shown, same gating as on_yelp/
    // on_angi above.
    const bbbAvg = num(r.bbb_review_avg);
    if (bbbAvg !== null) parts.push(oneRatingLink("BBB", r.profile_url, bbbAvg, num(r.bbb_reviews_total)));
    if (!parts.length) return `<span class="muted">${dash}</span>`;
    return parts.join("<br>");
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
  // How many independent places corroborate this business exists and has a
  // real reputation, not raw platform count -- MapQuest doesn't get its own
  // entry since it's a second door into the SAME Yelp data on_yelp already
  // covers (see merge.py's own on_yelp/_has_mapquest_yelp_data), and
  // sentiment isn't a source at all, it's an analysis of review text from
  // the sources below. BBB always counts -- every row is a BBB record by
  // construction, so 1/4 is the real floor, not a bug.
  const ALL_SOURCES = ["BBB", "Yelp", "Angi", "Facebook"];
  function sourcesFor(r) {
    const sources = ["BBB"];
    if (isTrue(r.on_yelp)) sources.push("Yelp");
    if (isTrue(r.on_angi)) sources.push("Angi");
    if (r.facebook_status === "ok") sources.push("Facebook");
    return sources;
  }
  function sourcesCell(r) {
    const sources = sourcesFor(r);
    const cls = sources.length >= 3 ? "badge-yes" : sources.length === 2 ? "badge-soft" : "badge-flag";
    const missing = ALL_SOURCES.filter((s) => !sources.includes(s));
    const title = missing.length
      ? `Have: ${sources.join(", ")} — missing: ${missing.join(", ")}`
      : `Have: ${sources.join(", ")} — every source`;
    return `<span class="badge ${cls}" title="${esc(title)}">${sources.length}/${ALL_SOURCES.length} sources</span>`;
  }
  function scoreCell(key, max) {
    return (r) => {
      const v = num(r[key]);
      if (v === null) return `<span class="muted">${dash}</span>`;
      const pct = Math.max(2, Math.min(100, (v / max) * 100));
      // review_sentiment_signal (published, but otherwise shown nowhere --
      // caught in a full-project audit, 2026-09-16) is one of the inputs
      // averaged into lead_priority_score -- worth a hover on the one
      // column reps actually sort/scan by, not its own column.
      const sentiment = key === "lead_priority_score" ? num(r.review_sentiment_signal) : null;
      const title = sentiment !== null ? ` title="Review-sentiment signal: ${sentiment}/100"` : "";
      return `<span class="score"${title}><span class="score-bar" style="width:${pct}%"></span><span class="score-val">${v}</span></span>`;
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
    // 2026-09-16: surfaces bbb_scraper.match.merge's review_gap_flag (a
    // separate +6 bonus in lead_priority_score, distinct from the
    // sentiment/divergence signals above) -- gone quiet vs. its OWN normal
    // review cadence, not a fixed day count.
    if (isTrue(r.review_gap_flag))
      out.push('<span class="badge badge-soft" title="Reviews have gone notably quiet compared to this business\'s own history">Gone quiet</span>');
    // A separate sales angle from the reputation flags above: no working
    // site at all is a website lead, independent of whether their
    // reputation also needs help.
    if (isTrue(r.website_dead_flag))
      out.push('<span class="badge badge-flag" title="Their own website is down, 404ing, or a parked domain">No live website</span>');
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
  // "Latest negative review" -- 2026-09-17, Nick's call: the PRIMARY value
  // is most_recent_negative_review_date when there is one (a recent
  // negative is a sharper, more actionable lead signal than "reviewed at
  // all recently"), but a business with only positive/mixed-free reviews
  // shouldn't just show blank -- that read as a bug ("why is this empty")
  // rather than the good news it actually is. Falls back to
  // most_recent_review_date (any sentiment) with a hover explicitly
  // saying so, so it's never mistaken for a negative date. Both date
  // fields pool Yelp/Angi/BBB equally at the source (bbb_scraper.
  // sentiment's _aggregate, 2026-09-17: BBB stopped being second-class
  // here once its own review text became a real, automatically-fetched
  // source). Genuinely blank only when there's no review date of any
  // sentiment on file at all.
  function latestReviewCell(r) {
    const negDate = r.most_recent_negative_review_date ?? "";
    const anyDate = r.most_recent_review_date ?? "";
    const anySource = r.most_recent_review_source ?? "";

    if (negDate) {
      const negSource = r.most_recent_negative_review_source ?? "";
      const parts = [];
      if (negSource) parts.push(`Source: ${negSource}`);
      if (anyDate && anyDate !== negDate) {
        parts.push(`Latest review (any sentiment): ${anyDate}${anySource ? ` (${anySource})` : ""}`);
      }
      const title = parts.join(". ");
      return title ? `<span title="${esc(title)}">${esc(negDate)}</span>` : esc(negDate);
    }
    if (anyDate) {
      const title = `No negative review on file -- latest review of any sentiment${anySource ? ` (${anySource})` : ""}`;
      return `<span title="${esc(title)}">${esc(anyDate)}</span>`;
    }
    return "";
  }
  // Specialties (Angi's "; "-joined services-offered list, see
  // bbb_scraper/angi/parsing.py + scripts/scrape_angi_category.py) is
  // unbounded -- real data runs from 1 item to 200+ for a business with a
  // sprawling Angi profile (checked the actual batch CSVs: median ~8-11,
  // one plumber had 173). Rendering that in full, wrapped, in a 160px
  // column was blowing individual row heights out to several screens tall
  // and making the whole table nearly unscrollable (2026-09-14 feedback).
  // Collapse to a short preview + click-to-expand instead of a title=""
  // hover -- consistent with th, td's own comment above about not relying
  // on hover (no reveal on a real phone). Items stay "; "-joined even in
  // the preview/expanded text (not ", ") because several category names
  // already contain a literal comma of their own (e.g. "Faucets, Fixtures
  // and Pipes - Repair or Replace") -- switching to comma-joins would make
  // the boundary between items ambiguous.
  const SPECIALTIES_PREVIEW_COUNT = 2;
  function specialtiesCell(r) {
    const raw = r.specialties ?? "";
    if (!raw) return "";
    const items = raw.split("; ").filter(Boolean);
    // data-export-text carries the FULL list for cellText() (PDF/TXT
    // export) to read regardless of the on-screen expand/collapse state --
    // see its own comment below. CSV/XLSX exports don't go through this at
    // all (exportableRows() reads r.specialties directly), so they're
    // always complete either way.
    if (items.length <= SPECIALTIES_PREVIEW_COUNT) {
      return `<span data-export-text="${esc(raw)}">${esc(raw)}</span>`;
    }
    const preview = items.slice(0, SPECIALTIES_PREVIEW_COUNT).join("; ");
    const full = items.join("; ");
    const moreLabel = `+${items.length - SPECIALTIES_PREVIEW_COUNT} more`;
    return (
      `<span class="specialties-cell" data-export-text="${esc(full)}">` +
      `<span class="specialties-text">${esc(preview)}</span> ` +
      `<button type="button" class="specialties-toggle" aria-expanded="false" ` +
      `data-preview="${esc(preview)}" data-full="${esc(full)}" ` +
      `data-more="${esc(moreLabel)}" data-less="Show less">${esc(moreLabel)}</button>` +
      `</span>`
    );
  }

  // The single most representative negative/mixed review, picked by
  // bbb_scraper.sentiment.analyze._top_complaint -- a real, already-
  // analyzed sentence, not a synthesized blurb. Theme (a short category)
  // as a lightweight prefix, full sentence after; title="" carries the
  // whole thing (plus the negative/analyzed ratio -- "is this 1 bad review
  // or 4 out of 5?", real context a rep would want before calling, added
  // 2026-09-16) for a summary long enough to truncate on-screen.
  function topComplaintCell(r) {
    const summary = r.top_complaint_summary || "";
    if (!summary) return `<span class="muted">${dash}</span>`;
    const theme = r.top_complaint_theme || "";
    const neg = r.review_sentiment_negative_count;
    const analyzed = r.review_sentiment_analyzed_count;
    const ratio = (neg != null && analyzed) ? ` (${neg}/${analyzed} reviews negative)` : "";
    const full = (theme ? `${theme}: ${summary}` : summary) + ratio;
    return `<span class="top-complaint-cell" title="${esc(full)}">` +
      (theme ? `<span class="top-complaint-theme">${esc(theme)}</span> ` : "") +
      `${esc(summary)}</span>`;
  }

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
  //
  // 2026-09-14: Angi joined as a second 3rd-party source (phone-matched,
  // see bbb_scraper/angi/enrich.py). Folded into the *same* rating cell as
  // Yelp rather than adding a whole new column for it (yelpCell now shows
  // either/both) -- but "Specialties" (Angi's services-offered list) is
  // new information with no existing cell to share, so it's one genuinely
  // new column, 14 total then.
  //
  // 2026-09-16: the separate "Reputation" column is gone -- reputation_score
  // was retired in favor of one score (see bbb_scraper/match/merge.py's
  // _lead_priority_score docstring). Lead priority already answered the
  // question that mattered ("is this worth calling"); showing a second,
  // differently-scaled number next to it just asked the reader to reconcile
  // two opinions instead of acting on one, down to 13 columns -- then "Top
  // complaint" added the same day (bbb_scraper.sentiment's own pick of the
  // single most representative negative/mixed review, mostly MapQuest-
  // sourced real review text -- see _top_complaint's docstring) so a rep
  // has an actual thing to open the call with, not just a score, 14 again --
  // then "Latest review" (most_recent_review_date already drove the score
  // via _review_gap_flag / _review_sentiment_signal's recency multiplier,
  // but was never actually visible on the published record until now), 15.
  const COLUMNS = [
    { key: "name", label: "Business", cls: "name-cell", w: 200, render: nameCell },
    { key: "city", label: "City", w: 110, render: cityCell },
    { key: "rating", label: "BBB", w: 75, render: bbbCell },
    { key: "bbb_complaints_total", label: "BBB complaints", w: 105, cls: "num-cell", render: intCell("bbb_complaints_total") },
    { key: "yelp_rating", label: "Yelp / Angi / BBB", w: 140, cls: "num-cell", render: yelpCell },
    { key: "specialties", label: "Specialties", w: 160, render: specialtiesCell },
    { key: "top_complaint_summary", label: "Top complaint", w: 200, render: topComplaintCell },
    { key: "most_recent_negative_review_date", label: "Latest negative review", w: 130, render: latestReviewCell },
    { key: "lead_priority_score", label: "Lead priority (of 130)", w: 110, render: scoreCell("lead_priority_score", 130) },
    { key: "contact_readiness_score", label: "Reach", w: 135, render: reachCell },
    { key: "sources", label: "Sources", w: 105, render: sourcesCell },
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
    document.title = "LossLess — Reputation Sales Intelligence";

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
    filterSort.addEventListener("change", renderHomeCards);
    filterClearBtn.addEventListener("click", () => {
      filterIndustry.value = "";
      filterMetro.value = "";
      renderHomeCards();
    });
  }

  // "Most recent" reads publish_site_data.py's own published_at (2026-09-
  // 17, added for exactly this -- the manifest's array order was never a
  // usable proxy: it's re-sorted (metro, industry) on every single
  // publish, so position reflects alphabetical order, not recency). A
  // dataset published before that field existed just has no published_at
  // -- sorts to the back of "Most recent" (oldest-reads-as-unknown, not a
  // crash) rather than breaking the whole sort.
  function sortedDatasets(rows) {
    const sorted = [...rows];
    if (filterSort.value === "az") {
      sorted.sort((a, b) => a.industry.localeCompare(b.industry) || a.metro.localeCompare(b.metro));
    } else {
      sorted.sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
    }
    return sorted;
  }

  function filteredDatasets() {
    const industry = filterIndustry.value;
    const metro = filterMetro.value;
    const rows = manifest.datasets.filter((d) =>
      (!industry || d.industry === industry) && (!metro || d.metro === metro));
    return sortedDatasets(rows);
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
      `A ranked, ready-to-call prospect list for every market — BBB details, matched ` +
      `Yelp data, and a lead priority score, all in one sortable, filterable <strong>table</strong>.` +
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
        loadingEl.textContent = "Couldn't load this list right now — please try again shortly.";
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
        "This market doesn't have Yelp match data yet — scores here are based on BBB " +
        "signal alone (grade, reviews, complaints). Yelp data will be added in a future update.";
    }

    if (changed) {
      sortKey = "lead_priority_score";
      sortDir = -1;
      mobileSort.value = `${sortKey}:${sortDir}`;
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

  // yelpCell/flagsCell each combine several underlying fields into one
  // cell (Yelp+Angi rating; up to 5 flag badges) -- sorting straight off
  // a[sortKey] only ever covered ONE of those fields (yelp_rating, or
  // just reputation_divergence_flag), so an Angi-only match's real rating,
  // or any of the other 4 flags, silently didn't affect that column's
  // sort even though it's what's on screen (2026-09-17 audit: verified
  // against real published records, e.g. an Angi-only pest-control match
  // with a real 4.9-star rating always sorted to the bottom of "Yelp /
  // Angi"). These compute the value each column's header actually means
  // to sort by; every other column keeps using its raw field as before.
  const SORT_VALUE = {
    yelp_rating: (r) => num(r.yelp_rating) ?? num(r.angi_rating) ?? num(r.bbb_review_avg),
    reputation_divergence_flag: (r) => [
      r.reputation_divergence_flag, r.accredited_but_low_rated, r.low_review_volume_flag,
      r.review_gap_flag, r.website_dead_flag,
    ].reduce((n, f) => n + (isTrue(f) ? 1 : 0), 0),
    // Same reason as yelp_rating above -- latestReviewCell falls back to
    // most_recent_review_date (any sentiment) when there's no negative
    // one, so sorting by the raw negative-only field would bury a
    // business showing a real, recent date at the bottom of the sort.
    most_recent_negative_review_date: (r) => r.most_recent_negative_review_date || r.most_recent_review_date,
    sources: (r) => sourcesFor(r).length,
  };

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
      const sortValue = SORT_VALUE[sortKey] ?? ((r) => r[sortKey]);
      rows = [...rows].sort((a, b) => {
        const av = sortValue(a), bv = sortValue(b);
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

  function setSort(key, dir) {
    sortKey = key;
    sortDir = dir;
    markSortedHeader();
    mobileSort.value = `${key}:${dir}`;
    renderTable();
  }
  function onSort(key) {
    setSort(key, sortKey === key ? sortDir * -1 : 1);
  }
  function markSortedHeader() {
    headRow.querySelectorAll("th[data-key]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.key === sortKey && sortDir === 1);
      th.classList.toggle("sorted-desc", th.dataset.key === sortKey && sortDir === -1);
    });
  }
  // 375px of real phone screen (2026-09-17 audit, Nick's report: "frozen
  // pane of the company name is bad") had the sticky Business column alone
  // eating 200 of 375px -- 53% of the viewport -- before a visitor could
  // see even one more column, across 15 total. A narrower sticky column
  // isn't a real fix; a table is fundamentally a wide-screen shape. Below
  // the same 560px breakpoint style.css already treats as "mobile" (see
  // its own @media block), the table is replaced with one card per
  // business instead: name+city promoted to a real heading (matches the
  // Lists page's own lead-card language), lead priority + reach pulled out
  // as an at-a-glance stat strip (the two numbers a rep scans for first),
  // every other column as a plain label:value row -- reusing each
  // column's own render() function directly (built generically off
  // COLUMNS, not hand-duplicated per field) so a card's content can never
  // drift from what the desktop table shows for that same business.
  const CARD_HEAD_KEYS = new Set(["name", "city"]);
  const CARD_STAT_KEYS = new Set(["lead_priority_score", "contact_readiness_score"]);
  const MOBILE_BREAKPOINT = window.matchMedia("(max-width: 560px)");
  function renderMobileCards(rows) {
    const cols = columns();
    const statCols = cols.filter((c) => CARD_STAT_KEYS.has(c.key));
    const fieldCols = cols.filter((c) => !CARD_HEAD_KEYS.has(c.key) && !CARD_STAT_KEYS.has(c.key));
    return rows.map((r) => {
      const stats = statCols.map((c) =>
        `<div class="lrc-stat"><span class="lrc-stat-label">${esc(c.label)}</span>${c.render(r)}</div>`
      ).join("");
      const fields = fieldCols.map((c) =>
        `<div class="lrc-field"><dt>${esc(c.label)}</dt><dd>${c.render(r)}</dd></div>`
      ).join("");
      return `<div class="lrc">
        <div class="lrc-head">
          <div class="lrc-name">${nameCell(r)}</div>
          <div class="lrc-sub">${cityCell(r)}</div>
        </div>
        <div class="lrc-stats">${stats}</div>
        <dl class="lrc-fields">${fields}</dl>
      </div>`;
    }).join("");
  }
  function renderTable() {
    const rows = filteredRecords();
    emptyState.hidden = rows.length !== 0;
    // Skip building whichever markup isn't currently shown -- a search
    // keystroke re-renders on every character (see the "input" listener
    // below), and a dataset can run past 1,000 rows, so unconditionally
    // building BOTH a <tr> string and a full card string per keystroke
    // would double real rendering work for every desktop visitor, who
    // never sees the card markup at all.
    if (MOBILE_BREAKPOINT.matches) {
      mobileCards.innerHTML = renderMobileCards(rows);
    } else {
      const cols = columns();
      tableBody.innerHTML = rows.map((r) =>
        "<tr>" + cols.map((c) => `<td${c.cls ? ` class="${c.cls}"` : ""}>${c.render(r)}</td>`).join("") + "</tr>"
      ).join("");
    }
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
    // A cell whose on-screen text is a truncated preview (specialtiesCell)
    // marks its root with data-export-text carrying the full value -- read
    // that instead of textContent so PDF/TXT export always gets the
    // complete list regardless of whether it's currently expanded on
    // screen. Every other column has no such attribute, so this is a no-op
    // for them (falls through to the plain textContent read as before).
    const exportText = scratchEl.firstElementChild?.dataset?.exportText;
    if (exportText !== undefined) return exportText;
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

  // Excel's own cell format has a hard 32,767-character limit -- a real
  // crash, caught live 2026-09-18 right after bbb_reviews/mapquest_reviews/
  // angi_reviews/review_sentiment were added to the published record (see
  // scripts/publish_site_data.py): a business with many/long reviews
  // JSON-stringified into one cell routinely blows past that limit, and
  // XLSX.write throws -- not a graceful truncation, the WHOLE export dies,
  // for every row, not just the one business over the limit. CSV has no
  // such per-cell limit (it's just text), so exportCsv/exportableRows keep
  // shipping the full inline JSON blob there. For Excel specifically,
  // reviews move to their own sheet instead -- one row per individual
  // review, not one JSON blob per business -- which sidesteps the limit
  // entirely (no single review's own text gets anywhere close to 32,767
  // chars) and is a genuinely more useful shape in a spreadsheet besides:
  // a rep can actually read a review, not decode JSON in a cell.
  // Social links (BBB's own `socials` + Facebook's own `facebook_social_
  // links`, added 2026-09-18) get the same JSON-blob-in-a-cell treatment
  // as reviews used to -- Nick's ask: "make it in excel that each social
  // account has a column no more json". Unlike reviews these are short
  // (one URL each, not paragraphs of text), so they stay inline in the
  // Leads sheet as real columns instead of moving to their own sheet.
  // Both source lists get merged (same platform found in both -- BBB's
  // own capture wins, since Facebook's own page URL already came FROM
  // that same bbb_socials entry) into one fixed, predictable column set;
  // anything on a platform outside this list still ships, just folded
  // into one "Other social" column instead of silently dropped.
  const SOCIAL_PLATFORM_COLUMNS = [
    ["facebook", "Facebook"], ["instagram", "Instagram"], ["twitter", "Twitter/X"],
    ["youtube", "YouTube"], ["tiktok", "TikTok"], ["linkedin", "LinkedIn"], ["pinterest", "Pinterest"],
  ];
  const SOCIAL_JSON_FIELDS = ["socials", "facebook_social_links"];
  function combinedSocialLinks(record) {
    const byPlatform = {};
    const other = [];
    for (const field of SOCIAL_JSON_FIELDS) {
      for (const item of Array.isArray(record[field]) ? record[field] : []) {
        if (!item || !item.platform || !item.url) continue;
        const key = String(item.platform).toLowerCase();
        if (SOCIAL_PLATFORM_COLUMNS.some(([k]) => k === key)) {
          if (!byPlatform[key]) byPlatform[key] = item.url;
        } else if (!other.includes(item.url)) {
          other.push(item.url);
        }
      }
    }
    return { byPlatform, other: other.join(", ") };
  }

  const REVIEW_ARRAY_FIELDS = ["bbb_reviews", "mapquest_reviews", "angi_reviews", "review_sentiment"];
  const REVIEW_SOURCE_LABEL = { bbb_reviews: "BBB", mapquest_reviews: "Yelp", angi_reviews: "Angi" };
  function buildReviewRows(records) {
    const out = [];
    for (const r of records) {
      for (const field of ["bbb_reviews", "mapquest_reviews", "angi_reviews"]) {
        const reviews = Array.isArray(r[field]) ? r[field] : [];
        for (const rev of reviews) {
          out.push({
            business: r.name ?? "",
            city: r.city ?? "",
            source: REVIEW_SOURCE_LABEL[field],
            reviewer: rev.reviewer_name ?? "",
            rating: rev.rating ?? "",
            date: rev.date ?? rev.date_label ?? "",
            text: rev.text ?? "",
          });
        }
      }
    }
    return out;
  }
  function exportXlsx() {
    if (typeof XLSX === "undefined") {
      alert("Excel export couldn't load — it may be blocked by your browser. Try another format, or reload the page.");
      return;
    }
    const records = filteredRecords();
    if (!records.length) return;
    // Leads sheet: the normal full-record row, but each review-array field
    // becomes its own count instead of a JSON blob -- the real content is
    // on the Reviews sheet below, in a form Excel can actually hold.
    const leadRows = exportableRows().map((row, i) => {
      const out = { ...row };
      for (const field of REVIEW_ARRAY_FIELDS) {
        out[field] = Array.isArray(records[i][field]) ? records[i][field].length : 0;
      }
      for (const field of SOCIAL_JSON_FIELDS) delete out[field];
      const { byPlatform, other } = combinedSocialLinks(records[i]);
      for (const [key, label] of SOCIAL_PLATFORM_COLUMNS) out[label] = byPlatform[key] || "";
      out["Other social"] = other;
      return out;
    });
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(leadRows), "Leads");
    const reviewRows = buildReviewRows(records);
    if (reviewRows.length) {
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(reviewRows), "Reviews");
    }
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
      alert("PDF export couldn't load — it may be blocked by your browser. Try another format, or reload the page.");
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
    mobileSort.addEventListener("change", () => {
      const [key, dir] = mobileSort.value.split(":");
      setSort(key, Number(dir));
    });
    window.addEventListener("hashchange", route);
    // Re-render on crossing MOBILE_BREAKPOINT (rotating a tablet, resizing
    // a desktop window down) so the table/card swap actually happens live,
    // not just on next navigation -- MediaQueryList's own "change" event
    // only fires when .matches actually flips, so this never re-renders on
    // every resize pixel, just the one frame that matters.
    MOBILE_BREAKPOINT.addEventListener("change", () => { if (!datasetView.hidden) renderTable(); });
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
    // Delegated (not per-cell) because renderTable() replaces
    // tableBody.innerHTML wholesale on every sort/search -- any listener
    // bound directly to a .specialties-toggle button would be gone the
    // next re-render anyway, same reasoning as the export panel above.
    tableBody.addEventListener("click", (e) => {
      const btn = e.target.closest(".specialties-toggle");
      if (!btn) return;
      const textEl = btn.previousElementSibling;
      const expanded = btn.getAttribute("aria-expanded") === "true";
      textEl.textContent = expanded ? btn.dataset.preview : btn.dataset.full;
      btn.textContent = expanded ? btn.dataset.more : btn.dataset.less;
      btn.setAttribute("aria-expanded", String(!expanded));
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
      loadingEl.textContent = "Couldn't load available data right now — please try again shortly.";
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
