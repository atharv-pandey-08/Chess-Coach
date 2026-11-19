/* Chess Coach — front-end. Vanilla JS, no build step. */
"use strict";

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

const CLS_GLYPH = {
  Brilliant: "!!", Great: "!", Best: "★", Excellent: "✓", Good: "✓", Book: "B",
  Forced: "→", Inaccuracy: "?!", Mistake: "?", Blunder: "??",
};
const CLS_ORDER = ["Brilliant", "Great", "Best", "Excellent", "Good", "Forced", "Inaccuracy", "Mistake", "Blunder"];
const LIST_BADGES = new Set(["Brilliant", "Great", "Inaccuracy", "Mistake", "Blunder"]);
const CLS_HL = {
  Brilliant: "rgba(38,194,181,.5)", Great: "rgba(91,143,240,.5)", Inaccuracy: "rgba(243,200,75,.5)",
  Mistake: "rgba(242,145,61,.5)", Blunder: "rgba(240,84,92,.5)",
};
const badge = (cls, extra = "") => cls ? `<span class="badge b-${cls} ${extra}" title="${cls}">${CLS_GLYPH[cls] || ""}</span>` : "";

const TC_ICON = {
  bullet: '<svg viewBox="0 0 24 24" class="ico"><circle cx="15" cy="12" r="4"/><path d="M3 12h6M5 8h4M5 16h4"/></svg>',
  blitz: '<svg viewBox="0 0 24 24" class="ico"><path d="M13 3 5 14h6l-1 7 8-11h-6z"/></svg>',
  rapid: '<svg viewBox="0 0 24 24" class="ico"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M10 2h4"/></svg>',
  daily: '<svg viewBox="0 0 24 24" class="ico"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>',
  imported: '<svg viewBox="0 0 24 24" class="ico"><path d="M12 4v12m0 0-4-4m4 4 4-4M5 20h14"/></svg>',
};

function relDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  if (diff < 172800) return "Yesterday";
  if (diff < 604800) return `${Math.round(diff / 86400)} days ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

function fmtTC(tc) {
  if (!tc) return "";
  if (tc.includes("/")) return `${Math.round(+tc.split("/")[1] / 86400)} day`;
  const [base, inc] = tc.split("+").map(Number);
  const m = base >= 60 ? `${base / 60}` : `${base}s`;
  return inc ? `${m}+${inc}` : `${m} min`;
}

function fmtClock(sec) {
  if (sec == null) return "";
  if (sec < 20) return sec.toFixed(1);
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const S = {
  cfg: null,
  username: "",
  games: [],
  filters: { tc: "all", res: "all", q: "" },
  data: null,       // /api/review response
  gameId: null,
  ply: 0,
  prevPly: 0,
  flipped: false,
  arrows: store.get("arrows", true),
  sound: store.get("sound", true),
  boardTheme: store.get("boardTheme", "walnut"),
  playTimer: null,
  pollTimer: null,
  coach: {},        // "<game>:<ply>" -> coach payload | "loading" | {error}
  coachAi: {},      // same key -> AI payload | "loading"
  story: {},        // game id -> story payload | "loading"
  variation: null,  // {kind,label,from_ply,uci,san,fens,step,prevStep} while "Show me" is active
};

// ---------------------------------------------------------------------------
// Theme / sound
// ---------------------------------------------------------------------------
const ICON_SUN = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
const ICON_SOUND = '<svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/></svg>';
const ICON_MUTE = '<svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4zM22 9l-6 6M16 9l6 6"/></svg>';

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#theme-btn").innerHTML = theme === "light" ? ICON_MOON : ICON_SUN;
  store.set("theme", theme);
}
function applySound() { $("#sound-btn").innerHTML = S.sound ? ICON_SOUND : ICON_MUTE; }

const BOARD_THEMES = { walnut: ["#ebd9b4", "#b08a62"], emerald: ["#eeeed2", "#6f9a5a"], slate: ["#dee3e6", "#8ca2ad"], ocean: ["#e3ecf4", "#5d84a8"], midnight: ["#9aa6b8", "#4b5568"] };
function applyBoardTheme(name) {
  S.boardTheme = name;
  document.documentElement.dataset.board = name;
  store.set("boardTheme", name);
  $$(".theme-sw").forEach((b) => b.classList.toggle("on", b.dataset.v === name));
}

let audioCtx;
function playSound(capture) {
  if (!S.sound) return;
  try {
    audioCtx ??= new (window.AudioContext || window.webkitAudioContext)();
    const t = audioCtx.currentTime;
    const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(capture ? 420 : 640, t);
    osc.frequency.exponentialRampToValueAtTime(capture ? 160 : 280, t + 0.08);
    gain.gain.setValueAtTime(capture ? 0.22 : 0.14, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.12);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(t); osc.stop(t + 0.13);
  } catch { /* audio unavailable */ }
}

// ---------------------------------------------------------------------------
// Home view
// ---------------------------------------------------------------------------
async function loadHome(username) {
  S.username = username;
  store.set("username", username);
  $("#user-input").value = username;
  $("#games-sub").textContent = "Fetching games from Chess.com…";
  $("#games-list").innerHTML = Array.from({ length: 7 }, (_, i) => `<div class="skeleton" style="animation-delay:${i * 60}ms"></div>`).join("");
  $("#profile-card").innerHTML = `<div class="skeleton" style="height:180px"></div>`;
  $("#stats-card").innerHTML = "";

  const profileP = api(`/api/profile?username=${encodeURIComponent(username)}`).catch(() => null);
  try {
    const data = await api(`/api/games?username=${encodeURIComponent(username)}`);
    S.games = data.games;
    renderGames();
    renderStats();
  } catch (e) {
    S.games = [];
    $("#games-sub").textContent = "";
    $("#games-list").innerHTML = `<div class="card empty"><h3>Couldn't load games</h3><p>${esc(e.message)}</p></div>`;
    $("#stats-card").innerHTML = "";
  }
  renderProfile(await profileP);
}

