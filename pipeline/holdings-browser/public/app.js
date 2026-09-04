const state = {
  catalog: null,
  amc: null,
  parent: null,
  scheme: null,
};

const $ = (id) => document.getElementById(id);

function fmt(n) {
  if (n == null || n === "") return "—";
  const x = Number(String(n).replace(/,/g, ""));
  if (!Number.isFinite(x)) return "—";
  return x.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function boot() {
  const res = await fetch("/catalog.json");
  state.catalog = await res.json();
  $("scheme-q").addEventListener("input", renderSchemes);
  $("amc-q").addEventListener("input", onAmcInput);
  $("parent-q").addEventListener("input", onParentInput);
  renderSchemes();
  $("scheme-q").focus();
}

function onAmcInput() {
  const q = $("amc-q").value.trim().toLowerCase();
  state.amc = (state.catalog.amcs || []).find(
    (a) => (a.name || "").toLowerCase() === q || (a.id || "") === q,
  ) || null;
  if (!state.amc && q) {
    const hits = (state.catalog.amcs || []).filter(
      (a) => (a.name || "").toLowerCase().includes(q) || (a.id || "").includes(q),
    );
    if (hits.length === 1) state.amc = hits[0];
  }
  state.parent = null;
  if (!state.amc) $("parent-q").value = "";
  renderSchemes();
}

function onParentInput() {
  const q = $("parent-q").value.trim().toLowerCase();
  const rows = parentOptions();
  state.parent = rows.find((p) => p.toLowerCase() === q) || null;
  if (!state.parent && q) {
    const hits = rows.filter((p) => p.toLowerCase().includes(q));
    if (hits.length === 1) state.parent = hits[0];
  }
  renderSchemes();
}

function parentOptions() {
  const seen = new Set();
  const out = [];
  for (const s of state.catalog.schemes || []) {
    if (state.amc && s.amc_id !== state.amc.id && (s.amc_name || "") !== state.amc.name) continue;
    const name = s.parent_name || "";
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  return out.sort((a, b) => a.localeCompare(b));
}

function matchingSchemes() {
  const q = $("scheme-q").value.trim().toLowerCase();
  const amcQ = $("amc-q").value.trim().toLowerCase();
  const parentQ = $("parent-q").value.trim().toLowerCase();
  return (state.catalog.schemes || []).filter((s) => {
    if (state.amc && s.amc_id !== state.amc.id && (s.amc_name || "") !== state.amc.name) {
      if (!amcQ || !((s.amc_name || "").toLowerCase().includes(amcQ) || (s.amc_id || "").includes(amcQ))) {
        return false;
      }
    } else if (!state.amc && amcQ) {
      if (!((s.amc_name || "").toLowerCase().includes(amcQ) || (s.amc_id || "").includes(amcQ))) return false;
    }
    if (state.parent && (s.parent_name || "") !== state.parent) {
      if (!parentQ || !(s.parent_name || "").toLowerCase().includes(parentQ)) return false;
    } else if (!state.parent && parentQ) {
      if (!(s.parent_name || "").toLowerCase().includes(parentQ)) return false;
    }
    if (!q) return true;
    return (
      (s.amfi_code || "").includes(q) ||
      (s.name || "").toLowerCase().includes(q) ||
      (s.parent_name || "").toLowerCase().includes(q) ||
      (s.parent_amfi || "").includes(q)
    );
  });
}

function renderSchemes() {
  const rows = matchingSchemes();
  rows.sort(
    (a, b) =>
      Number(b.has_holdings) - Number(a.has_holdings) ||
      (a.name || "").localeCompare(b.name || ""),
  );
  $("scheme-meta").textContent = `${rows.length} schemes`;
  const ul = $("scheme-list");
  ul.innerHTML = "";
  if (!rows.length) {
    ul.innerHTML = `<li class="empty">No schemes match.</li>`;
    return;
  }
  for (const s of rows.slice(0, 60)) {
    const li = document.createElement("li");
    if (state.scheme && state.scheme.amfi_code === s.amfi_code && state.scheme.name === s.name) {
      li.classList.add("active");
    }
    const tag = s.has_holdings
      ? `${s.amfi_code || "—"} · holdings ${s.holdings?.as_of || ""}`
      : `${s.amfi_code || "—"} · no disclosure`;
    li.innerHTML = `<span class="name">${esc(s.name)}</span><span class="sub">${esc(tag)}</span>`;
    li.addEventListener("click", () => selectScheme(s));
    ul.appendChild(li);
  }
}

function siblingsOf(scheme) {
  const parent = scheme.parent_amfi || scheme.parent_name;
  return (state.catalog.schemes || []).filter((s) => {
    if (s.amc_id && scheme.amc_id && s.amc_id !== scheme.amc_id) return false;
    if (scheme.parent_amfi) return s.parent_amfi === scheme.parent_amfi;
    return s.parent_name === parent;
  });
}

async function selectScheme(scheme) {
  state.scheme = scheme;
  if (scheme.amc_id || scheme.amc_name) {
    state.amc =
      (state.catalog.amcs || []).find((a) => a.id === scheme.amc_id || a.name === scheme.amc_name) || state.amc;
    if (scheme.amc_name && $("amc-q").value.trim() === "") $("amc-q").value = scheme.amc_name;
  }
  if (scheme.parent_name && $("parent-q").value.trim() === "") $("parent-q").value = scheme.parent_name;
  state.parent = scheme.parent_name || state.parent;
  renderSchemes();
  $("detail-panel").classList.remove("hidden");
  $("detail-title").textContent = scheme.name;
  const facts = [
    ["AMC", scheme.amc_name],
    ["Parent", scheme.parent_name],
    ["Parent AMFI", scheme.parent_amfi || "—"],
    ["Scheme AMFI", scheme.amfi_code || "—"],
    ["NAV", scheme.nav ? `${scheme.nav} (${scheme.nav_date || ""})` : "—"],
    ["ISIN", scheme.isin || "—"],
    ["Category", scheme.category || "—"],
    ["Holdings as of", scheme.holdings?.as_of || "—"],
    ["Disclosure", scheme.holdings?.shortcode || "—"],
    ["Source file", scheme.holdings?.source_file || "—"],
  ];
  $("detail-facts").innerHTML = facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || "—")}</dd>`).join("");

  const sibs = siblingsOf(scheme);
  $("siblings-label").textContent = sibs.length
    ? "Plans of this parent — same holdings book"
    : "";
  const sul = $("sibling-list");
  sul.innerHTML = "";
  for (const s of sibs) {
    const li = document.createElement("li");
    if (s.amfi_code === scheme.amfi_code) li.classList.add("active");
    li.innerHTML = `<span class="name">${esc(s.name)}</span><span class="sub">${esc(s.amfi_code || "")}</span>`;
    li.addEventListener("click", () => selectScheme(s));
    sul.appendChild(li);
  }

  const tb = $("holdings-table").querySelector("tbody");
  tb.innerHTML = "";
  if (!scheme.has_holdings || !scheme.holdings?.b2_key) {
    $("holdings-note").textContent = "No latest disclosure mapped to this scheme.";
    $("detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  $("holdings-note").textContent = "Loading parent-fund holdings…";
  const url = `/api/amfi/${encodeURIComponent(scheme.amfi_code)}`;
  const res = await fetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    $("holdings-note").textContent = data.error || "No Data Found";
    return;
  }
  const rows = data.holdings || [];
  const unit = data.meta?.market_value_unit === "INR_LAKH" ? "₹ lakh" : (data.meta?.market_value_unit || "");
  const prev = data.links?.previous;
  const next = data.links?.next;
  const nav = [
    prev?.href ? `prev ${prev.as_of}${prev.message ? ` (${prev.message})` : ""}` : "",
    next?.href ? `next ${next.as_of}${next.message ? ` (${next.message})` : ""}` : "",
  ].filter(Boolean).join(" · ");
  $("holdings-note").textContent =
    `${rows.length} holdings · as of ${data.meta?.as_of || scheme.holdings?.as_of} · MV in ${unit || "scheme unit"} · parent disclosure, shown on every plan${nav ? ` · ${nav}` : ""}`;
  for (const h of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(h.holding_type || "")}</td><td>${esc(h.instrument)}</td><td>${esc(h.isin)}</td><td>${esc(h.industry || h.rating || "")}</td><td class="num">${esc(fmt(h.quantity))}</td><td class="num">${esc(fmt(h.market_value))}</td><td class="num">${esc(fmt(h.pct_nav))}</td>`;
    tb.appendChild(tr);
  }
  $("detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

boot();
