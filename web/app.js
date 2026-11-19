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

// Standard chess annotation marks. Only moves worth a second look get one.
const NAG = { Brilliant: "!!", Great: "!", Inaccuracy: "?!", Mistake: "?", Blunder: "??" };
const CLS_ORDER = ["Brilliant", "Great", "Best", "Excellent", "Good", "Forced", "Inaccuracy", "Mistake", "Blunder"];
const CLS_HL = {
  Brilliant: "rgba(38,194,181,.5)", Great: "rgba(91,143,240,.5)", Inaccuracy: "rgba(243,200,75,.5)",
  Mistake: "rgba(242,145,61,.5)", Blunder: "rgba(240,84,92,.5)",
};
const nag = (cls) => NAG[cls] ? `<span class="nag t-${cls}" title="${cls}">${NAG[cls]}</span>` : "";


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

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $$("#theme-seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === theme));
  store.set("theme", theme);
  if (S.data) renderGraph();
}
function applySound() { $("#opt-sound").checked = S.sound; }

const BOARD_THEMES = { walnut: ["#ebd9b4", "#b08a62"], green: ["#eeeed2", "#769656"], slate: ["#dee3e6", "#8ca2ad"], blue: ["#e3ecf4", "#6a8fb3"], grey: ["#d9d9d9", "#9a9a9a"] };
function applyBoardTheme(name) {
  if (!BOARD_THEMES[name]) name = "walnut";
  S.boardTheme = name;
  document.documentElement.dataset.board = name;
  store.set("boardTheme", name);
  $$(".swatch").forEach((b) => b.classList.toggle("on", b.dataset.v === name));
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
  $("#games-list").innerHTML = Array.from({ length: 8 }, () => `<div class="skeleton"></div>`).join("");
  $("#profile").innerHTML = "";

  const profileP = api(`/api/profile?username=${encodeURIComponent(username)}`).catch(() => null);
  try {
    const data = await api(`/api/games?username=${encodeURIComponent(username)}`);
    S.games = data.games;
    renderGames();
  } catch (e) {
    S.games = [];
    $("#games-sub").textContent = "";
    $("#games-list").innerHTML = `<div class="empty"><h3>Couldn't load games</h3><p>${esc(e.message)}</p></div>`;
  }
  S.profile = await profileP;
  renderProfile();
}

function renderProfile() {
  const p = S.profile, g = S.games, el = $("#profile");
  if (!p && !g.length) { el.innerHTML = ""; return; }
  const labels = { bullet: "Bullet", blitz: "Blitz", rapid: "Rapid", daily: "Daily", puzzles: "Puzzles" };
  const name = p?.username || S.username;
  const who = `
    <div class="who">
      ${p?.avatar ? `<img class="avatar" src="${esc(p.avatar)}" alt="" />` : `<div class="avatar">${esc((name || "?")[0].toUpperCase())}</div>`}
      <div>
        <div class="who-name">${p?.title ? `<span class="title-tag">${esc(p.title)}</span>` : ""}${esc(name)}</div>
        <a class="who-link" href="https://www.chess.com/member/${encodeURIComponent(name)}" target="_blank" rel="noopener">chess.com profile ↗</a>
      </div>
    </div>`;
  const ratings = Object.entries(p?.ratings || {}).filter(([, r]) => r.rating).map(([k, r]) => `
    <div class="rating"><span class="rating-k">${labels[k] || k}</span><span class="rating-v">${r.rating}</span></div>`).join("");

  let record = "";
  if (g.length) {
    const w = g.filter((x) => x.result === "Win").length, l = g.filter((x) => x.result === "Loss").length, d = g.length - w - l;
    const pct = (n) => (100 * n / g.length).toFixed(2);
    record = `
      <div class="record">
        <div class="record-top"><span>Last ${g.length} games</span><span><b>${w}</b> won · <b>${d}</b> drawn · <b>${l}</b> lost</span></div>
        <div class="wld"><span style="width:${pct(w)}%;background:var(--win)"></span><span style="width:${pct(d)}%;background:var(--draw)"></span><span style="width:${pct(l)}%;background:var(--loss)"></span></div>
      </div>`;
  }
  el.innerHTML = who + (ratings ? `<div class="ratings">${ratings}</div>` : "") + record;
}