function renderProfile(p) {
  const el = $("#profile-card");
  if (!p) { el.innerHTML = ""; return; }
  const labels = { bullet: "Bullet", blitz: "Blitz", rapid: "Rapid", daily: "Daily", puzzles: "Puzzles" };
  const ratings = Object.entries(p.ratings || {}).map(([k, r]) => `
    <div class="rating">
      <div class="rating-k">${TC_ICON[k] || TC_ICON.rapid}${labels[k] || k}</div>
      <div class="rating-v">${r.rating ?? "—"}</div>
      ${r.best ? `<div class="rating-best">Best ${r.best}</div>` : ""}
    </div>`).join("");
  el.innerHTML = `
    <div class="profile-top">
      ${p.avatar ? `<img class="avatar" src="${esc(p.avatar)}" alt="" />` : `<div class="avatar">${esc((p.username || "?")[0].toUpperCase())}</div>`}
      <div>
        <div class="profile-name">${p.title ? `<span class="title-tag">${esc(p.title)}</span>` : ""}${esc(p.username)}</div>
        <a class="profile-link" href="https://www.chess.com/member/${encodeURIComponent(p.username)}" target="_blank" rel="noopener">chess.com/member/${esc(p.username)} ↗</a>
      </div>
    </div>
    <div class="ratings">${ratings || `<p class="muted">No ratings available.</p>`}</div>`;
}

function renderStats() {
  const g = S.games;
  if (!g.length) { $("#stats-card").innerHTML = ""; return; }
  const w = g.filter((x) => x.result === "Win").length, l = g.filter((x) => x.result === "Loss").length, d = g.length - w - l;
  const analyzed = g.filter((x) => x.accuracy != null);
  const avgAcc = analyzed.length ? (analyzed.reduce((a, x) => a + x.accuracy, 0) / analyzed.length).toFixed(1) : null;
  const openings = {};
  g.forEach((x) => { if (x.opening) { const k = x.opening.split(" ").slice(0, 3).join(" "); openings[k] = (openings[k] || 0) + 1; } });
  const topOpening = Object.entries(openings).sort((a, b) => b[1] - a[1])[0];
  const bestWin = g.filter((x) => x.result === "Win" && x.opponent_rating).sort((a, b) => b.opponent_rating - a.opponent_rating)[0];
  let streak = 0; const first = g[0].result;
  for (const x of g) { if (x.result === first) streak++; else break; }
  const pct = (n) => (100 * n / g.length).toFixed(1);

  $("#stats-card").innerHTML = `
    <h3>Last ${g.length} games</h3>
    <div style="display:flex;align-items:baseline;gap:10px"><span class="big-number">${Math.round(100 * w / g.length)}%</span><span class="muted">win rate</span></div>
    <div class="wld"><span style="width:${pct(w)}%;background:var(--win)"></span><span style="width:${pct(d)}%;background:var(--draw)"></span><span style="width:${pct(l)}%;background:var(--loss)"></span></div>
    <div class="wld-legend"><span><i class="dot" style="background:var(--win)"></i><b>${w}</b> W</span><span><i class="dot" style="background:var(--draw)"></i><b>${d}</b> D</span><span><i class="dot" style="background:var(--loss)"></i><b>${l}</b> L</span></div>
    <div class="stat-rows">
      <div class="stat-row"><span>Avg. accuracy</span><b>${avgAcc ? `${avgAcc}%` : "Review a game"}${avgAcc ? ` <span class="muted" style="font-weight:500">(${analyzed.length})</span>` : ""}</b></div>
      <div class="stat-row"><span>Current streak</span><b class="${first === "Win" ? "t-Best" : first === "Loss" ? "t-Blunder" : ""}">${streak} ${first.toLowerCase()}${streak > 1 && first !== "Loss" ? "s" : streak > 1 ? "es" : ""}</b></div>
      ${topOpening ? `<div class="stat-row"><span>Favourite opening</span><b title="${esc(topOpening[0])}">${esc(topOpening[0])}</b></div>` : ""}
      ${bestWin ? `<div class="stat-row"><span>Best win</span><b>${esc(bestWin.opponent)} (${bestWin.opponent_rating})</b></div>` : ""}
    </div>`;
}

function renderGames() {
  const { tc, res, q } = S.filters;
  const needle = q.trim().toLowerCase();
  const list = S.games.filter((g) =>
    (tc === "all" || g.time_class === tc) &&
    (res === "all" || g.result === res) &&
    (!needle || g.opponent.toLowerCase().includes(needle) || (g.opening || "").toLowerCase().includes(needle)));

  $("#games-sub").textContent = S.games.length
    ? `${list.length} of ${S.games.length} games · ${S.username}`
    : `No games found for ${S.username}`;

  if (!list.length) {
    $("#games-list").innerHTML = `<div class="card empty"><h3>No games match</h3><p>Try a different filter${S.games.length ? "" : " or username"}.</p></div>`;
    return;
  }
  $("#games-list").innerHTML = list.map((g, i) => `
    <button class="game-row" data-id="${esc(g.id)}" style="animation-delay:${Math.min(i, 14) * 22}ms">
      <span class="res-stripe" style="background:var(--${g.result.toLowerCase()})"></span>
      <span class="res-badge res-${g.result}">${g.result === "Draw" ? "½" : g.result[0]}</span>
      <span class="g-opp">
        <span class="g-opp-name"><span class="color-chip ${g.user_color}" title="You played ${g.user_color}"></span>${esc(g.opponent)} <span class="muted" style="font-weight:500">${g.opponent_rating ?? ""}</span></span>
        <span class="g-opp-meta">${relDate(g.end_time)} · ${esc(g.termination ? g.termination.replace(/^.*? won /, "won ").replace(/^.*?\b(drawn|game abandoned)\b/i, "$1") : g.result_detail)}</span>
      </span>
      <span class="g-open" title="${esc(g.opening)}">${esc(g.opening || "—")}</span>
      <span class="g-tc">${TC_ICON[g.time_class] || TC_ICON.rapid}${esc(fmtTC(g.time_control) || g.time_class)}</span>
      ${g.accuracy != null ? `<span class="acc-pill" title="Your accuracy">${g.accuracy.toFixed(1)}%</span>` : `<span class="acc-pill none">Review</span>`}
      <span class="go"><svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg></span>
    </button>`).join("");
}

