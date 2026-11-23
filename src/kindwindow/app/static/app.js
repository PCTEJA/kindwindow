/* KindWindow demo UI — thin client over the existing JSON API.
   No data is invented here: resident/contact views only show what the
   API returns; the Research view shows verified aggregates only. */
"use strict";

const DAY_NAMES = { 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun" };
const SYNTHETIC_PROBABILITY = 0.42;

/* Per-home Brier, calibrated forest vs recency baseline, test split.
   Source: reports/tables/test_scores.csv (MACRO rows excluded here). */
const PER_HOME = [
  { home: "aruba", n: 2062, forest: 0.1301, recency: 0.196 },
  { home: "hh101", n: 3521, forest: 0.153, recency: 0.1865 },
  { home: "hh102", n: 9902, forest: 0.1288, recency: 0.1807 },
  { home: "hh103", n: 20651, forest: 0.1801, recency: 0.2118 },
  { home: "hh104", n: 7751, forest: 0.0997, recency: 0.1192 },
  { home: "hh105", n: 20180, forest: 0.1878, recency: 0.2337 },
  { home: "hh106", n: 12433, forest: 0.1632, recency: 0.2206 },
  { home: "hh108", n: 20659, forest: 0.1596, recency: 0.2013 },
  { home: "hh109", n: 15938, forest: 0.1915, recency: 0.2133 },
  { home: "hh111", n: 20660, forest: 0.1804, recency: 0.2161 },
  { home: "hh112", n: 1279, forest: 0.1454, recency: 0.1971 },
  { home: "hh113", n: 4840, forest: 0.1563, recency: 0.2189 },
  { home: "hh114", n: 20612, forest: 0.2057, recency: 0.2387 },
  { home: "hh115", n: 3268, forest: 0.0973, recency: 0.2351 },
  { home: "hh116", n: 1451, forest: 0.0573, recency: 0.0992 },
  { home: "hh117", n: 3268, forest: 0.0775, recency: 0.1519 },
  { home: "hh118", n: 11198, forest: 0.0896, recency: 0.123 },
  { home: "hh119", n: 18472, forest: 0.0932, recency: 0.0928 },
  { home: "hh120", n: 6819, forest: 0.1346, recency: 0.145 },
  { home: "hh122", n: 1989, forest: 0.1882, recency: 0.2104 },
];

const $ = (id) => document.getElementById(id);
const alertBox = $("alert");

function showAlert(message) {
  alertBox.textContent = message;
  alertBox.hidden = false;
  alertBox.scrollIntoView({ block: "nearest" });
}
function clearAlert() { alertBox.hidden = true; alertBox.textContent = ""; }

function fmtTime(epochSeconds) {
  if (epochSeconds === null || epochSeconds === undefined) return "—";
  const d = new Date(epochSeconds * 1000);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pad = (v) => String(v).padStart(2, "0");
  return `${days[d.getUTCDay()]} ${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}, ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}
function fmtPrefs(p) {
  if (!p || !p.preferred_days || !p.preferred_start) return "No visiting hours set yet.";
  const days = p.preferred_days.map((d) => DAY_NAMES[d]).join(", ");
  let s = `${days}, ${p.preferred_start}–${p.preferred_end}`;
  if (p.quiet_start) s += ` (quiet ${p.quiet_start}–${p.quiet_end})`;
  return s;
}
function describeParsed(parsed) {
  const parts = [];
  if (parsed.days) parts.push("days: " + parsed.days.map((d) => DAY_NAMES[d]).join(", "));
  if (parsed.preferred_range) parts.push(`preferred ${parsed.preferred_range[0]}–${parsed.preferred_range[1]}`);
  if (parsed.quiet_range) parts.push(`quiet ${parsed.quiet_range[0]}–${parsed.quiet_range[1]}`);
  return parts.length ? parts.join("; ") : "nothing was understood";
}
/* datetime-local value -> epoch seconds, treating the typed time as UTC
   (the demo treats local time as UTC). */
function slotToEpoch(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
  if (!m) return null;
  return Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return data;
}

/* ---------------- view switching ---------------- */
document.querySelectorAll(".view-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".view-tab").forEach((t) => {
      t.classList.remove("is-active");
      t.removeAttribute("aria-current");
    });
    tab.classList.add("is-active");
    tab.setAttribute("aria-current", "page");
    document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
    $("view-" + tab.dataset.view).hidden = false;
    clearAlert();
  });
});

/* ---------------- resident ---------------- */
let residentSignedIn = false;

async function loadResident() {
  try {
    const data = await api("/preferences");
    residentSignedIn = true;
    $("resident-login").hidden = true;
    $("resident-app").hidden = false;
    $("resident-login-hint").textContent = "";
    $("resident-status").textContent = data.paused
      ? "Signed in as resident. Visits are paused."
      : "Signed in as resident.";
    $("btn-pause").hidden = !!data.paused;
    $("btn-resume").hidden = !data.paused;
    $("prefs-current").innerHTML =
      "<strong>Current hours:</strong> " + escapeHtml(fmtPrefs(data.preferences));
    if (data.preferences) fillPrefsForm(data.preferences, data.contacts || []);
    else fillPrefsForm(null, data.contacts || []);
    if (data.pending_preferences) showPending(data.pending_preferences);
    else $("pending-box").hidden = true;
    await loadInvitations();
  } catch (err) {
    residentSignedIn = false;
    $("resident-login").hidden = false;
    $("resident-app").hidden = true;
    showAlert("Could not load your preferences: " + err.message);
  }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function fillPrefsForm(prefs, contacts) {
  document.querySelectorAll("#day-toggles input").forEach((cb) => {
    cb.checked = !!prefs && prefs.preferred_days.includes(Number(cb.value));
  });
  $("pref-start").value = prefs ? prefs.preferred_start || "" : "";
  $("pref-end").value = prefs ? prefs.preferred_end || "" : "";
  $("quiet-start").value = prefs ? prefs.quiet_start || "" : "";
  $("quiet-end").value = prefs ? prefs.quiet_end || "" : "";
  const ids = new Set((contacts || []).map((c) => c.contact_id));
  $("grant-a").checked = ids.has(1);
  $("grant-b").checked = ids.has(2);
}

$("btn-login-resident").addEventListener("click", async () => {
  clearAlert();
  try {
    await api("/demo/login", { method: "POST", body: JSON.stringify({ identity: "resident" }) });
    await loadResident();
  } catch (err) {
    $("resident-login-hint").textContent = "Log in failed: " + err.message;
  }
});
$("btn-logout-resident").addEventListener("click", async () => {
  try { await api("/demo/logout", { method: "POST" }); } catch (_) { /* already logged out */ }
  residentSignedIn = false;
  $("resident-app").hidden = true;
  $("resident-login").hidden = false;
});

$("prefs-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  clearAlert();
  const days = [...document.querySelectorAll("#day-toggles input:checked")].map((c) => Number(c.value));
  const msg = $("prefs-msg");
  msg.className = "form-msg";
  if (!days.length) { msg.textContent = "Choose at least one preferred day."; msg.classList.add("err"); return; }
  if (!$("pref-start").value || !$("pref-end").value) {
    msg.textContent = "Fill in both preferred start and end times."; msg.classList.add("err"); return;
  }
  const qs = $("quiet-start").value, qe = $("quiet-end").value;
  if ((qs && !qe) || (!qs && qe)) {
    msg.textContent = "Quiet hours need both a start and an end time, or neither.";
    msg.classList.add("err"); return;
  }
  const contactIds = [];
  if ($("grant-a").checked) contactIds.push(1);
  if ($("grant-b").checked) contactIds.push(2);
  const payload = {
    preferred_days: days,
    preferred_range: { start: $("pref-start").value, end: $("pref-end").value },
    quiet_range: qs ? { start: qs, end: qe } : null,
    contact_ids: contactIds,
  };
  try {
    await api("/preferences", { method: "POST", body: JSON.stringify(payload) });
    msg.textContent = "Visiting hours saved.";
    msg.classList.add("ok");
    await loadResident();
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("err");
  }
});
$("btn-clear-quiet").addEventListener("click", () => {
  $("quiet-start").value = "";
  $("quiet-end").value = "";
});

function showPending(pending) {
  $("pending-summary").textContent = "Understood as: " + fmtPrefs(pending) + ".";
  $("pending-box").hidden = false;
}
$("btn-parse-note").addEventListener("click", async () => {
  clearAlert();
  const text = $("note-text").value.trim();
  const result = $("note-result");
  if (!text) { result.textContent = "Write a note first."; return; }
  try {
    const data = await api("/notes/parse", { method: "POST", body: JSON.stringify({ text }) });
    result.textContent = "Understood: " + describeParsed(data.parsed) + ". " +
      (data.detail || "") + " Nothing has changed yet — confirm below to apply it.";
    if (data.pending_preferences) showPending(data.pending_preferences);
    else $("pending-box").hidden = true;
  } catch (err) {
    result.textContent = "Could not understand the note: " + err.message;
  }
});
$("btn-confirm-pending").addEventListener("click", async () => {
  try {
    await api("/preferences/confirm", { method: "POST", body: JSON.stringify({ confirm: true }) });
    $("pending-box").hidden = true;
    $("note-result").textContent = "Confirmed — your visiting hours are updated.";
    await loadResident();
  } catch (err) { showAlert(err.message); }
});
$("btn-cancel-pending").addEventListener("click", async () => {
  try {
    await api("/preferences/cancel", { method: "POST" });
    $("pending-box").hidden = true;
    $("note-result").textContent = "Cancelled — your current hours are unchanged.";
    await loadResident();
  } catch (err) { showAlert(err.message); }
});

$("invite-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  clearAlert();
  const msg = $("invite-msg");
  msg.className = "form-msg";
  const epoch = slotToEpoch($("invite-slot").value);
  if (epoch === null) { msg.textContent = "Choose a date and time for the visit."; msg.classList.add("err"); return; }
  const key = "web-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  try {
    await api("/invitations", {
      method: "POST",
      body: JSON.stringify({ contact_id: Number($("invite-contact").value), slot_start_utc: epoch, request_key: key }),
    });
    msg.textContent = "Visit proposed. It is pending until you approve it.";
    msg.classList.add("ok");
    await loadInvitations();
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("err");
  }
});

async function loadInvitations() {
  const list = $("invitations-list");
  try {
    const data = await api("/invitations");
    const invs = data.invitations || [];
    $("invitations-empty").hidden = invs.length > 0;
    list.innerHTML = "";
    invs.forEach((inv) => {
      const li = document.createElement("li");
      const contact = inv.contact_id === 1 ? "Contact A" : "Contact B";
      const meta = document.createElement("div");
      meta.className = "inv-meta";
      meta.innerHTML = `<strong>${escapeHtml(contact)}</strong> — ${escapeHtml(fmtTime(inv.slot_start_utc))} ` +
        `<span class="state ${escapeHtml(inv.state)}">${escapeHtml(inv.state)}</span><br>` +
        `<span class="fine">Invitation ${inv.invitation_id}` +
        (inv.expires_utc ? ` · expires ${escapeHtml(fmtTime(inv.expires_utc))}` : "") +
        ` · synthetic probability ${SYNTHETIC_PROBABILITY} (resident only)</span>`;
      li.appendChild(meta);
      const actions = document.createElement("div");
      actions.className = "inv-actions";
      const addBtn = (label, fn) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "btn"; b.textContent = label;
        b.addEventListener("click", fn);
        actions.appendChild(b);
      };
      if (inv.state === "pending" || inv.state === "postponed") {
        addBtn("Approve", () => inviteAction(inv.invitation_id, "approve"));
        addBtn("Reject", () => inviteAction(inv.invitation_id, "reject"));
      }
      if (["pending", "approved", "postponed"].includes(inv.state)) {
        const when = document.createElement("input");
        when.type = "datetime-local";
        when.setAttribute("aria-label", "New time for invitation " + inv.invitation_id);
        actions.appendChild(when);
        addBtn("Postpone", async () => {
          const epoch = slotToEpoch(when.value);
          if (epoch === null) { showAlert("Choose a new date and time to postpone to."); return; }
          await inviteAction(inv.invitation_id, "postpone", { new_slot_start_utc: epoch });
        });
        addBtn("Revoke", () => inviteAction(inv.invitation_id, "revoke"));
      }
      li.appendChild(actions);
      list.appendChild(li);
    });
  } catch (err) { showAlert("Could not load invitations: " + err.message); }
}
async function inviteAction(id, action, body) {
  clearAlert();
  try {
    await api(`/invitations/${id}/${action}`, {
      method: "POST", body: body ? JSON.stringify(body) : undefined,
    });
    await loadInvitations();
  } catch (err) { showAlert(err.message); }
}

$("btn-pause").addEventListener("click", async () => {
  try {
    const data = await api("/pause", { method: "POST" });
    $("pause-msg").textContent = data.detail +
      (data.invalidated_approved ? ` (${data.invalidated_approved} approved invitation(s) invalidated.)` : "");
    $("pause-msg").className = "form-msg ok";
    await loadResident();
  } catch (err) { showAlert(err.message); }
});
$("btn-resume").addEventListener("click", async () => {
  try {
    const data = await api("/resume", { method: "POST" });
    $("pause-msg").textContent = data.detail;
    $("pause-msg").className = "form-msg ok";
    await loadResident();
  } catch (err) { showAlert(err.message); }
});
$("btn-delete").addEventListener("click", () => { $("delete-confirm").hidden = false; });
$("btn-delete-no").addEventListener("click", () => { $("delete-confirm").hidden = true; });
$("btn-delete-yes").addEventListener("click", async () => {
  try {
    await api("/demo/delete", { method: "POST" });
    $("delete-confirm").hidden = true;
    $("delete-msg").textContent = "Demo data deleted. To use the demo again, restart the server — a fresh demo database is created.";
    $("delete-msg").className = "form-msg ok";
    residentSignedIn = false;
    $("resident-app").hidden = true;
    $("resident-login").hidden = false;
  } catch (err) { showAlert(err.message); }
});

/* ---------------- contact ---------------- */
let contactIdentity = null;
$("btn-login-contact").addEventListener("click", async () => {
  clearAlert();
  contactIdentity = $("contact-picker").value;
  try {
    await api("/demo/login", { method: "POST", body: JSON.stringify({ identity: contactIdentity }) });
    $("contact-app").hidden = false;
    $("contact-login-hint").textContent = "";
    $("contact-status").textContent = "Signed in as " +
      (contactIdentity === "contact-a" ? "Contact A" : "Contact B") + ".";
    await loadContactInbox();
  } catch (err) {
    $("contact-login-hint").textContent = "Log in failed: " + err.message;
  }
});
$("btn-logout-contact").addEventListener("click", async () => {
  try { await api("/demo/logout", { method: "POST" }); } catch (_) { /* already logged out */ }
  $("contact-app").hidden = true;
});
$("btn-refresh-contact").addEventListener("click", loadContactInbox);
async function loadContactInbox() {
  const list = $("contact-list");
  try {
    const data = await api("/contact/invitations");
    const invs = data.invitations || [];
    $("contact-empty").hidden = invs.length > 0;
    list.innerHTML = "";
    invs.forEach((inv) => {
      const li = document.createElement("li");
      li.innerHTML = `<div class="inv-meta"><strong>Invitation ${inv.invitation_id}</strong><br>` +
        `Visit: ${escapeHtml(fmtTime(inv.slot_start_utc))}<br>` +
        `<span class="fine">Expires: ${escapeHtml(fmtTime(inv.expires_utc))}</span></div>`;
      list.appendChild(li);
    });
    if (!invs.length) {
      $("contact-empty").textContent = contactIdentity === "contact-b"
        ? "No invitations for Contact B right now — nothing has been approved for you."
        : "No invitations for you right now.";
    }
  } catch (err) { showAlert("Could not load your invitations: " + err.message); }
}

/* ---------------- research: per-home chart ---------------- */
(function renderPerHome() {
  const chart = $("perhome-chart");
  const legend = document.createElement("p");
  legend.className = "chart-legend";
  legend.innerHTML = '<span><span class="swatch forest"></span>Frozen forest</span>' +
    '<span><span class="swatch recency"></span>Recency baseline</span>';
  chart.appendChild(legend);
  const max = 0.25;
  PER_HOME.forEach((row) => {
    const wrap = document.createElement("div");
    wrap.className = "chart-row";
    const label = document.createElement("span");
    label.className = "home";
    label.textContent = row.home;
    const bars = document.createElement("div");
    bars.className = "chart-bars";
    const f = document.createElement("div");
    f.className = "bar"; f.style.width = (row.forest / max * 100).toFixed(1) + "%";
    f.title = `Frozen forest ${row.forest}`;
    const r = document.createElement("div");
    r.className = "bar recency"; r.style.width = (row.recency / max * 100).toFixed(1) + "%";
    r.title = `Recency baseline ${row.recency}`;
    bars.append(f, r);
    const vals = document.createElement("span");
    vals.textContent = `${row.forest.toFixed(3)} / ${row.recency.toFixed(3)}`;
    wrap.append(label, bars, vals);
    chart.appendChild(wrap);
  });
  const tbody = document.querySelector("#perhome-table tbody");
  PER_HOME.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<th scope="row">${escapeHtml(row.home)}</th><td>${row.n.toLocaleString("en-US")}</td>` +
      `<td>${row.forest.toFixed(4)}</td><td>${row.recency.toFixed(4)}</td>`;
    tbody.appendChild(tr);
  });
})();
