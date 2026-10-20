"use strict";

const $ = (id) => document.getElementById(id);
const form = $("scan-form");
const goBtn = $("go");
const state = { source: null, results: [], expected: 0, filter: "all" };

const CATEGORY_LABELS = { all: "All", code: "Code", social: "Social", games: "Games", media: "Media" };
const FACT_LABELS = {
  followers: "followers", repos: "repos", karma: "karma", posts: "posts", edits: "edits",
  games: "games", honor: "honor", streak: "day streak", packages: "packages", joined: "joined",
  location: "", company: "",
};

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.source) return stop("Stopped.");
  const u = $("username").value.trim();
  if (!u) return;
  start(u, form.depth.value);
});

$("export").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(found(), null, 2)], { type: "application/json" });
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob),
    download: `ghost-search-${$("username").value.trim()}.json`,
  });
  a.click();
  URL.revokeObjectURL(a.href);
});

// /?u=name&depth=1 prefills the form and starts right away.
{
  const params = new URLSearchParams(location.search);
  const u = params.get("u");
  if (u) {
    $("username").value = u;
    const d = form.querySelector(`input[name=depth][value="${CSS.escape(params.get("depth") || "0")}"]`);
    if (d) d.checked = true;
    queueMicrotask(() => form.requestSubmit());
  }
}

function start(username, depth) {
  history.replaceState(null, "", `?${new URLSearchParams({ u: username, depth })}`);
  state.results = [];
  state.expected = 0;
  state.filter = "all";
  $("found").replaceChildren();
  $("status").hidden = false;
  $("results").hidden = false;
  goBtn.textContent = "Stop";
  goBtn.dataset.busy = "";
  setStatus(`Checking ${username}…`);
  renderFilters();

  const qs = new URLSearchParams({ u: username, depth });
  const source = new EventSource(`/api/scan?${qs}`);
  state.source = source;

  source.addEventListener("queued", (e) => {
    const q = JSON.parse(e.data);
    state.expected += q.checks;
    if (q.depth > 0) setStatus(`Following ${q.usernames.join(", ")}…`);
    updateMeter();
  });
  source.addEventListener("result", (e) => {
    const r = JSON.parse(e.data);
    state.results.push(r);
    if (r.status === "found") addCard(r);
    updateMeter();
    renderFilters();
  });
  source.addEventListener("done", () => {
    const n = found().length;
    stop(n ? `Found ${n} profile${n === 1 ? "" : "s"}.` : "No profiles found.");
  });
  source.onerror = () => {
    if (state.source) stop("Lost connection to the server.");
  };
}

function stop(message) {
  state.source?.close();
  state.source = null;
  goBtn.textContent = "Scan";
  delete goBtn.dataset.busy;
  $("meter-fill").style.width = "100%";
  setStatus(message);
}

const found = () => state.results.filter((r) => r.status === "found");

function setStatus(text) { $("status-text").textContent = text; }

function updateMeter() {
  const pct = state.expected ? Math.min(100, (state.results.length / state.expected) * 100) : 0;
  $("meter-fill").style.width = `${pct}%`;
}

function renderFilters() {
  const counts = { all: found().length };
  for (const r of found()) counts[r.category] = (counts[r.category] || 0) + 1;
  const box = $("filters");
  box.replaceChildren(
    ...Object.keys(CATEGORY_LABELS)
      .filter((c) => c === "all" || counts[c])
      .map((c) => {
        const b = document.createElement("button");
        b.type = "button";
        b.role = "tab";
        b.textContent = `${CATEGORY_LABELS[c]} ${counts[c] || 0}`;
        b.setAttribute("aria-selected", String(state.filter === c));
        b.onclick = () => { state.filter = c; applyFilter(); renderFilters(); };
        return b;
      }),
  );
}

function applyFilter() {
  for (const li of $("found").children) {
    li.hidden = state.filter !== "all" && li.dataset.category !== state.filter;
  }
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch { return null; }
}