// ---------------------------------------------------------------------------
// Review view
// ---------------------------------------------------------------------------
async function openReview(id) {
  stopPlay();
  clearTimeout(S.pollTimer);
  S.gameId = id;
  S.data = null;
  S.ply = 0; S.prevPly = 0;
  S.variation = null;
  $("#game-head").innerHTML = `<div class="skeleton" style="height:70px"></div>`;
  $("#coach-card").innerHTML = "";
  $("#moves").innerHTML = "";
  $("#graph").innerHTML = "";
  $("#report").innerHTML = "";
  await pollReview(true);
}

async function pollReview(first = false) {
  const id = S.gameId;
  let data;
  try {
    data = await api(`/api/review/${encodeURIComponent(id)}`);
  } catch (e) {
    if (id !== S.gameId) return;
    $("#game-head").innerHTML = `<a href="#/" class="back-link">← Back to games</a><p class="err">${esc(e.message)}</p>`;
    return;
  }
  if (id !== S.gameId) return; // user navigated away meanwhile
  const wasDone = S.data?.status === "done";
  S.data = data;
  if (first) {
    S.flipped = data.game.user_color === "black";
    renderGameHead();
    buildSquares();
  }
  renderProgress();
  renderAll(false);
  if (data.status === "running" || data.status === "queued") {
    S.pollTimer = setTimeout(() => pollReview(), 650);
  } else if (data.status === "done" && !first && !wasDone) {
    toast("Analysis complete");
    const g = S.games.find((x) => x.id === id);
    const acc = data.review.summary[data.game.user_color].accuracy;
    if (g && acc != null) g.accuracy = acc;
  }
}

function renderProgress() {
  const d = S.data, el = $("#progress");
  if (d.status === "done") { el.hidden = true; return; }
  el.hidden = false;
  if (d.status === "error") {
    $("#progress-text").innerHTML = `<span class="err" style="margin:0">${esc(d.error)}</span>`;
    $("#progress-pct").textContent = "";
    $("#progress-fill").style.width = "0";
    return;
  }
  const pct = d.total ? Math.round(100 * d.progress / d.total) : 0;
  $("#progress-text").textContent = d.status === "queued" ? "Starting Stockfish…" : `Analyzing with Stockfish · depth ${S.cfg?.depth ?? ""}`;
  $("#progress-pct").textContent = `${d.progress}/${d.total}`;
  $("#progress-fill").style.width = pct + "%";
}

