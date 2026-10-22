"use strict";
const $ = (id) => document.getElementById(id);
const selected = new Set();
let mode = null, timezone = undefined, statusSoon = null;

async function api(method, path, body, signal) {
  // the daemon takes JSON POSTs only (it keeps other web pages out that way)
  if (method === "POST") body = body || {};
  const r = await fetch(path, {method, headers: body ? {"Content-Type": "application/json"} : {},
                               body: body ? JSON.stringify(body) : undefined, signal});
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error(data?.error || r.statusText);
  return data;
}

function say(text) { $("msg").textContent = text || ""; }

function button(label, onclick, cls) {
  const b = document.createElement("button");
  b.textContent = label;
  if (cls) b.className = cls;
  b.onclick = onclick;
  return b;
}

function refreshSelection() {
  for (const b of $("rooms").children) b.classList.toggle("on", selected.has(b.dataset.id));
  for (const b of $("modes").children) b.classList.toggle("on", b.dataset.mode === mode);
  $("start").disabled = selected.size === 0;  // while busy, it queues
}

async function loadRooms() {
  const d = await api("GET", "/rooms");
  timezone = d.timezone || undefined;
  mode = d.default_mode;
  $("rooms").replaceChildren(...d.rooms.filter((r) => !r.forbidden).map((r) => {
    const carry = r.carry_in || r.carry_out;
    const b = button(r.label + (carry ? " ✋" : ""), () => {
      selected.has(b.dataset.id) ? selected.delete(b.dataset.id) : selected.add(b.dataset.id);
      refreshSelection();
    });
    b.dataset.id = String(r.id);
    if (carry) b.title = "someone has to carry the robot " + (r.carry_in ? "in" : "out");
    return b;
  }));
  $("presets").replaceChildren(...d.presets.map((p) => button(p.label, () => start(p.rooms.map(String), p.mode))));
  $("modes").replaceChildren(...Object.entries(d.modes).map(([m, label]) => {
    const b = button(label, () => { mode = m; refreshSelection(); });
    b.dataset.mode = m;
    return b;
  }));
  refreshSelection();
}

async function start(rooms, presetMode) {
  say("");
  try {
    await api("POST", "/jobs", {rooms, mode: presetMode || mode});
    selected.clear();
  } catch (e) { say(e.message); }
  loadStatus();
}

