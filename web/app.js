/* KindWindow hosted demo — self-contained, browser-only.
   No backend, no network calls: an in-browser store below mirrors the
   rules of the in-repo FastAPI app (src/kindwindow/app/app.py), and the
   note parser mirrors the Step 06 parser v1.0.0
   (src/kindwindow/preferences/parser.py). State lives in memory only;
   reloading the page re-seeds the demo (one resident, Contact A and
   Contact B granted, no preferences, no invitations, not paused).
   The Research view shows verified aggregates only — no number is
   invented here. */
"use strict";

const DAY_NAMES = { 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun" };
const SYNTHETIC_PROBABILITY = 0.42;
const INVITE_TTL_SECONDS = 3600; // short-lived invite: 1 hour

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
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- HH:MM / slot rules (mirrors app.py) ---------------- */
const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;
function parseHhmm(value) {
  if (typeof value !== "string") return null;
  const m = HHMM.exec(value);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
function fmtMinutes(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
function inRange(minute, start, end) {
  if (end > start) return start <= minute && minute < end;
  return minute >= start || minute < end; // overnight
}
function rangeMinutes(start, end) {
  const s = new Set();
  if (end > start) { for (let m = start; m < end; m++) s.add(m); }
  else { for (let m = start; m < 1440; m++) s.add(m); for (let m = 0; m < end; m++) s.add(m); }
  return s;
}
function quietCoversPreferred(pStart, pEnd, qStart, qEnd) {
  const pref = rangeMinutes(pStart, pEnd);
  const quiet = rangeMinutes(qStart, qEnd);
  for (const m of pref) if (!quiet.has(m)) return false;
  return true;
}
function checkSlot(slotStartUtc, prefs) {
  if (!prefs || !prefs.preferred_days || !prefs.preferred_start) {
    return "no preferred hours set; set preferred hours first";
  }
  const dt = new Date(slotStartUtc * 1000);
  const minute = dt.getUTCHours() * 60 + dt.getUTCMinutes();
  const weekday = dt.getUTCDay() === 0 ? 7 : dt.getUTCDay(); // ISO weekday
  if (!prefs.preferred_days.includes(weekday)) return "slot is not on a preferred day";
  if (!inRange(minute, parseHhmm(prefs.preferred_start), parseHhmm(prefs.preferred_end))) {
    return "slot is outside preferred hours";
  }
  if (prefs.quiet_start && inRange(minute, parseHhmm(prefs.quiet_start), parseHhmm(prefs.quiet_end))) {
    return "slot is within quiet hours";
  }
  return null;
}

/* ---------------- note parser (mirrors parser.py v1.0.0) ---------------- */
const DAY_NAME_TO_ISO = {
  monday: 1, mon: 1, tuesday: 2, tues: 2, tue: 2, wednesday: 3, wed: 3,
  thursday: 4, thur: 4, thurs: 4, thu: 4, friday: 5, fri: 5,
  saturday: 6, sat: 6, sunday: 7, sun: 7,
};
const DAY_ALT = Object.keys(DAY_NAME_TO_ISO).sort((a, b) => b.length - a.length).join("|");
const DAY_RE = `(?:${DAY_ALT})s?\\b`;
const GROUP_RE = "(?:weekdays?|weekends?|every\\s+day|everyday|daily|all\\s+days?|each\\s+day|every\\s+weekday|every\\s+weekend)";
const DAY_RANGE_RE = `${DAY_RE}\\s*(?:-|\\bto\\b|\\bthrough\\b|\\bthru\\b)\\s*${DAY_RE}`;
const DAY_LIST_RE = `${DAY_RE}(?:\\s*,\\s*${DAY_RE}|\\s+and\\s+${DAY_RE}|\\s*&\\s*${DAY_RE}|\\s*,\\s*and\\s*${DAY_RE})*`;
const DAY_EXPR_RE = new RegExp(`\\b(?:${GROUP_RE}|${DAY_RANGE_RE}|${DAY_LIST_RE})`, "g");
const DAY_TOKEN_RE = new RegExp(`\\b(${DAY_ALT})s?\\b`, "g");
const GROUPS = {
  weekday: [1, 2, 3, 4, 5], weekdays: [1, 2, 3, 4, 5],
  weekend: [6, 7], weekends: [6, 7],
  "every day": [1, 2, 3, 4, 5, 6, 7], everyday: [1, 2, 3, 4, 5, 6, 7],
  daily: [1, 2, 3, 4, 5, 6, 7], "all day": [1, 2, 3, 4, 5, 6, 7],
  "all days": [1, 2, 3, 4, 5, 6, 7], "each day": [1, 2, 3, 4, 5, 6, 7],
  "every weekday": [1, 2, 3, 4, 5], "every weekend": [6, 7],
};
const DAY_REFUSAL_RE = /\b(except|excluding|excluded|but\s+not|other\s+than|every\s+other|alternate|alternating|unless|apart\s+from)\b/;
const TIME_RE = "(?:noon|midnight|\\d{1,2}:\\d{2}\\s*(?:am|pm)?|\\d{1,2}\\s*(?:am|pm)?)";
const RANGE_RE = new RegExp(`\\b(${TIME_RE})\\s*(?:to|until|till|through|-)\\s*(${TIME_RE})`, "g");
const BETWEEN_RE = new RegExp(`\\bbetween\\s+(${TIME_RE})\\s+and\\s+(${TIME_RE})`, "g");
const STRAY_TIME_RE = /\b(?:noon|midnight|\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))\b/g;
const HEDGE_RE = /\b(around|about|approx(?:imately)?|roughly|maybe|~)\b/;
const QUIET_MARKER_RE = /(?:\bquiet\b|\bdo\s+not\s+(?:ring|call|disturb)|\bdon'?t\s+(?:ring|call|disturb)|\bnever\s+(?:ring|call|disturb)|\bno\s+(?:ringing|calls?|ring\b)|\bdo\s+not\s+disturb\b|\bsilent\b|\bsilence\b)/;
const PREFERRED_MARKER_RE = /(?:\bavailable\b|\bavailability\b|\bcall\b|\bring\b|\bgood\b|\bworks?\b|\bprefer\w*\b|\bwelcome\b)/;

function normalizeText(text) {
  let out = String(text).normalize("NFKC");
  for (const ch of "‐‑‒–—―−") out = out.split(ch).join("-");
  for (const ch of "“”«»") out = out.split(ch).join('"');
  for (const ch of "‘’‚‛") out = out.split(ch).join("'");
  out = out.replace(/ /g, " ").replace(/​/g, "");
  return out.replace(/\s+/g, " ").trim().toLowerCase();
}
function expandDayExpr(expr) {
  expr = expr.trim();
  if (Object.prototype.hasOwnProperty.call(GROUPS, expr)) return [...GROUPS[expr]];
  const tokens = [...expr.matchAll(DAY_TOKEN_RE)].map((m) => m[1]);
  if (/(-|\bto\b|\bthrough\b|\bthru\b)/.test(expr) && tokens.length === 2) {
    const start = DAY_NAME_TO_ISO[tokens[0].toLowerCase()];
    const end = DAY_NAME_TO_ISO[tokens[1].toLowerCase()];
    if (start === end) return null;
    const days = [];
    let cur = start;
    for (;;) { days.push(cur); if (cur === end) break; cur = (cur % 7) + 1; }
    return days.sort((a, b) => a - b);
  }
  if (tokens.length) return [...new Set(tokens.map((t) => DAY_NAME_TO_ISO[t.toLowerCase()]))].sort((a, b) => a - b);
  return null;
}
function parseDays(text) {
  if (DAY_REFUSAL_RE.test(text)) return null;
  const matches = [...text.matchAll(DAY_EXPR_RE)];
  if (!matches.length) return null;
  const expanded = matches.map((m) => expandDayExpr(m[0]));
  if (expanded.some((e) => e === null)) return null;
  const first = JSON.stringify(expanded[0]);
  if (expanded.every((e) => JSON.stringify(e) === first)) return expanded[0];
  return null; // two different day selectors: conflicting -> refuse
}
function ampmOf(token) {
  const m = /\b(am|pm)\b/.exec(token);
  return m ? m[1] : null;
}
function parseTimeToken(token, partnerAmpm) {
  token = token.trim();
  if (token === "noon") return 12 * 60;
  if (token === "midnight") return 0;
  let m = /^(\d{1,2}):(\d{2})\s*(am|pm)?$/.exec(token);
  if (m) {
    let hour = Number(m[1]); const minute = Number(m[2]); const ampm = m[3];
    if (minute > 59) return null;
    if (ampm) {
      if (hour < 1 || hour > 12) return null;
      if (ampm === "am") hour = hour === 12 ? 0 : hour;
      else hour = hour === 12 ? 12 : hour + 12;
    } else if (hour > 23) return null;
    return hour * 60 + minute;
  }
  m = /^(\d{1,2})\s*(am|pm)?$/.exec(token);
  if (m) {
    let hour = Number(m[1]); const ampm = m[2] || partnerAmpm;
    if (ampm === null || ampm === undefined) return null; // bare hour, no marker: out of grammar
    if (hour < 1 || hour > 12) return null;
    if (ampm === "am") hour = hour === 12 ? 0 : hour;
    else hour = hour === 12 ? 12 : hour + 12;
    return hour * 60;
  }
  return null;
}
function parseRanges(text) {
  const spans = [];
  for (const m of text.matchAll(BETWEEN_RE)) spans.push([m.index, m.index + m[0].length, m[1], m[2]]);
  for (const m of text.matchAll(RANGE_RE)) {
    if (spans.some(([s, e]) => (s <= m.index && m.index < e) || (s < m.index + m[0].length && m.index + m[0].length <= e))) continue;
    spans.push([m.index, m.index + m[0].length, m[1], m[2]]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  if (spans.length > 2) return [null, null];
  for (const m of text.matchAll(STRAY_TIME_RE)) {
    if (!spans.some(([s, e]) => s <= m.index && m.index + m[0].length <= e)) return [null, null];
  }
  const preferred = [];
  const quiet = [];
  let refusedPreferred = false;
  let refusedQuiet = false;
  let invalidRange = false;
  spans.forEach(([start, end, rawA, rawB], idx) => {
    const a = parseTimeToken(rawA, ampmOf(rawB));
    const b = parseTimeToken(rawB, ampmOf(rawA));
    if (a === null || b === null || a === b) { invalidRange = true; return; }
    const prevEnd = idx ? spans[idx - 1][1] : 0;
    const before = text.slice(prevEnd, start);
    const semi = text.indexOf(";", end);
    let after = text.slice(end, semi === -1 ? text.length : semi);
    if (idx + 1 < spans.length && semi === -1) after = text.slice(end, spans[idx + 1][0]);
    let isQuiet = QUIET_MARKER_RE.test(before);
    if (!isQuiet && !PREFERRED_MARKER_RE.test(before) && QUIET_MARKER_RE.test(after)) isQuiet = true;
    const hedged = HEDGE_RE.test(before) || HEDGE_RE.test(after);
    if (hedged) { if (isQuiet) refusedQuiet = true; else refusedPreferred = true; return; }
    if (isQuiet) quiet.push([a, b]); else preferred.push([a, b]);
  });
  if (invalidRange) return [null, null];
  if (preferred.length > 1) refusedPreferred = true;
  if (quiet.length > 1) refusedQuiet = true;
  const prefOut = (!refusedPreferred && preferred.length) ? [fmtMinutes(preferred[0][0]), fmtMinutes(preferred[0][1])] : null;
  const quietOut = (!refusedQuiet && quiet.length) ? [fmtMinutes(quiet[0][0]), fmtMinutes(quiet[0][1])] : null;
  if (prefOut !== null && quietOut !== null && prefOut[0] === quietOut[0] && prefOut[1] === quietOut[1]) return [null, null];
  return [prefOut, quietOut];
}
/* Returns {days, preferred_range, quiet_range}; null means unspecified/refused.
   Unclear or conflicting parts are never guessed. */
function parseNote(text) {
  const norm = normalizeText(text);
  const days = parseDays(norm);
  const [preferredRange, quietRange] = parseRanges(norm);
  return { days, preferred_range: preferredRange, quiet_range: quietRange };
}

/* ---------------- in-browser demo store (mirrors app.py + store.py) ---------------- */
const DEMO_LABEL = "Hosted demo — synthetic identities only. No real people are contacted.";
const CONTACT_LABELS = { 1: "Contact A", 2: "Contact B" };

function seedStore() {
  return {
    // Seeded exactly like store.init_db: resident exists, both contacts
    // granted, no preferences, no invitations, not paused.
    paused: false,
    preferences: null,
    pendingPreferences: null,
    grants: new Set([1, 2]),
    invitations: [],
    nextInvitationId: 1,
    session: null, // { role: "resident" | "contact", contact_id?: number }
  };
}
let store = seedStore();

function nowSeconds() { return Math.floor(Date.now() / 1000); }

function fail(message) { throw new Error(message); }

function residentView(inv) {
  return {
    invitation_id: inv.invitation_id,
    resident_id: 1,
    contact_id: inv.contact_id,
    slot_start_utc: inv.slot_start_utc,
    state: inv.state,
    expires_utc: inv.expires_utc,
    request_key: inv.request_key,
    experimental_probability: SYNTHETIC_PROBABILITY,
  };
}
function contactView(inv) {
  return {
    invitation_id: inv.invitation_id,
    slot_start_utc: inv.slot_start_utc,
    expires_utc: inv.expires_utc,
  };
}
function prefsView(p) {
  if (!p) return null;
  return {
    preferred_days: p.preferred_days ? [...p.preferred_days] : [],
    preferred_start: p.preferred_start,
    preferred_end: p.preferred_end,
    quiet_start: p.quiet_start,
    quiet_end: p.quiet_end,
  };
}
/* Mark approved invitations whose expiry has been reached as expired.
   Strict: at exactly expires_utc access ends. */
function expireStale() {
  const now = nowSeconds();
  for (const inv of store.invitations) {
    if (inv.state === "approved" && inv.expires_utc !== null && inv.expires_utc <= now) {
      inv.state = "expired";
      inv.expires_utc = null;
    }
  }
}
function parseRange(value) {
  let start; let end;
  if (value && typeof value === "object" && !Array.isArray(value)) { ({ start, end } = value); }
  else if (Array.isArray(value) && value.length === 2) { [start, end] = value; }
  else return null;
  if (parseHhmm(start) === null || parseHhmm(end) === null) return null;
  if (start === end) return null;
  return [String(start), String(end)];
}

/* Local stand-in for the JSON API. Same paths, same payload shapes, same
   error messages as the FastAPI app, so the UI code below is unchanged. */
async function api(path, options = {}) {
  const method = options.method || "GET";
  const body = options.body ? JSON.parse(options.body) : undefined;
  const match = /^\/invitations\/(\d+)\/(approve|postpone|reject|revoke)$/.exec(path);
  const requireRole = (role) => {
    if (!store.session) fail("authentication required");
    if (store.session.role !== role) fail("forbidden");
    return store.session;
  };

  if (path === "/demo/login" && method === "POST") {
    const identity = body && body.identity;
    if (identity === "resident") store.session = { role: "resident" };
    else if (identity === "contact-a") store.session = { role: "contact", contact_id: 1 };
    else if (identity === "contact-b") store.session = { role: "contact", contact_id: 2 };
    else fail("authentication failed");
    return { identity, role: store.session.role, demo: DEMO_LABEL };
  }
  if (path === "/demo/logout" && method === "POST") {
    store.session = null;
    return { logged_out: true };
  }
  if (path === "/preferences" && method === "GET") {
    requireRole("resident");
    return {
      demo: DEMO_LABEL,
      resident_id: 1,
      paused: store.paused,
      preferences: prefsView(store.preferences),
      pending_preferences: prefsView(store.pendingPreferences),
      contacts: [...store.grants].sort().map((id) => ({ contact_id: id, label: CONTACT_LABELS[id] })),
    };
  }
  if (path === "/preferences" && method === "POST") {
    requireRole("resident");
    if (!body || typeof body !== "object") fail("invalid preferences");
    const stored = store.preferences || {
      preferred_days: [], preferred_start: null, preferred_end: null,
      quiet_start: null, quiet_end: null,
    };
    const merged = { ...stored };
    if ("preferred_days" in body) {
      const days = body.preferred_days;
      if (!Array.isArray(days) || !days.length ||
          days.some((d) => !Number.isInteger(d) || d < 1 || d > 7)) {
        fail("preferred_days must be a non-empty list of ISO weekdays 1-7");
      }
      merged.preferred_days = [...new Set(days)].sort((a, b) => a - b);
    }
    if ("preferred_range" in body) {
      const rng = parseRange(body.preferred_range);
      if (!rng) fail("preferred_range must be HH:MM start/end with start != end");
      [merged.preferred_start, merged.preferred_end] = rng;
    }
    if ("quiet_range" in body) {
      if (body.quiet_range === null) { merged.quiet_start = null; merged.quiet_end = null; }
      else {
        const rng = parseRange(body.quiet_range);
        if (!rng) fail("quiet_range must be HH:MM start/end with start != end");
        [merged.quiet_start, merged.quiet_end] = rng;
      }
    }
    if (!merged.preferred_days.length || !merged.preferred_start) {
      fail("preferred_days and preferred_range are required");
    }
    if (merged.quiet_start && quietCoversPreferred(
      parseHhmm(merged.preferred_start), parseHhmm(merged.preferred_end),
      parseHhmm(merged.quiet_start), parseHhmm(merged.quiet_end))) {
      fail("quiet hours cover all preferred hours; no usable slot");
    }
    if (body.contact_ids !== undefined && body.contact_ids !== null) {
      const ids = body.contact_ids;
      if (!Array.isArray(ids) || ids.some((c) => !Number.isInteger(c))) {
        fail("contact_ids must be a list of integers");
      }
      if (ids.some((c) => !(c in CONTACT_LABELS))) fail("unknown contact");
      store.grants = new Set(ids);
    }
    store.preferences = merged;
    return { preferences: prefsView(merged), demo: DEMO_LABEL };
  }
  if (path === "/notes/parse" && method === "POST") {
    requireRole("resident");
    const text = body && typeof body.text === "string" ? body.text : "";
    const parsed = parseNote(text);
    if (parsed.days === null && parsed.preferred_range === null && parsed.quiet_range === null) {
      return {
        parsed,
        pending_preferences: null,
        detail: "nothing could be parsed from the note; preferences unchanged",
      };
    }
    const pref = parsed.preferred_range;
    const quiet = parsed.quiet_range;
    store.pendingPreferences = {
      preferred_days: parsed.days,
      preferred_start: pref ? pref[0] : null,
      preferred_end: pref ? pref[1] : null,
      quiet_start: quiet ? quiet[0] : null,
      quiet_end: quiet ? quiet[1] : null,
    };
    return {
      parsed,
      pending_preferences: prefsView(store.pendingPreferences),
      detail: "parsed fields are pending confirmation",
    };
  }
  if (path === "/preferences/confirm" && method === "POST") {
    requireRole("resident");
    const confirm = !body || !("confirm" in body) ? true : Boolean(body.confirm);
    return applyOrCancelPending(confirm);
  }
  if (path === "/preferences/cancel" && method === "POST") {
    requireRole("resident");
    return applyOrCancelPending(false);
  }
  if (path === "/invitations" && method === "POST") {
    requireRole("resident");
    if (!body || typeof body !== "object") fail("invalid invitation");
    const contactId = body.contact_id;
    const slot = body.slot_start_utc;
    const requestKey = body.request_key;
    if (!Number.isInteger(contactId) || !Number.isInteger(slot) || !requestKey) {
      fail("contact_id, slot_start_utc and request_key are required");
    }
    if (slot <= nowSeconds()) fail("slot must be in the future");
    expireStale();
    if (store.paused) fail("resident is paused");
    if (!store.grants.has(contactId)) fail("forbidden");
    const existing = store.invitations.find((i) => i.request_key === requestKey);
    if (existing) {
      if (existing.contact_id !== contactId || existing.slot_start_utc !== slot) {
        fail("request_key already used with a different invitation");
      }
      return residentView(existing);
    }
    const slotErr = checkSlot(slot, store.preferences);
    if (slotErr) fail(slotErr);
    const inv = {
      invitation_id: store.nextInvitationId++,
      contact_id: contactId,
      slot_start_utc: slot,
      state: "pending",
      expires_utc: null,
      request_key: requestKey,
    };
    store.invitations.push(inv);
    return residentView(inv);
  }
  if (path === "/invitations" && method === "GET") {
    requireRole("resident");
    expireStale();
    const invs = [...store.invitations]
      .sort((a, b) => a.slot_start_utc - b.slot_start_utc || a.invitation_id - b.invitation_id);
    return { demo: DEMO_LABEL, invitations: invs.map(residentView) };
  }
  if (match && method === "POST") {
    requireRole("resident");
    const id = Number(match[1]);
    const action = match[2];
    expireStale();
    const inv = store.invitations.find((i) => i.invitation_id === id);
    if (!inv) fail("invitation not found");
    if (action === "approve") {
      if (!["pending", "postponed"].includes(inv.state)) {
        fail("invitation cannot be approved in its current state");
      }
      if (store.paused) fail("resident is paused; approval requires resume");
      if (!store.grants.has(inv.contact_id)) fail("forbidden");
      const slotErr = checkSlot(inv.slot_start_utc, store.preferences);
      if (slotErr) fail(slotErr);
      inv.state = "approved";
      inv.expires_utc = nowSeconds() + INVITE_TTL_SECONDS;
      return residentView(inv);
    }
    if (action === "postpone") {
      const newSlot = body && body.new_slot_start_utc;
      if (!Number.isInteger(newSlot)) fail("new_slot_start_utc is required");
      if (newSlot <= nowSeconds()) fail("slot must be in the future");
      if (!["pending", "approved", "postponed"].includes(inv.state)) {
        fail("invitation cannot be postponed in its current state");
      }
      const slotErr = checkSlot(newSlot, store.preferences);
      if (slotErr) fail(slotErr);
      const clash = store.invitations.some((i) =>
        i.invitation_id !== id && i.contact_id === inv.contact_id &&
        i.slot_start_utc === newSlot && ["pending", "approved"].includes(i.state));
      if (clash) fail("slot already has a live invitation for this contact");
      // Postpone requires fresh approval: back to a non-approved state
      // with expiry cleared.
      inv.state = "postponed";
      inv.slot_start_utc = newSlot;
      inv.expires_utc = null;
      return residentView(inv);
    }
    if (action === "reject") {
      if (!["pending", "approved", "postponed"].includes(inv.state)) {
        fail("invitation cannot be rejected in its current state");
      }
      inv.state = "rejected";
      inv.expires_utc = null;
      return residentView(inv);
    }
    if (action === "revoke") {
      if (inv.state === "revoked") return residentView(inv); // idempotent
      if (!["pending", "approved", "postponed"].includes(inv.state)) {
        fail("invitation cannot be revoked in its current state");
      }
      inv.state = "revoked";
      inv.expires_utc = null;
      return residentView(inv);
    }
  }
  if (path === "/pause" && method === "POST") {
    requireRole("resident");
    expireStale();
    store.paused = true;
    // Privacy-first: approved invitations are invalidated (returned to
    // pending, expiry cleared) and need a fresh approval after resume;
    // they are never auto-revived.
    let invalidated = 0;
    for (const inv of store.invitations) {
      if (inv.state === "approved") { inv.state = "pending"; inv.expires_utc = null; invalidated++; }
    }
    return {
      paused: true,
      invalidated_approved: invalidated,
      detail: "Paused. Approved invitations were invalidated and need fresh approval after resume.",
    };
  }
  if (path === "/resume" && method === "POST") {
    requireRole("resident");
    store.paused = false;
    return {
      paused: false,
      detail: "Resumed. Previously approved invitations remain pending until re-approved.",
    };
  }
  if (path === "/contact/invitations" && method === "GET") {
    const sess = requireRole("contact");
    expireStale();
    const invs = store.invitations
      .filter((i) => i.contact_id === sess.contact_id && store.grants.has(i.contact_id) &&
        !store.paused && i.state === "approved" && i.expires_utc !== null &&
        i.expires_utc > nowSeconds())
      .sort((a, b) => a.slot_start_utc - b.slot_start_utc || a.invitation_id - b.invitation_id);
    return { demo: DEMO_LABEL, invitations: invs.map(contactView) };
  }
  if (path === "/demo/delete" && method === "POST") {
    requireRole("resident");
    store = seedStore(); // reset to the starting state; login works again
    return {
      deleted: true,
      detail: "Synthetic demo records for this resident were deleted; the demo has been reset to its starting state.",
      remaining_invitations: 0,
    };
  }
  fail("not found");
}

function applyOrCancelPending(confirm) {
  const pending = store.pendingPreferences;
  if (pending === null) fail("no pending text-derived changes to confirm");
  if (!confirm) {
    store.pendingPreferences = null;
    return {
      confirmed: false, pending_preferences: null,
      detail: "pending text-derived changes cancelled; preferences unchanged",
    };
  }
  // Re-validate conflicts before applying (never silently overwrite).
  if (pending.quiet_start && pending.preferred_start &&
      quietCoversPreferred(
        parseHhmm(pending.preferred_start), parseHhmm(pending.preferred_end),
        parseHhmm(pending.quiet_start), parseHhmm(pending.quiet_end))) {
    fail("pending changes: quiet hours cover all preferred hours");
  }
  store.preferences = { ...pending };
  store.pendingPreferences = null;
  return { confirmed: true, preferences: prefsView(store.preferences), pending_preferences: null };
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
    $("delete-msg").textContent = "Demo data deleted. The demo has been reset to its starting state — log in again to start over.";
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