function renderGameHead() {
  const g = S.data.game;
  const date = g.end_time ? new Date(g.end_time * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  $("#game-head").innerHTML = `
    <a href="#/" class="back-link">← All games</a>
    <div class="gh-top">
      <div style="min-width:0">
        <div class="gh-title">vs ${esc(g.opponent)} <span class="res-badge res-${g.result}" style="display:inline-grid;width:auto;height:auto;padding:2px 8px;border-radius:7px;font-size:12px;margin-left:6px;vertical-align:2px">${g.result}</span></div>
        <div class="gh-sub">${[g.time_class !== "imported" ? `${fmtTC(g.time_control)} ${g.time_class}` : "Imported", date, g.opening].filter(Boolean).map(esc).join(" · ")}</div>
      </div>
      <div class="gh-links">
        ${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener" title="Open on Chess.com">Chess.com ↗</a>` : ""}
        <button id="copy-pgn" title="Copy PGN">PGN</button>
      </div>
    </div>`;
  $("#copy-pgn").onclick = () => navigator.clipboard.writeText(S.data.pgn).then(() => toast("PGN copied"), () => toast("Couldn't copy", true));
}

// ----- board ---------------------------------------------------------------
const FILES = "abcdefgh";
function sqToXY(sq) { // "e4" -> screen col/row
  const f = FILES.indexOf(sq[0]), r = +sq[1] - 1;
  return S.flipped ? [7 - f, r] : [f, 7 - r];
}

function buildSquares() {
  const wrap = $("#squares");
  let html = "";
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const f = S.flipped ? 7 - col : col, r = S.flipped ? row : 7 - row;
      const name = FILES[f] + (r + 1);
      const light = (f + r) % 2 === 1;
      let coords = "";
      if (row === 7) coords += `<span class="coord file">${FILES[f]}</span>`;
      if (col === 0) coords += `<span class="coord rank">${r + 1}</span>`;
      html += `<div class="sq ${light ? "l" : "d"}" data-sq="${name}">${coords}</div>`;
    }
  }
  wrap.innerHTML = html;
  $("#evalbar").classList.toggle("flipped", S.flipped);
}

function parseFen(fen) {
  const out = {};
  fen.split(" ")[0].split("/").forEach((row, i) => {
    let f = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) { f += +ch; continue; }
      const color = ch === ch.toUpperCase() ? "w" : "b";
      out[FILES[f] + (8 - i)] = color + ch.toLowerCase();
      f++;
    }
  });
  return out;
}

function boardView() {
  const d = S.data, v = S.variation;
  if (!v) return { pos: d.review.positions[S.ply], move: S.ply > 0 ? d.review.moves[S.ply - 1] : null };
  const base = d.review.positions[v.from_ply];
  if (!v.step) return { pos: { fen: base.fen, eval: base.eval }, move: null };
  const u = v.uci[v.step - 1];
  return { pos: { fen: v.fens[v.step - 1], eval: d.review.positions[S.ply].eval },
           move: { from: u.slice(0, 2), to: u.slice(2, 4), san: v.san[v.step - 1], classification: null } };
}

function renderBoard() {
  const d = S.data, { pos, move } = boardView(), v = S.variation;
  const pieces = parseFen(pos.fen);

  // squares: last-move + check highlight
  const sideToMove = pos.fen.split(" ")[1];
  const inCheck = move && /[+#]$/.test(move.san);
  const kingSq = inCheck ? Object.keys(pieces).find((s) => pieces[s] === sideToMove + "k") : null;
  const hlColor = move && CLS_HL[move.classification];
  $$(".sq").forEach((el) => {
    const s = el.dataset.sq;
    const on = move && (s === move.from || s === move.to);
    el.classList.toggle("hl", !!on);
    el.style.setProperty("--hl-color", on && hlColor ? hlColor : "");
    el.classList.toggle("check", s === kingSq);
  });

  // pieces
  const layer = $("#pieces");
  let html = "";
  for (const [sq, code] of Object.entries(pieces)) {
    const [x, y] = sqToXY(sq);
    html += `<div class="piece" data-sq="${sq}" style="background-image:url(/pieces/${code}.png);transform:translate(${x * 100}%,${y * 100}%)"></div>`;
  }
  if (move?.classification) {
    const [x, y] = sqToXY(move.to);
    html += `<div class="badge-sq" style="left:${x * 12.5}%;top:${y * 12.5}%">${badge(move.classification)}</div>`;
  }
  layer.innerHTML = html;

  // animate a single step (forward or backward), in the game or in a "Show me" line
  const cur = v ? v.step : S.ply, prev = v ? v.prevStep : S.prevPly;
  const delta = cur - prev;
  if (Math.abs(delta) === 1) {
    const m = v ? { from: v.uci[Math.max(cur, prev) - 1].slice(0, 2), to: v.uci[Math.max(cur, prev) - 1].slice(2, 4), san: v.san[Math.max(cur, prev) - 1] }
      : d.review.moves[Math.max(S.ply, S.prevPly) - 1];
    const [fromSq, toSq] = delta === 1 ? [m.from, m.to] : [m.to, m.from];
    const el = layer.querySelector(`.piece[data-sq="${toSq}"]`);
    if (el) {
      const [fx, fy] = sqToXY(fromSq), [tx, ty] = sqToXY(toSq);
      el.style.transform = `translate(${fx * 100}%,${fy * 100}%)`;
      el.getBoundingClientRect(); // force reflow so the transition runs
      el.classList.add("anim");
      el.style.transform = `translate(${tx * 100}%,${ty * 100}%)`;
    }
    if (delta === 1) playSound(m.san.includes("x"));
  }
  if (v) v.prevStep = v.step; else S.prevPly = S.ply;
  $("#board").classList.toggle("in-line", !!v);

  renderArrows(move, pos);
  renderEvalBar(pos.eval);
  renderPlayerBars(pieces);
}

function arrowPath(from, to) {
  const [x1, y1] = sqToXY(from).map((v) => v + 0.5), [x2, y2] = sqToXY(to).map((v) => v + 0.5);
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
  const ux = dx / len, uy = dy / len, px = -uy, py = ux;
  const shaft = 0.09, head = 0.24, headLen = 0.38;
  const sx = x1 + ux * 0.18, sy = y1 + uy * 0.18;
  const bx = x2 - ux * headLen, by = y2 - uy * headLen;
  const pts = [
    [sx + px * shaft, sy + py * shaft], [bx + px * shaft, by + py * shaft], [bx + px * head, by + py * head],
    [x2, y2], [bx - px * head, by - py * head], [bx - px * shaft, by - py * shaft], [sx - px * shaft, sy - py * shaft],
  ];
  return pts.map((p) => p.map((v) => v.toFixed(3)).join(",")).join(" ");
}

function renderArrows(move, pos) {
  let svg = "";
  if (S.variation) { $("#arrows").innerHTML = ""; return; }
  // The opponent's threat the move ignored, in red.
  const c = S.coach[`${S.gameId}:${S.ply}`];
  if (c?.threat && !c.threat.addressed) {
    svg += `<polygon points="${arrowPath(c.threat.uci.slice(0, 2), c.threat.uci.slice(2, 4))}" fill="rgba(230,90,80,.8)"/>`;
  }
  const bad = move && ["Inaccuracy", "Mistake", "Blunder"].includes(move.classification);
  if (bad && move.best_uci) {
    svg += `<polygon points="${arrowPath(move.best_uci.slice(0, 2), move.best_uci.slice(2, 4))}" fill="rgba(95,207,128,.85)"/>`;
  }
  if (S.arrows && pos.eval?.best && !(bad && move.best_uci)) {
    svg += `<polygon points="${arrowPath(pos.eval.best.slice(0, 2), pos.eval.best.slice(2, 4))}" fill="rgba(91,143,240,.78)"/>`;
  }
  $("#arrows").innerHTML = svg;
}

function renderEvalBar(ev) {
  const fill = $("#evalbar-fill"), label = $("#evalbar-label");
  if (!ev) { fill.style.height = "50%"; label.textContent = ""; return; }
  fill.style.height = ev.wp + "%";
  const whiteAhead = ev.wp >= 50;
  label.textContent = ev.text.replace(/^\+/, "");
  // put the label at the leading side's end of the bar
  const atTop = whiteAhead === S.flipped;
  label.classList.toggle("top", atTop);
  label.style.color = whiteAhead ? "#333" : "#eee";
}

const VALUES = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
const START = { p: 8, n: 2, b: 2, r: 2, q: 1 };
function renderPlayerBars(pieces) {
  const g = S.data.game, moves = S.data.review.moves;
  const counts = { w: {}, b: {} };
  Object.values(pieces).forEach((c) => (counts[c[0]][c[1]] = (counts[c[0]][c[1]] || 0) + 1));
  const material = (c) => Object.entries(counts[c]).reduce((a, [t, n]) => a + VALUES[t] * n, 0);
  const diff = material("w") - material("b");
  const base = (() => { const tc = g.time_control || ""; return /^\d+/.test(tc) && !tc.includes("/") ? +tc.split("+")[0] : null; })();
  const sideToMove = S.data.review.positions[S.ply].fen.split(" ")[1];
  const over = S.ply === moves.length;

  const bar = (color) => {
    const c = color[0], opp = c === "w" ? "b" : "w", info = g[color];
    let captured = "";
    for (const t of ["q", "r", "b", "n", "p"]) {
      const missing = Math.max(0, (START[t] || 0) - (counts[opp][t] || 0));
      for (let i = 0; i < missing; i++) captured += `<img src="/pieces/${opp}${t}.png" alt="" />`;
    }
    const adv = c === "w" ? diff : -diff;
    let clock = null;
    for (let i = S.ply - 1; i >= 0; i--) if (moves[i].color === color && moves[i].clock != null) { clock = moves[i].clock; break; }
    if (clock == null && moves.some((m) => m.clock != null)) clock = base;
    const active = !over && sideToMove === c;
    const low = clock != null && (clock < 20 || (base && clock < base * 0.1));
    const isUser = g.user_color === color;
    return `
      <span class="pb-avatar ${color}">${esc((info.name || "?")[0].toUpperCase())}</span>
      <span class="pb-name">${esc(info.name)}${isUser && g.time_class !== "imported" ? ' <span class="muted" style="font-weight:500">(you)</span>' : ""}</span>
      <span class="pb-rating">${info.rating ? `(${info.rating})` : ""}</span>
      <span class="pb-captured">${captured}${adv > 0 ? `<span class="adv">+${adv}</span>` : ""}</span>
      ${clock != null ? `<span class="pb-clock ${active ? "active" : ""} ${low ? "low" : ""}">${fmtClock(clock)}</span>` : ""}`;
  };
  const bottom = S.flipped ? "black" : "white", top = S.flipped ? "white" : "black";
  $("#bar-bottom").innerHTML = bar(bottom);
  $("#bar-top").innerHTML = bar(top);
}

// ----- side panel ------------------------------------------------------------
const plyLabel = (m) => `${m.number}${m.color === "white" ? "." : "…"} ${m.san}`;
const VERDICT = { Best: "best", Forced: "forced", Great: "a great move", Brilliant: "brilliant", Book: "book" };
const verdictText = (c) => VERDICT[c] || `${/^[AEIOU]/.test(c) ? "an" : "a"} ${c.toLowerCase()}`;

function renderCoach() {
  const d = S.data, r = d.review, el = $("#coach-card");
  if (S.ply === 0) return renderStory(el);
  const m = r.moves[S.ply - 1], pos = r.positions[S.ply];
  if (!m.classification) {
    el.innerHTML = `
      <div class="coach-head"><span class="spinner"></span>
        <div class="coach-title"><span class="san">${esc(plyLabel(m))}</span></div></div>
      <p class="coach-text muted">Waiting for Stockfish to reach this move…</p>`;
    return;
  }
  const key = `${S.gameId}:${S.ply}`;
  const ai = S.coachAi[key], eng = S.coach[key];
  const c = ai && ai !== "loading" && !ai.error ? ai : eng;
  const who = m.is_user ? "You" : esc(d.game.opponent);

  const head = `
    <div class="coach-head">
      ${badge(m.classification)}
      <div style="min-width:0">
        <div class="coach-title"><span class="san">${esc(plyLabel(m))}</span> is <span class="t-${m.classification}">${verdictText(m.classification)}</span></div>
        <div class="coach-meta">${who}${m.time_spent != null ? ` · ${m.time_spent}s` : ""}</div>
      </div>
      ${pos.eval ? `<span class="coach-eval ${pos.eval.wp >= 50 ? "w" : "b"}">${esc(pos.eval.text)}</span>` : ""}
    </div>`;

  let body;
  if (!r.complete || !c || c === "loading" || c.error) {
    // The quick rule-based comment, until the full coach note arrives.
    body = `<p class="coach-text">${esc(m.comment)}</p>${r.complete && c === "loading" ? `<p class="coach-sub muted"><span class="spinner"></span> Checking the position…</p>` : ""}`;
  } else {
    const n = c.note;
    body = `
      <p class="coach-headline">${esc(n.headline)}</p>
      <p class="coach-text">${esc(n.what_happened)}</p>
      ${n.what_was_missed ? `<p class="coach-text"><span class="k">Missed</span>${esc(n.what_was_missed)}</p>` : ""}
      ${n.better_idea ? `<p class="coach-text"><span class="k">Better</span>${esc(n.better_idea)}</p>` : ""}
      <p class="coach-lesson">${esc(n.lesson)}</p>
      ${c.lines.length ? `<div class="coach-lines">${c.lines.map((l, i) => `
        <button class="line-btn ${S.variation?.kind === l.kind ? "on" : ""}" data-line="${i}">
          <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>${esc(l.label)}</button>`).join("")}</div>` : ""}`;
  }

  let foot = "";
  if (r.complete && c && c !== "loading" && !c.error) {
    const src = c.source === "ai"
      ? `Written by AI · fact-checked${c.rejected ? ` (${c.rejected} draft${c.rejected > 1 ? "s" : ""} rejected)` : ""}`
      : "From engine analysis";
    const aiBtn = S.cfg?.ai && c.source !== "ai"
      ? (ai === "loading" ? `<button class="link-btn" disabled><span class="spinner"></span> Asking the AI…</button>`
        : `<button class="link-btn" id="ai-btn">Rewrite with AI</button>`) : "";
    foot = `<div class="coach-foot"><span>${src}</span>${aiBtn}</div>`;
  }
  el.innerHTML = head + variationBar() + body + foot;

  el.querySelectorAll(".line-btn").forEach((b) => (b.onclick = () => startLine(c.lines[+b.dataset.line])));
  const btn = $("#ai-btn");
  if (btn) btn.onclick = () => askAi(S.ply);
  bindVariationBar(el);
  if (r.complete && eng === undefined) loadCoach(S.ply);
}

let coachTimer;
function loadCoach(ply) {
  // Debounced so holding → doesn't fire a request per move.
  clearTimeout(coachTimer);
  coachTimer = setTimeout(async () => {
    const key = `${S.gameId}:${ply}`;
    if (S.coach[key] !== undefined) return;
    S.coach[key] = "loading";
    if (S.ply === ply) renderCoach();
    try {
      S.coach[key] = await api("/api/coach", { method: "POST", body: JSON.stringify({ game_id: S.gameId, ply }) });
    } catch (e) {
      S.coach[key] = { error: e.message };
    }
    if (S.ply === ply && !S.variation) { renderCoach(); renderArrows(boardView().move, boardView().pos); }
  }, 180);
}

async function askAi(ply) {
  const key = `${S.gameId}:${ply}`;
  S.coachAi[key] = "loading";
  renderCoach();
  try {
    const res = await api("/api/coach", { method: "POST", body: JSON.stringify({ game_id: S.gameId, ply, ai: true }) });
    if (res.ai_error) { toast(res.ai_error, true); delete S.coachAi[key]; }
    else S.coachAi[key] = res;
  } catch (e) {
    delete S.coachAi[key];
    toast(e.message, true);
  }
  if (S.ply === ply) renderCoach();
}

// ----- "Show me": play a line from the explanation on the board -------------
function startLine(line) {
  stopPlay();
  S.variation = { ...line, step: 0, prevStep: 0 };
  renderBoard();
  renderCoach();
  setTimeout(() => stepLine(1), 350);
}
function stepLine(delta) {
  const v = S.variation;
  if (!v) return;
  v.step = Math.max(0, Math.min(v.uci.length, v.step + delta));
  renderBoard();
  const bar = $("#line-bar");
  if (bar) bar.outerHTML = variationBar(), bindVariationBar($("#coach-card"));
}
function exitLine() {
  if (!S.variation) return;
  S.variation = null;
  S.prevPly = S.ply;
  renderBoard();
  renderCoach();
}
function variationBar() {
  const v = S.variation;
  if (!v) return "";
  const moves = v.san.map((san, i) => `<button class="lv ${i + 1 === v.step ? "cur" : ""}" data-step="${i + 1}">${esc(san)}</button>`).join("");
  return `<div class="line-bar" id="line-bar">
      <div class="line-bar-top"><b>${esc(v.label)}</b><span class="muted">${v.step}/${v.uci.length}</span>
        <button class="link-btn" id="line-exit">Back to game</button></div>
      <div class="line-moves">${moves}</div>
    </div>`;
}
function bindVariationBar(root) {
  const ex = root.querySelector("#line-exit");
  if (ex) ex.onclick = exitLine;
  root.querySelectorAll(".lv").forEach((b) => (b.onclick = () => { S.variation.step = +b.dataset.step - 1; stepLine(1); }));
}

// ----- the game story (shown at the start position) --------------------------
function renderStory(el) {
  const d = S.data, r = d.review, g = d.game, s = r.summary[g.user_color];
  const head = `<div class="coach-head"><div style="min-width:0">
      <div class="coach-title">${esc(g.opening || "Starting position")}</div>
      <div class="coach-meta">${Math.ceil(r.moves.length / 2)} moves${r.complete && s.accuracy != null ? ` · your accuracy ${s.accuracy}%` : ""}</div></div></div>`;
  if (!r.complete) {
    el.innerHTML = head + `<p class="coach-text muted">Stockfish is reviewing the game. You can step through the moves with ← → while it works.</p>`;
    return;
  }
  const st = S.story[S.gameId];
  if (st === undefined) { loadStory(false); }
  if (!st || st === "loading" || st.error) {
    el.innerHTML = head + `<p class="coach-text muted">${st?.error ? esc(st.error) : `<span class="spinner"></span> Finding the moments that decided the game…`}</p>`;
    return;
  }
  const pts = st.turning_points.map((p) => `
    <button class="tp" data-ply="${p.ply}">${badge(p.classification)}
      <span class="tp-main"><span class="san">${esc(p.numbered)}</span><span class="tp-who">${p.is_user ? "You" : esc(g.opponent)}</span></span>
      <span class="tp-text">${esc(p.key_point)}</span></button>`).join("");
  const aiState = S.story[S.gameId + ":ai"];
  const text = st.ai ? `<p class="coach-text">${esc(st.ai.summary)}</p><p class="coach-lesson">${esc(st.ai.lesson)}</p>`
    : `<p class="coach-text">${esc(st.turning_points.length ? st.summary.split(". ")[0] + "." : st.summary)}</p>`;
  const foot = st.ai ? `<div class="coach-foot"><span>Written by AI · fact-checked</span></div>`
    : S.cfg?.ai && st.turning_points.length ? `<div class="coach-foot"><span>From engine analysis</span>${aiState === "loading"
      ? `<button class="link-btn" disabled><span class="spinner"></span> Writing…</button>` : `<button class="link-btn" id="story-ai">Tell the story with AI</button>`}</div>` : "";
  el.innerHTML = head + text + (pts ? `<div class="tps">${pts}</div>` : "") + foot;
  el.querySelectorAll(".tp").forEach((b) => (b.onclick = () => goTo(+b.dataset.ply)));
  const sb = $("#story-ai");
  if (sb) sb.onclick = () => loadStory(true);
}

async function loadStory(ai) {
  const id = S.gameId, key = ai ? id + ":ai" : id;
  S.story[key] = "loading";
  if (S.ply === 0) renderCoach();
  try {
    const res = await api(`/api/story/${encodeURIComponent(id)}${ai ? "?ai=1" : ""}`);
    if (ai && !res.ai) toast(res.ai_error || "The AI couldn't write the story.", true);
    else S.story[id] = res;
    if (ai) delete S.story[key];
  } catch (e) {
    S.story[key] = ai ? undefined : { error: e.message };
    if (ai) toast(e.message, true);
  }
  if (S.gameId === id && S.ply === 0) renderCoach();
}

function renderMoves(scroll = true) {
  const moves = S.data.review.moves, list = $("#moves");
  let html = "";
  for (let i = 0; i < moves.length; i += 2) {
    html += `<li><span class="mv-num">${moves[i].number}.</span>`;
    for (const m of [moves[i], moves[i + 1]]) {
      if (!m) { html += "<span></span>"; continue; }
      const b = LIST_BADGES.has(m.classification) ? badge(m.classification) : `<span class="ph"></span>`;
      html += `<button class="mv ${m.ply === S.ply ? "cur" : ""}" data-ply="${m.ply}">${b}<span>${esc(m.san)}</span>${m.time_spent != null ? `<span class="t">${m.time_spent}s</span>` : ""}</button>`;
    }
    html += "</li>";
  }
  const res = S.data.review.headers.Result;
  if (res && res !== "*") html += `<li><span class="mv-result">${esc(res)}${S.data.game.termination ? ` · ${esc(S.data.game.termination)}` : ""}</span></li>`;
  const top = list.parentElement.scrollTop;
  list.innerHTML = html;
  list.parentElement.scrollTop = top;
  if (scroll) {
    const cur = list.querySelector(".mv.cur");
    if (cur) {
      const box = list.parentElement, cb = cur.getBoundingClientRect(), bb = box.getBoundingClientRect();
      if (cb.top < bb.top + 8 || cb.bottom > bb.bottom - 8) box.scrollTop += cb.top - bb.top - bb.height / 2 + cb.height / 2;
    }
  }
}

function renderGraph() {
  const svg = $("#graph"), r = S.data.review;
  const W = svg.clientWidth || 400, H = svg.clientHeight || 96;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const n = r.positions.length - 1 || 1;
  const x = (i) => (i / n) * W, y = (wp) => H - (wp / 100) * H;
  const pts = r.positions.map((p, i) => (p.eval ? [x(i), y(p.eval.wp)] : null)).filter(Boolean);
  let body = `<rect width="${W}" height="${H}" fill="#2b303a"/>`;
  if (pts.length) {
    const area = `M0,${H} ` + pts.map(([a, b]) => `L${a.toFixed(1)},${b.toFixed(1)}`).join(" ") + ` L${pts[pts.length - 1][0].toFixed(1)},${H} Z`;
    body += `<path d="${area}" fill="#e9ecf1"/>`;
  }
  body += `<line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="#7d8699" stroke-opacity=".5" stroke-dasharray="3 4"/>`;
  r.moves.forEach((m) => {
    if (["Blunder", "Mistake", "Brilliant", "Great"].includes(m.classification) && r.positions[m.ply].eval) {
      body += `<circle cx="${x(m.ply)}" cy="${y(r.positions[m.ply].eval.wp)}" r="3.6" fill="var(--c-${m.classification.toLowerCase()})" stroke="#111" stroke-width="1"/>`;
    }
  });
  body += `<line x1="${x(S.ply)}" x2="${x(S.ply)}" y1="0" y2="${H}" stroke="var(--accent)" stroke-width="2"/>`;
  svg.innerHTML = body;
}

function renderReport() {
  const r = S.data.review, g = S.data.game, s = r.summary, el = $("#report");
  if (!r.complete) {
    el.innerHTML = `<div class="empty" style="padding:30px 10px"><div class="spinner" style="margin:0 auto 12px;color:var(--accent)"></div>The game report is ready once Stockfish finishes (${r.analyzed}/${r.total}).</div>`;
    return;
  }
  const rows = CLS_ORDER.map((c) => {
    const w = s.white.counts[c] || 0, b = s.black.counts[c] || 0;
    return `<tr class="${w + b ? "" : "zero"}"><td>${w}</td><td><span class="lbl">${badge(c)}<span class="t-${c}">${c}</span></span></td><td>${b}</td></tr>`;
  }).join("");
  const ph = s.phases;
  const moments = s.key_moments.map((p) => r.moves[p - 1]).map((m) => `
    <button class="moment" data-ply="${m.ply}">${badge(m.classification)}
      <span class="san">${m.number}${m.color === "white" ? "." : "…"} ${esc(m.san)}</span>
      <span class="t-${m.classification}" style="font-size:12.5px;font-weight:600">${m.classification}</span>
      <span class="who">${m.is_user ? "You" : esc(g.opponent)}</span></button>`).join("");
  el.innerHTML = `
    <div class="acc-row">
      <div class="acc-box white"><div class="n">${s.white.accuracy ?? "—"}</div><div class="l">${esc(s.white.name)}${g.user_color === "white" ? " (you)" : ""}</div></div>
      <span class="acc-vs">ACCURACY</span>
      <div class="acc-box black"><div class="n">${s.black.accuracy ?? "—"}</div><div class="l">${esc(s.black.name)}${g.user_color === "black" ? " (you)" : ""}</div></div>
    </div>
    <table class="cls-table">${rows}</table>
    <div class="rep-h">Your accuracy by phase</div>
    <div class="phase-row">${["opening", "middlegame", "endgame"].map((k) => `<div class="phase"><div class="k">${k}</div><div class="v">${ph[k] ?? "—"}</div></div>`).join("")}</div>
    ${moments ? `<div class="rep-h">Key moments</div>${moments}` : ""}`;
}

function renderAll(scroll = true) {
  if (!S.data) return;
  S.ply = Math.max(0, Math.min(S.data.review.moves.length, S.ply));
  renderBoard();
  renderCoach();
  renderMoves(scroll);
  renderGraph();
  if (!$("#tab-report").hidden) renderReport();
  $$('.ctl[data-act="arrows"]').forEach((b) => b.classList.toggle("on", S.arrows));
}

function goTo(ply) {
  if (!S.data) return;
  if (S.variation) { S.variation = null; S.prevPly = -99; }
  S.ply = Math.max(0, Math.min(S.data.review.moves.length, ply));
  if (S.ply === S.prevPly) return;
  renderAll();
}

function stopPlay() {
  clearInterval(S.playTimer);
  S.playTimer = null;
  const b = $('.ctl[data-act="play"]');
  if (b) b.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
}
function togglePlay() {
  if (S.playTimer) return stopPlay();
  if (S.ply >= S.data.review.moves.length) goTo(0);
  $('.ctl[data-act="play"]').innerHTML = '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
  S.playTimer = setInterval(() => {
    if (S.ply >= S.data.review.moves.length) return stopPlay();
    goTo(S.ply + 1);
  }, 850);
}

function control(act) {
  if (!S.data) return;
  if (act !== "play") stopPlay();
  switch (act) {
    case "first": goTo(0); break;
    case "prev": goTo(S.ply - 1); break;
    case "next": goTo(S.ply + 1); break;
    case "last": goTo(S.data.review.moves.length); break;
    case "play": togglePlay(); break;
    case "flip": S.flipped = !S.flipped; buildSquares(); S.prevPly = S.ply; renderAll(false); break;
    case "arrows": S.arrows = !S.arrows; store.set("arrows", S.arrows); renderAll(false); break;
  }
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
function route() {
  const m = location.hash.match(/^#\/review\/(.+)$/);
  if (m) {
    $("#home-view").hidden = true;
    $("#review-view").hidden = false;
    const id = decodeURIComponent(m[1]);
    if (id !== S.gameId || !S.data) openReview(id);
    window.scrollTo(0, 0);
  } else {
    stopPlay();
    clearTimeout(S.pollTimer);
    S.gameId = null;
    $("#review-view").hidden = true;
    $("#home-view").hidden = false;
    if (!S.games.length && S.username) loadHome(S.username);
    else renderGames(), renderStats();
  }
}

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
function bind() {
  $("#user-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const u = $("#user-input").value.trim();
    if (!u) return;
    S.games = [];
    if (location.hash.startsWith("#/review")) location.hash = "#/";
    loadHome(u);
  });

  $("#games-list").addEventListener("click", (e) => {
    const row = e.target.closest(".game-row");
    if (row) location.hash = `#/review/${encodeURIComponent(row.dataset.id)}`;
  });

  for (const [id, key] of [["#tc-filter", "tc"], ["#res-filter", "res"]]) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      $$(`${id} button`).forEach((x) => x.classList.toggle("on", x === b));
      S.filters[key] = b.dataset.v;
      renderGames();
    });
  }
  $("#opp-search").addEventListener("input", (e) => { S.filters.q = e.target.value; renderGames(); });

  $(".controls").addEventListener("click", (e) => {
    const b = e.target.closest(".ctl");
    if (b) control(b.dataset.act);
  });
  $("#moves").addEventListener("click", (e) => {
    const b = e.target.closest(".mv");
    if (b) { stopPlay(); goTo(+b.dataset.ply); }
  });
  $("#report").addEventListener("click", (e) => {
    const b = e.target.closest(".moment");
    if (b) { switchTab("analysis"); goTo(+b.dataset.ply); }
  });
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  // graph scrubbing
  const graph = $("#graph"), tip = $("#graph-tip");
  const plyAt = (e) => {
    const rect = graph.getBoundingClientRect();
    return Math.round(((e.clientX - rect.left) / rect.width) * (S.data.review.positions.length - 1));
  };
  graph.addEventListener("mousemove", (e) => {
    if (!S.data) return;
    const p = Math.max(0, Math.min(S.data.review.moves.length, plyAt(e)));
    const m = S.data.review.moves[p - 1], ev = S.data.review.positions[p].eval;
    tip.hidden = false;
    tip.style.left = `${Math.max(40, Math.min(graph.clientWidth - 40, e.clientX - graph.getBoundingClientRect().left))}px`;
    tip.textContent = `${m ? `${m.number}${m.color === "white" ? "." : "…"} ${m.san}` : "Start"}${ev ? `  ${ev.text}` : ""}`;
  });
  graph.addEventListener("mouseleave", () => (tip.hidden = true));
  graph.addEventListener("click", (e) => { if (S.data) { stopPlay(); goTo(plyAt(e)); } });

  // wheel on board steps through moves
  $("#board").addEventListener("wheel", (e) => {
    if (!S.data) return;
    e.preventDefault();
    stopPlay();
    if (S.variation) return stepLine(e.deltaY > 0 ? 1 : -1);
    goTo(S.ply + (e.deltaY > 0 ? 1 : -1));
  }, { passive: false });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "m" || e.key === "M") { S.sound = !S.sound; store.set("sound", S.sound); applySound(); return; }
    if ($("#review-view").hidden) return;
    const map = { ArrowLeft: "prev", ArrowRight: "next", ArrowUp: "first", ArrowDown: "last", Home: "first", End: "last", " ": "play", f: "flip", F: "flip", e: "arrows", E: "arrows" };
    if (S.variation) {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); stepLine(e.key === "ArrowRight" ? 1 : -1); return; }
      if (e.key === "Escape") { exitLine(); return; }
    }
    if (e.key === "Escape") { location.hash = "#/"; return; }
    if (map[e.key]) { e.preventDefault(); control(map[e.key]); }
  });

  $("#theme-btn").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  $("#sound-btn").onclick = () => { S.sound = !S.sound; store.set("sound", S.sound); applySound(); };

  $("#board-themes").innerHTML = Object.entries(BOARD_THEMES).map(([k, [l, d]]) =>
    `<button class="theme-sw" data-v="${k}" title="${k}"><i style="background:${l}"></i><i style="background:${d}"></i><i style="background:${d}"></i><i style="background:${l}"></i></button>`).join("");
  $("#board-themes").addEventListener("click", (e) => { const b = e.target.closest(".theme-sw"); if (b) applyBoardTheme(b.dataset.v); });

  // PGN import
  const dlg = $("#import-dialog");
  $("#import-btn").onclick = () => { $("#import-error").hidden = true; dlg.showModal(); $("#pgn-input").focus(); };
  $("#import-form").addEventListener("submit", async (e) => {
    if (e.submitter?.value !== "go") return;
    e.preventDefault();
    const btn = $("#import-go");
    btn.disabled = true;
    try {
      const g = await api("/api/import", { method: "POST", body: JSON.stringify({ pgn: $("#pgn-input").value }) });
      dlg.close();
      location.hash = `#/review/${encodeURIComponent(g.id)}`;
    } catch (err) {
      $("#import-error").textContent = err.message;
      $("#import-error").hidden = false;
    } finally { btn.disabled = false; }
  });

  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => S.data && renderGraph(), 80); });
  window.addEventListener("hashchange", route);
}