async function loadStatus() {
  try {
    const s = await api("GET", "/status");
    $("state").textContent = s.status_text + (s.errors.length ? " — " + s.errors.map((e) => e.text).join(", ") : "");
    const bits = [];
    if (s.battery != null) bits.push(`battery ${s.battery} %`);
    if (s.clean_water_empty) bits.push("clean water tank empty");
    if (s.relocating) bits.push("relocating");
    // the clock goes at the bottom; only being inside the quiet hours is worth a line up here
    const c = s.clock, clock = [];
    if (c?.skew) clock.push(`robot clock ${Math.abs(c.skew)} min ${c.skew > 0 ? "ahead" : "behind"}`);
    if (c?.quiet_hours) {
      const dur = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ` : "") + `${m % 60} min`;
      clock.push(`quiet hours ${c.quiet_hours_local}` + (c.quiet_now ? ` — now, for ${dur(c.quiet_ends_in)} more`
                                                                      : ` — in ${dur(c.quiet_starts_in)}`));
      if (c.quiet_now) bits.push("quiet hours (likely no bin emptying)");
    }
    $("details").textContent = bits.join(" · ");
    $("clock").textContent = clock.join(" · ");
    const j = s.job;
    showQuestion(j);
    $("job").textContent = (j ? `Job: ${j.description} — ${j.state}` + (j.steps > 1 ? ` (step ${j.step}/${j.steps})` : "")
                                + (j.message ? `: ${j.message}` : "") : "")
                           + ((s.queue || []).length ? ` · then: ${s.queue.map((q) => q.description).join(" | ")}` : "");
  } catch (e) {
    $("state").textContent = "no contact";
    $("details").textContent = e.message;
    $("clock").textContent = "";
  }
  refreshSelection();
}

function showQuestion(j) {
  const q = j && j.question;
  $("question").hidden = !q;
  if (!q) return;
  $("qtext").textContent = q.text;
  $("qchoices").replaceChildren(...q.choices.map((c) => button(c, async () => {
    try { await api("POST", `/jobs/${j.id}/answer`, {choice: c}); } catch (e) { say(e.message); }
    loadStatus();
  }, c === "cancel" ? "" : "primary")));
}

function fmt(t) {
  return new Date(t).toLocaleString("sv-SE", {timeZone: timezone, hour12: false});
}

function addLog(rec) {
  const li = document.createElement("li");
  li.className = rec.level + (rec.code === "progress" ? " progress" : "");
  const time = document.createElement("time");
  time.textContent = fmt(rec.t);
  li.append(time, rec.msg);
  if (rec.by) {
    const by = document.createElement("small");
    by.className = "by";
    by.textContent = ` — ${rec.by}`;
    li.append(by);
  }
  $("log").prepend(li);
  while ($("log").children.length > 100) $("log").lastChild.remove();
}

function follow() {
  const es = new EventSource("/events?backlog=30");
  es.onmessage = (m) => {
    addLog(JSON.parse(m.data));
    clearTimeout(statusSoon);  // the backlog comes in a burst; ask the robot once
    statusSoon = setTimeout(loadStatus, 1000);
  };
  es.onerror = () => { es.close(); setTimeout(follow, 5000); };
}

const KINDS = ["vac", "mop", "move"];

function showMap() {
  const show = KINDS.filter((k) => $("show-" + k).checked);
  const q = new URLSearchParams({max_age: $("maxage").value || 0, show: show.join(","), t: Date.now()});
  $("map").src = "/map.png?" + q;
}

// map pixels -> map mm (GET /map.json); the inverse of mapimg.render's p()
let geo = null;
async function loadGeo() { try { geo = await api("GET", "/map.json"); } catch (e) { geo = null; } }

$("map").onclick = async (e) => {
  const img = $("map");
  if (!geo) await loadGeo();  // it failed at load time
  if (!geo || !img.naturalWidth) { say("the map's geometry is not available"); return; }
  const px = e.offsetX * img.naturalWidth / img.clientWidth, py = e.offsetY * img.naturalHeight / img.clientHeight;
  const x = Math.round(geo.origin[0] + px / geo.scale * geo.resolution);
  const y = Math.round(geo.origin[1] + (geo.height - 1 - py / geo.scale) * geo.resolution);
  if (!confirm(`Send the robot to this spot (${x}, ${y})?`)) return;
  try { await api("POST", "/goto", {x, y}); say(""); } catch (err) { say(err.message); }
};

// the map options are remembered per browser; storage may be unavailable
function mapOpts(save) {
  try {
    if (save) {
      localStorage.setItem("mapopts", JSON.stringify(
        {maxage: $("maxage").value, show: KINDS.filter((k) => $("show-" + k).checked)}));
      return;
    }
    const o = JSON.parse(localStorage.getItem("mapopts"));
    if (!o) return;
    $("maxage").value = o.maxage;
    for (const k of KINDS) $("show-" + k).checked = o.show.includes(k);
  } catch (e) { /* defaults */ }
}

for (const id of ["maxage", ...KINDS.map((k) => "show-" + k)]) {
  $(id).onchange = () => { mapOpts(true); showMap(); };
}

$("start").onclick = () => start([...selected]);
// a command button shows it was pressed, and takes no more presses, until the daemon replies
function command(id, path, busy) {
  const b = $(id), label = b.textContent;
  b.onclick = async () => {
    b.disabled = true;
    b.textContent = busy;
    say("");
    // the robot's own timeout is 30 s; past that a stalled reply must not leave Stop dead
    try { await api("POST", path, null, AbortSignal.timeout(35000)); } catch (e) { say(e.message); } finally {
      b.textContent = label;
      b.disabled = false;
    }
    loadStatus();
  };
}
command("stop", "/stop", "Stopping…");
command("home", "/home", "Sending home…");
$("reload").onclick = async () => {
  $("reload").disabled = true;
  try { await api("POST", "/map/refresh"); await loadGeo(); showMap(); } catch (e) { say(e.message); }
  $("reload").disabled = false;
};

loadRooms().catch((e) => say(e.message));
loadStatus();
follow();
mapOpts();
loadGeo();
showMap();
setInterval(loadStatus, 30000);