function renderGames() {
  const { tc, res, q } = S.filters;
  const needle = q.trim().toLowerCase();
  const list = S.games.filter((g) =>
    (tc === "all" || g.time_class === tc) &&
    (res === "all" || g.result === res) &&
    (!needle || g.opponent.toLowerCase().includes(needle) || (g.opening || "").toLowerCase().includes(needle)));

  $("#games-sub").textContent = S.games.length
    ? (list.length === S.games.length ? `${list.length} games` : `${list.length} of ${S.games.length} games`)
    : `No games found for ${S.username}`;

  if (!list.length) {
    $("#games-list").innerHTML = `<div class="empty"><h3>No games match</h3><p>Try a different filter${S.games.length ? "" : " or username"}.</p></div>`;
    return;
  }
  const word = { Win: "Won", Loss: "Lost", Draw: "Draw" };
  $("#games-list").innerHTML = list.map((g) => `
    <button class="game-row" data-id="${esc(g.id)}">
      <span class="res res-${g.result}">${word[g.result] || g.result}</span>
      <span class="g-opp"><span class="side-dot ${g.user_color}" title="You played ${g.user_color}"></span>${esc(g.opponent)}<span class="r">${g.opponent_rating ?? ""}</span></span>
      <span class="g-open" title="${esc(g.opening)}">${esc(g.opening || "")}</span>
      <span class="g-tc">${esc(fmtTC(g.time_control) || g.time_class)}</span>
      <span class="g-acc ${g.accuracy == null ? "none" : ""}" title="Your accuracy">${g.accuracy != null ? g.accuracy.toFixed(1) : "–"}</span>
      <span class="g-date">${relDate(g.end_time)}</span>
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
  $("#progress-text").textContent = d.status === "queued" ? "Starting Stockfish…" : "Stockfish is analyzing the game";
  $("#progress-pct").textContent = `${d.progress} / ${d.total}`;
  $("#progress-fill").style.width = pct + "%";
}

function renderGameHead() {
  const g = S.data.game;
  const date = g.end_time ? new Date(g.end_time * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  const word = { Win: "Won", Loss: "Lost", Draw: "Draw" };
  $("#game-head").innerHTML = `
    <a href="#/" class="back-link">← Games</a>
    <div class="gh-title"><span>vs ${esc(g.opponent)}</span><span class="res res-${g.result}">${word[g.result] || g.result}</span></div>
    <div class="gh-sub">${[g.time_class !== "imported" ? `${fmtTC(g.time_control)} ${g.time_class}` : "Imported game", date].filter(Boolean).map(esc).join(" · ")}</div>
    <div class="gh-links">
      ${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">Open on Chess.com</a>` : ""}
      <button id="copy-pgn">Copy PGN</button>
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
  if (NAG[move?.classification]) {
    const [x, y] = sqToXY(move.to);
    html += `<div class="badge-sq" style="left:${x * 12.5}%;top:${y * 12.5}%"><span class="nag-dot bg-${move.classification}">${NAG[move.classification]}</span></div>`;
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
  const fill = $("#evalbar-fill"), bar = $("#evalbar");
  fill.style.height = (ev ? ev.wp : 50) + "%";
  bar.title = ev ? `Evaluation ${ev.text}` : "";
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
  const sideToMove = boardView().pos.fen.split(" ")[1];
  const over = S.ply === moves.length && !S.variation;

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
    const isUser = g.user_color === color && g.time_class !== "imported";
    return `
      <span class="pb-name">${esc(info.name)}</span>
      ${info.rating ? `<span class="pb-rating">${info.rating}</span>` : ""}
      ${isUser ? `<span class="pb-you">you</span>` : ""}
      <span class="pb-captured">${captured}${adv > 0 ? `<span class="adv">+${adv}</span>` : ""}</span>
      ${clock != null ? `<span class="pb-clock ${active ? "active" : ""} ${low ? "low" : ""}">${fmtClock(clock)}</span>` : ""}`;
  };
  const bottom = S.flipped ? "black" : "white", top = S.flipped ? "white" : "black";
  $("#bar-bottom").innerHTML = bar(bottom);
  $("#bar-top").innerHTML = bar(top);
}

// ----- side panel ------------------------------------------------------------
const plyLabel = (m) => `${m.number}${m.color === "white" ? "." : "…"} ${m.san}`;

function renderCoach() {
  const d = S.data, r = d.review, el = $("#coach-card");
  if (S.ply === 0) return renderStory(el);
  const m = r.moves[S.ply - 1], pos = r.positions[S.ply];
  if (!m.classification) {
    el.innerHTML = `
      <div class="coach-head"><span class="coach-move">${esc(plyLabel(m))}</span></div>
      <p class="coach-text muted"><span class="spinner"></span> Waiting for Stockfish to reach this move…</p>`;
    return;
  }
  const key = `${S.gameId}:${S.ply}`;
  const ai = S.coachAi[key], eng = S.coach[key];
  const c = ai && ai !== "loading" && !ai.error ? ai : eng;
  const who = m.is_user ? "You" : esc(d.game.opponent);

  const head = `
    <div class="coach-head">
      <span class="coach-move">${esc(plyLabel(m))}${nag(m.classification)}</span>
      <span class="coach-verdict t-${m.classification}">${esc(m.classification)}</span>
      ${pos.eval ? `<span class="coach-eval" title="Engine evaluation">${esc(pos.eval.text)}</span>` : ""}
    </div>
    <div class="coach-meta">${who}${m.time_spent != null ? ` · ${m.time_spent}s on the clock` : ""}</div>`;

  let body;
  if (!r.complete || !c || c === "loading" || c.error) {
    // The quick rule-based comment, until the full coach note arrives.
    body = `<p class="coach-text">${esc(m.comment)}</p>${r.complete && c === "loading" ? `<p class="coach-text muted small"><span class="spinner"></span> Checking the position…</p>` : ""}`;
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
  const d = S.data, r = d.review, g = d.game, sum = r.summary;
  const you = g.user_color, them = you === "white" ? "black" : "white";
  const head = `
    <div class="coach-head"><span class="coach-move title">${esc(g.opening || "Game summary")}</span></div>
    <div class="coach-meta">${Math.ceil(r.moves.length / 2)} moves</div>`;
  if (!r.complete) {
    el.innerHTML = head + `<p class="coach-text muted">Stockfish is reviewing the game. Step through the moves with ← → while it works; verdicts appear as each move is analyzed.</p>`;
    return;
  }
  const acc = `
    <div class="acc-pair">
      <div><b>${sum[you].accuracy ?? "–"}</b><span>Your accuracy</span></div>
      <div><b>${sum[them].accuracy ?? "–"}</b><span>${esc(sum[them].name || "Opponent")}</span></div>
    </div>`;

  const st = S.story[S.gameId];
  if (st === undefined) loadStory(false);
  let story;
  if (!st || st === "loading" || st.error) {
    story = `<p class="coach-text muted">${st?.error ? esc(st.error) : `<span class="spinner"></span> Finding the moments that decided the game…`}</p>`;
  } else {
    const pts = st.turning_points.map((p) => `
      <button class="tp" data-ply="${p.ply}">
        <div class="tp-top"><span class="tp-move">${esc(p.numbered)}${nag(p.classification)}</span><span class="tp-who">${p.is_user ? "You" : esc(g.opponent)}</span></div>
        <div class="tp-text">${esc(p.key_point)}</div>
      </button>`).join("");
    const text = st.ai ? `<p class="coach-text">${esc(st.ai.summary)}</p><p class="coach-lesson">${esc(st.ai.lesson)}</p>`
      : `<p class="coach-text">${esc(st.turning_points.length ? st.summary.split(". ")[0] + "." : st.summary)}</p>`;
    const aiState = S.story[S.gameId + ":ai"];
    const foot = st.ai ? `<div class="coach-foot"><span>Written by AI · fact-checked</span></div>`
      : S.cfg?.ai && st.turning_points.length ? `<div class="coach-foot"><span>From engine analysis</span>${aiState === "loading"
        ? `<button class="link-btn" disabled><span class="spinner"></span> Writing…</button>` : `<button class="link-btn" id="story-ai">Tell the story with AI</button>`}</div>` : "";
    story = text + (pts ? `<div class="tps">${pts}</div>` : "") + foot;
  }

  const rows = CLS_ORDER.map((c) => {
    const a = sum[you].counts[c] || 0, b = sum[them].counts[c] || 0;
    return `<tr class="${a + b ? "" : "zero"}"><td class="t-${c}">${c}</td><td>${a}</td><td>${b}</td></tr>`;
  }).join("");
  const ph = sum.phases;
  const details = `
    <details class="details">
      <summary>Move breakdown</summary>
      <table class="counts"><tr><th></th><th>You</th><th>${esc(sum[them].name || "Opponent")}</th></tr>${rows}</table>
      <div class="phases">${["opening", "middlegame", "endgame"].map((k) => `<span>${k[0].toUpperCase() + k.slice(1)} <b>${ph[k] ?? "–"}</b></span>`).join("")}</div>
    </details>`;

  el.innerHTML = head + acc + story + details;
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
      html += `<button class="mv ${m.ply === S.ply ? "cur" : ""}" data-ply="${m.ply}"><span>${esc(m.san)}</span>${nag(m.classification)}${m.time_spent != null ? `<span class="t">${m.time_spent}s</span>` : ""}</button>`;
    }
    html += "</li>";
  }
  const res = S.data.review.headers.Result;
  if (res && res !== "*") html += `<li><span class="mv-result">${esc(res)}${S.data.game.termination ? ` · ${esc(S.data.game.termination)}` : ""}</span></li>`;
  const box = list.parentElement, top = box.scrollTop;
  list.innerHTML = html;
  box.scrollTop = top;
  if (scroll) {
    const cur = list.querySelector(".mv.cur");
    if (cur) {
      const cb = cur.getBoundingClientRect(), bb = box.getBoundingClientRect();
      if (cb.top < bb.top + 8 || cb.bottom > bb.bottom - 8) box.scrollTop += cb.top - bb.top - bb.height / 2 + cb.height / 2;
    }
  }
}

function renderGraph() {
  const svg = $("#graph"), r = S.data.review;
  const W = svg.clientWidth || 400, H = svg.clientHeight || 56;
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const n = r.positions.length - 1 || 1;
  const x = (i) => (i / n) * W, y = (wp) => H - (wp / 100) * H;
  const pts = r.positions.map((p, i) => (p.eval ? [x(i), y(p.eval.wp)] : null)).filter(Boolean);
  let body = `<rect width="${W}" height="${H}" fill="${v("--graph-bg")}"/>`;
  if (pts.length) {
    const area = `M0,${H} ` + pts.map(([a, b]) => `L${a.toFixed(1)},${b.toFixed(1)}`).join(" ") + ` L${pts[pts.length - 1][0].toFixed(1)},${H} Z`;
    body += `<path d="${area}" fill="${v("--graph-fg")}"/>`;
  }
  body += `<line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="${v("--muted")}" stroke-opacity=".45" stroke-dasharray="2 4"/>`;
  r.moves.forEach((m) => {
    if (["Blunder", "Mistake", "Brilliant"].includes(m.classification) && r.positions[m.ply].eval) {
      body += `<circle cx="${x(m.ply)}" cy="${y(r.positions[m.ply].eval.wp)}" r="3" fill="${v(`--c-${m.classification.toLowerCase()}`)}"/>`;
    }
  });
  body += `<line x1="${x(S.ply)}" x2="${x(S.ply)}" y1="0" y2="${H}" stroke="${v("--focus")}" stroke-width="1.5"/>`;
  svg.innerHTML = body;
}


function renderAll(scroll = true) {
  if (!S.data) return;
  S.ply = Math.max(0, Math.min(S.data.review.moves.length, S.ply));
  renderBoard();
  renderCoach();
  renderMoves(scroll);
  renderGraph();
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
  if (b) b.innerHTML = '<svg viewBox="0 0 24 24" class="fill"><path d="M8 5v14l11-7z"/></svg>';
}
function togglePlay() {
  if (S.playTimer) return stopPlay();
  if (S.ply >= S.data.review.moves.length) goTo(0);
  $('.ctl[data-act="play"]').innerHTML = '<svg viewBox="0 0 24 24" class="fill"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
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
    case "arrows": S.arrows = !S.arrows; store.set("arrows", S.arrows); $("#opt-arrows").checked = S.arrows; renderAll(false); break;
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
    else renderGames(), renderProfile();
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

  // settings menu
  $("#theme-seg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) applyTheme(b.dataset.v); });
  $("#opt-sound").addEventListener("change", (e) => { S.sound = e.target.checked; store.set("sound", S.sound); });
  $("#opt-arrows").addEventListener("change", (e) => { S.arrows = e.target.checked; store.set("arrows", S.arrows); if (S.data) renderBoard(); });
  $("#board-themes").innerHTML = Object.entries(BOARD_THEMES).map(([k, [l, d]]) =>
    `<button class="swatch" data-v="${k}" title="${k}" aria-label="${k} board"><i style="background:${l}"></i><i style="background:${d}"></i><i style="background:${d}"></i><i style="background:${l}"></i></button>`).join("");
  $("#board-themes").addEventListener("click", (e) => { const b = e.target.closest(".swatch"); if (b) applyBoardTheme(b.dataset.v); });
  document.addEventListener("click", (e) => { const m = $("#settings"); if (m.open && !m.contains(e.target)) m.open = false; });

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
  $("#engine-banner").hidden = !!S.cfg.engine;
  $("#engine-note").textContent = S.cfg.engine
    ? `Stockfish, depth ${S.cfg.depth}` + (S.cfg.ai ? " · AI coach on" : " · AI coach off (no API key)")
    : "Stockfish not found";
  $("#opt-arrows").checked = S.arrows;

  const params = new URLSearchParams(location.search);
  S.username = params.get("u") || store.get("username", "") || S.cfg.username || "";
  $("#user-input").value = S.username;
  if (location.hash.startsWith("#/review/")) {
    route();
  } else if (S.username) {
    loadHome(S.username);
  } else {
    $("#games-sub").textContent = "";
    $("#games-list").innerHTML = `<div class="empty"><h3>Welcome</h3><p>Enter your Chess.com username above to load your games.</p></div>`;
  }
}

init();