function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  $("#tab-analysis").hidden = name !== "analysis";
  $("#tab-report").hidden = name !== "report";
  if (name === "report") renderReport();
  else if (S.data) renderGraph();
}

async function init() {
  applyTheme(store.get("theme", window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  applySound();
  bind();
  applyBoardTheme(S.boardTheme);
  try {
    S.cfg = await api("/api/config");
  } catch {
    S.cfg = { username: "", engine: false, ai: false };
  }
  const pill = $("#engine-pill");
  pill.classList.add(S.cfg.engine ? "ok" : "bad");
  pill.querySelector("span").textContent = S.cfg.engine ? `Stockfish · d${S.cfg.depth}` : "No engine";
  pill.title = S.cfg.engine ? S.cfg.engine_path : "Stockfish not found. Install it with: brew install stockfish";

  const params = new URLSearchParams(location.search);
  S.username = params.get("u") || store.get("username", "") || S.cfg.username || "";
  $("#user-input").value = S.username;
  if (location.hash.startsWith("#/review/")) {
    route();
  } else if (S.username) {
    loadHome(S.username);
  } else {
    $("#games-sub").textContent = "";
    $("#games-list").innerHTML = `<div class="card empty"><h3>Welcome!</h3><p>Enter your Chess.com username above to load your games.</p></div>`;
  }
}

init();