function addCard(r) {
  const li = $("card-tpl").content.firstElementChild.cloneNode(true);
  const d = r.details || {};
  li.dataset.category = r.category;
  li.hidden = state.filter !== "all" && state.filter !== r.category;

  const img = li.querySelector(".avatar");
  const avatar = d.avatar && safeUrl(d.avatar);
  if (avatar) img.src = avatar; else img.replaceWith(initialTile(r.site));

  li.querySelector(".site").textContent = r.site;
  const handle = li.querySelector(".handle");
  handle.textContent = r.username;
  handle.href = safeUrl(r.url) || "#";
  li.querySelector(".cat").textContent = r.category;
  li.querySelector(".name").textContent = d.name || "";
  li.querySelector(".bio").textContent = d.bio || "";

  const facts = li.querySelector(".facts");
  const add = (key, value) => {
    if (value === undefined || value === null || value === "") return;
    const row = document.createElement("div");
    const dd = document.createElement("dd");
    dd.textContent = typeof value === "number" ? value.toLocaleString() : value;
    row.append(dd);
    if (FACT_LABELS[key]) {
      const dt = document.createElement("dt");
      dt.textContent = FACT_LABELS[key];
      row.append(dt);
    }
    facts.append(row);
  };
  add("location", d.location);
  add("followers", d.followers);
  for (const [k, v] of Object.entries(d.extra || {})) if (k in FACT_LABELS) add(k, v);

  const links = li.querySelector(".links");
  for (const link of (d.links || []).slice(0, 5)) {
    const href = safeUrl(link);
    if (!href) continue;
    const a = Object.assign(document.createElement("a"), {
      href, textContent: href.replace(/^https?:\/\/(www\.)?/, ""), target: "_blank",
      rel: "noopener noreferrer",
    });
    const item = document.createElement("li");
    item.append(a);
    links.append(item);
  }
  if (r.via) li.querySelector(".via").textContent = `↳ linked from ${r.via.replace(":", " · ")}`;
  li.tabIndex = 0;
  li.setAttribute("aria-label", `${r.site} profile ${r.username}, show details`);
  li.addEventListener("click", (e) => { if (!e.target.closest("a")) openProfile(r); });
  li.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target === li) { e.preventDefault(); openProfile(r); }
  });
  $("found").append(li);
}

function openProfile(r) {
  const d = r.details || {};
  const url = safeUrl(r.url);

  const old = $("pf-avatar");
  const avatar = d.avatar && safeUrl(d.avatar);
  const fresh = avatar ? document.createElement("img") : document.createElement("div");
  fresh.id = "pf-avatar";
  fresh.className = "pf-avatar";
  if (avatar) Object.assign(fresh, { src: avatar, alt: "", referrerPolicy: "no-referrer" });
  else fresh.textContent = r.site[0];
  old.replaceWith(fresh);

  $("pf-site").textContent = `${r.site} · ${r.category}`;
  $("pf-name").textContent = d.name || "";
  $("pf-handle").textContent = r.username;
  $("pf-bio").textContent = d.bio || "";

  const facts = $("pf-facts");
  facts.replaceChildren();
  const row = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = label;
    dd.textContent = typeof value === "number" ? value.toLocaleString() : String(value);
    facts.append(dt, dd);
  };
  row("Location", d.location);
  row("Followers", d.followers);
  for (const [k, v] of Object.entries(d.extra || {})) {
    row(FACT_LABELS[k] ? FACT_LABELS[k][0].toUpperCase() + FACT_LABELS[k].slice(1) : k, v);
  }

  const links = $("pf-links");
  links.replaceChildren();
  for (const link of d.links || []) {
    const href = safeUrl(link);
    if (!href) continue;
    const a = Object.assign(document.createElement("a"), {
      href, textContent: href.replace(/^https?:\/\/(www\.)?/, ""), target: "_blank",
      rel: "noopener noreferrer",
    });
    const item = document.createElement("li");
    item.append(a);
    links.append(item);
  }
  $("pf-links-title").hidden = !links.children.length;
  $("pf-via").textContent = r.via ? `↳ found by following ${r.via.replace(":", " · ")}` : "";

  const open = $("pf-open");
  open.href = url || "#";
  const copy = $("pf-copy");
  copy.textContent = "Copy link";
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(url || ""); copy.textContent = "Copied"; }
    catch { copy.textContent = "Couldn't copy"; }
  };
  $("profile").showModal();
}

$("profile").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close(); // click on backdrop
});

function initialTile(site) {
  const tile = document.createElement("div");
  tile.className = "avatar";
  tile.style.cssText = "display:grid;place-items:center;font-weight:700;color:var(--muted)";
  tile.textContent = site[0];
  return tile;
}

