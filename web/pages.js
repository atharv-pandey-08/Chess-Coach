/* Insights and Puzzles pages. Shares helpers and state (S, $, api, esc...) with app.js. */
"use strict";

const TACTIC_NAME = {
  fork: "forks", pin: "pins", skewer: "skewers", discovered_attack: "discovered attacks",
  double_check: "double checks", trapped_piece: "trapped pieces", back_rank_mate: "back-rank mates",
  smothered_mate: "smothered mates", mate_threat: "mate threats", wins_material: "winning captures",
  checkmate: "checkmates",
};
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// ===========================================================================
// Insights
// ===========================================================================
let batchTimer = null;
let lastBatchDone = -1;

async function showInsights() {
  const el = $("#insights");
  if (!S.username) {
    el.innerHTML = `<div class="empty"><h3>No player yet</h3><p>Enter your Chess.com username above first.</p></div>`;
    return;
  }
  if (!el.dataset.user || el.dataset.user !== S.username) el.innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  el.dataset.user = S.username;
  let p;
  try {
    p = await api(`/api/insights?username=${encodeURIComponent(S.username)}`);
  } catch (e) {
    if (/Load your games/.test(e.message) && !S.games.length) {
      // Insights need the game list; fetch it once, then try again.
      try { await api(`/api/games?username=${encodeURIComponent(S.username)}`); return showInsights(); } catch { /* fall through */ }
    }
    el.innerHTML = `<div class="empty"><h3>Couldn't build insights</h3><p>${esc(e.message)}</p></div>`;
    return;
  }
  if (!location.hash.startsWith("#/insights")) return;
  renderBatch(p.batch, p);
  renderPuzzleCount(p.puzzles);
  $("#insights-sub").textContent = `Based on ${plural(p.analysed, "analysed game")} out of ${p.games}.`;
  el.innerHTML = p.analysed < 3 ? insightsEmpty(p) : insightsBody(p);
  if (p.analysed >= 3) drawTrend(p.trend);
}

function renderBatch(b, p) {
  const el = $("#batch");
  clearTimeout(batchTimer);
  if (b?.running && b.username === S.username.toLowerCase()) {
    el.innerHTML = `<div class="batch-run"><span class="spinner"></span> Analyzing game ${Math.min(b.done + 1, b.total)} of ${b.total}</div>
      <div class="progress-track"><div class="progress-fill" style="width:${b.total ? 100 * b.done / b.total : 0}%"></div></div>`;
    batchTimer = setTimeout(pollBatch, 3000);
  } else {
    const left = p ? p.games - p.analysed : 0;
    el.innerHTML = left > 0 ? `<button class="btn btn-sm" id="batch-go">Analyze ${Math.min(20, left)} more games</button>
      <div class="muted small" style="margin-top:4px">${left} not analysed yet · ${S.cfg?.public ? "a minute or more each on the free demo server" : "about 5–10 s each"}</div>` : "";
    const go = $("#batch-go");
    if (go) go.onclick = startBatch;
  }
}

async function startBatch() {
  try {
    const b = await api("/api/batch", { method: "POST", body: JSON.stringify({ username: S.username, count: 20 }) });
    renderBatch(b);
  } catch (e) { toast(e.message, true); }
}

async function pollBatch() {
  if (!location.hash.startsWith("#/insights")) return;
  let b;
  try { b = await api("/api/batch"); } catch { return; }
  // Refresh the numbers every few finished games, and once at the end.
  if (b.done !== lastBatchDone && (b.done % 4 === 0 || !b.running)) { lastBatchDone = b.done; return showInsights(); }
  renderBatch(b);
}

function insightsEmpty(p) {
  return `<div class="empty"><h3>Analyze a few more games first</h3>
    <p>Patterns need data: at least 3 analysed games, ideally 20 or more. ${p.analysed ? `You have ${p.analysed}.` : ""}<br/>
    Use “Analyze more games” above; it runs in the background while you do other things.</p></div>`;
}

function bar(value, max, cls = "") {
  const w = value == null || !max ? 0 : Math.max(2, (100 * value) / max);
  return `<span class="bar ${cls}"><i style="width:${w}%"></i></span>`;
}

function insightsBody(p) {
  const heads = p.headlines.length ? `<ul class="headlines">${p.headlines.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : "";

  const overview = `
    <div class="kpis">
      <div><b>${p.accuracy ?? "–"}</b><span>average accuracy</span></div>
      <div><b>${p.blunders_per_game ?? "–"}</b><span>blunders per game</span></div>
      <div><b>${p.score != null ? Math.round(p.score) + "%" : "–"}</b><span>score (${p.wins}W ${p.draws}D ${p.losses}L)</span></div>
    </div>`;

  const maxCat = Math.max(...p.categories.map((c) => c.count), 1);
  const cats = p.categories.map((c, i) => `
    <details class="cat" ${i === 0 ? "open" : ""}>
      <summary>
        <span class="cat-label">${esc(c.label)}</span>
        ${bar(c.count, maxCat, i === 0 ? "strong" : "")}
        <span class="cat-n">${c.count} <span class="muted">· ${Math.round(c.share)}%</span></span>
      </summary>
      <div class="cat-body">
        <p class="cat-advice">${esc(c.advice)}</p>
        ${c.tactics.length ? `<p class="muted small">Mostly ${c.tactics.map((t) => `${TACTIC_NAME[t.kind] || t.kind} (${t.count})`).join(", ")}.</p>` : ""}
        ${c.examples.map((x) => `
          <a class="example" href="#/review/${encodeURIComponent(x.game_id)}/${x.ply}">
            <span class="ex-move">${esc(x.numbered)}${nag(x.cls)}</span>
            <span class="ex-text">${esc(x.key_point)}</span>
            <span class="ex-opp">vs ${esc(x.opponent)}</span>
          </a>`).join("")}
      </div>
    </details>`).join("");

  const tableRows = (rows, labelKey, rateLabel) => rows.map((r) => `
    <tr><td>${esc(r[labelKey])}</td><td class="num">${r.moves}</td>
      <td class="num">${r.accuracy ?? "–"}</td>
      <td class="bar-cell">${bar(r.blunder_rate, 30, "warn")}<span class="num">${r.blunder_rate != null ? r.blunder_rate + "%" : "–"}</span></td></tr>`).join("");
  const time = `
    <div class="two">
      <div>
        <h3>Clock left when you moved</h3>
        <table class="tbl"><tr><th></th><th class="num">Moves</th><th class="num">Accuracy</th><th>Blunder rate</th></tr>
        ${tableRows(p.time, "label")}</table>
      </div>
      <div>
        <h3>Time you took (after the opening)</h3>
        <table class="tbl"><tr><th></th><th class="num">Moves</th><th class="num">Accuracy</th><th>Mistake rate</th></tr>
        ${tableRows(p.speed, "label")}</table>
      </div>
    </div>`;

  const phases = `
    <div class="two">
      <div>
        <h3>By phase</h3>
        <table class="tbl"><tr><th></th><th class="num">Accuracy</th><th class="num">Mistakes</th></tr>
        ${p.phases.map((x) => `<tr><td>${x.phase[0].toUpperCase() + x.phase.slice(1)}</td><td class="num">${x.accuracy ?? "–"}</td><td class="num">${x.mistakes}</td></tr>`).join("")}</table>
      </div>
      <div>
        <h3>By colour</h3>
        <table class="tbl"><tr><th></th><th class="num">Games</th><th class="num">Score</th><th class="num">Accuracy</th></tr>
        ${p.colors.map((x) => `<tr><td>${x.color === "white" ? "White" : "Black"}</td><td class="num">${x.games}</td><td class="num">${x.score != null ? Math.round(x.score) + "%" : "–"}</td><td class="num">${x.accuracy ?? "–"}</td></tr>`).join("")}</table>
      </div>
    </div>`;

  const openings = p.openings.length ? `
    <table class="tbl openings">
      <tr><th>Opening</th><th>As</th><th class="num">Games</th><th>Score</th><th class="num">Accuracy</th><th class="num" title="Average move of your first mistake">First mistake</th></tr>
      ${p.openings.map((o) => `
        <tr><td>${esc(o.family)}</td><td><span class="side-dot ${o.color}"></span>${o.color === "white" ? "White" : "Black"}</td>
          <td class="num">${o.games}</td>
          <td class="bar-cell">${bar(o.score, 100, o.score < 40 ? "bad" : o.score >= 60 ? "good" : "")}<span class="num">${Math.round(o.score)}%</span></td>
          <td class="num">${o.accuracy ?? "–"}</td><td class="num">${o.first_mistake_move ? "move " + o.first_mistake_move : "–"}</td></tr>`).join("")}
    </table>` : `<p class="muted">Not enough repeated openings yet.</p>`;

  return `
    ${heads}
    ${overview}
    <section class="sec"><h2>What your mistakes are</h2>
      <p class="sec-sub">${plural(p.mistakes, "mistake")} and blunders from ${plural(p.analysed, "game")}, sorted by how often they happen. Open one to see real examples.</p>
      ${cats || `<p class="muted">No mistakes found. Impressive.</p>`}</section>
    <section class="sec"><h2>Clock and speed</h2>${time}</section>
    <section class="sec"><h2>Phase and colour</h2>${phases}</section>
    <section class="sec"><h2>Openings</h2><p class="sec-sub">Results from all ${p.games} games; accuracy and first mistake from analysed ones.</p>${openings}</section>
    <section class="sec"><h2>Accuracy over time</h2>
      <div class="trend-wrap"><svg id="trend" class="trend" preserveAspectRatio="none"></svg><div class="graph-tip" id="trend-tip" hidden></div></div>
      <p class="sec-sub"><span class="dot-k win"></span>won <span class="dot-k loss"></span>lost <span class="dot-k draw"></span>drawn · the line is a 5-game average. Click a point to open the game.</p></section>`;
}

function drawTrend(trend) {
  const svg = $("#trend");
  if (!svg || !trend.length) return;
  const W = svg.clientWidth || 800, H = svg.clientHeight || 160, pad = 10;
  const css = getComputedStyle(document.documentElement), v = (n) => css.getPropertyValue(n).trim();
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const accs = trend.map((t) => t.accuracy ?? 0);
  const lo = Math.max(0, Math.min(...accs) - 5), hi = Math.min(100, Math.max(...accs) + 5);
  const x = (i) => trend.length === 1 ? W / 2 : pad + (i * (W - 2 * pad)) / (trend.length - 1);
  const y = (a) => H - pad - ((a - lo) / (hi - lo || 1)) * (H - 2 * pad);
  const avg = accs.map((_, i) => { const w = accs.slice(Math.max(0, i - 4), i + 1); return w.reduce((a, b) => a + b, 0) / w.length; });
  let body = "";
  for (const g of [lo, (lo + hi) / 2, hi]) {
    body += `<line x1="0" x2="${W}" y1="${y(g)}" y2="${y(g)}" stroke="${v("--line")}"/>`;
    body += `<text x="2" y="${y(g) - 3}" font-size="10" fill="${v("--muted")}">${Math.round(g)}</text>`;
  }
  body += `<path d="${avg.map((a, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(a).toFixed(1)}`).join(" ")}" fill="none" stroke="${v("--text-2")}" stroke-width="1.5"/>`;
  const col = { Win: v("--win"), Loss: v("--loss"), Draw: v("--draw") };
  trend.forEach((t, i) => {
    body += `<circle cx="${x(i)}" cy="${y(accs[i])}" r="3.5" fill="${col[t.result] || v("--muted")}" data-i="${i}" class="pt"/>`;
  });
  svg.innerHTML = body;
  const tip = $("#trend-tip");
  svg.onmousemove = (e) => {
    const c = e.target.closest(".pt");
    if (!c) { tip.hidden = true; return; }
    const t = trend[+c.dataset.i];
    tip.hidden = false;
    tip.style.left = `${c.getAttribute("cx")}px`;
    tip.textContent = `${t.accuracy}% vs ${t.opponent} · ${t.blunders} blunder${t.blunders === 1 ? "" : "s"}`;
  };
  svg.onmouseleave = () => (tip.hidden = true);
  svg.onclick = (e) => { const c = e.target.closest(".pt"); if (c) location.hash = `#/review/${encodeURIComponent(trend[+c.dataset.i].id)}`; };
}

function renderPuzzleCount(stats) {
  const el = $("#puzzle-count");
  if (!stats) return;
  el.hidden = !stats.due;
  el.textContent = stats.due;
}

// ===========================================================================
// Puzzles: positions where you went wrong, brought back on a spaced schedule
// ===========================================================================
const PZ = { puzzle: null, selected: null, result: null, step: 0, busy: false, stats: null };

async function showPuzzles(after) {
  const side = $("#pz-side");
  if (!S.username) {
    side.innerHTML = `<div class="empty"><h3>No player yet</h3><p>Enter your Chess.com username above first.</p></div>`;
    return;
  }
  side.innerHTML = `<p class="muted"><span class="spinner"></span> Loading your next puzzle…</p>`;
  let res;
  try {
    res = await api(`/api/puzzles/next?username=${encodeURIComponent(S.username)}${after ? `&after=${encodeURIComponent(after)}` : ""}`);
  } catch (e) { side.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  Object.assign(PZ, { puzzle: res.puzzle, selected: null, result: null, step: 0, busy: false, stats: res.stats });
  renderPuzzleCount(res.stats);
  renderPuzzle();
}

function pzFen() {
  const r = PZ.result;
  if (!r) return PZ.puzzle.fen;
  if (PZ.step === 0) return r.correct ? r.your_fen : PZ.puzzle.fen;
  return r.line_fens[PZ.step - 1];
}

function renderPuzzle() {
  const p = PZ.puzzle, side = $("#pz-side"), st = PZ.stats;
  const statLine = `<div class="pz-stats">${st.due} due · ${st.total} total · ${st.learned} learned</div>`;
  if (!p) {
    drawBoard($("#pz-board"), "8/8/8/8/8/8/8/8 w - - 0 1", {});
    side.innerHTML = `<h1>Puzzles</h1>${statLine}
      <div class="empty" style="padding:30px 0;text-align:left"><h3>${st.total ? "All caught up" : "No puzzles yet"}</h3>
      <p>${st.total ? "Nothing is due right now. Puzzles you've solved come back in a few days, the way flashcards do."
        : "Puzzles are made from mistakes in your analysed games. Analyze some games on the Insights page."}</p></div>`;
    return;
  }
  const color = p.color === "white" ? "White" : "Black";
  const r = PZ.result;
  const flipped = p.color === "black";
  const lastUci = r ? (PZ.step === 0 ? null : r.line_uci[PZ.step - 1]) : null;
  drawBoard($("#pz-board"), pzFen(), {
    flipped, selected: PZ.selected,
    highlight: lastUci ? [lastUci.slice(0, 2), lastUci.slice(2, 4)] : [],
    interactive: !r && !PZ.busy,
  });

  let body;
  if (!r) {
    body = `
      <p class="pz-prompt">${color} to move. Find the best move.</p>
      <p class="muted">From your game against ${esc(p.opponent)}${p.end_time ? `, ${relDate(p.end_time).toLowerCase()}` : ""}. You played <b>${esc(p.played_san)}</b> here${p.reps ? "" : " and it cost you"}.</p>
      ${PZ.busy ? `<p class="muted"><span class="spinner"></span> Checking…</p>` : `<p class="muted small">Click a piece, then the square to move it to.</p>`}
      <div class="pz-actions"><button class="link-btn" id="pz-skip">Show the answer</button></div>`;
  } else {
    const verdict = r.correct
      ? `<p class="pz-verdict good">${r.exact ? "Correct" : "Good enough"}: ${esc(r.your_move)}${r.exact ? "" : `. The engine's move was ${esc(r.solution_san[0])}.`}</p>`
      : `<p class="pz-verdict bad">${r.your_move ? `${esc(r.your_move)} isn't it.` : "Here's the answer."} The move was ${esc(r.solution_san[0])}.</p>`;
    const line = r.line_san.map((san, i) => `<button class="lv ${i + 1 === PZ.step ? "cur" : ""}" data-step="${i + 1}">${esc(san)}</button>`).join("");
    body = `${verdict}
      <p class="coach-text"><span class="k">In the game</span>${esc(r.played_san)}: ${esc(r.key_point)}</p>
      <div class="line-bar"><div class="line-bar-top"><b>The line</b><span class="muted">${PZ.step}/${r.line_san.length}</span></div>
        <div class="line-moves">${line}</div></div>
      <p class="muted small">${r.correct ? `This comes back in ${r.next_in_days} day${r.next_in_days === 1 ? "" : "s"}.` : "This comes back in 10 minutes."}</p>
      <div class="pz-actions">
        <button class="btn" id="pz-next">Next puzzle</button>
        <a class="link-btn" href="#/review/${encodeURIComponent(p.game_id)}/${p.ply - 1}">Open in game review</a>
      </div>`;
  }
  side.innerHTML = `<h1>Puzzles</h1>${statLine}${body}`;
  side.querySelectorAll(".lv").forEach((b) => (b.onclick = () => { PZ.step = +b.dataset.step; renderPuzzle(); }));
  const skip = $("#pz-skip");
  if (skip) skip.onclick = () => answerPuzzle(null);
  const next = $("#pz-next");
  if (next) next.onclick = () => showPuzzles(p.id);
}

async function answerPuzzle(uci) {
  const p = PZ.puzzle;
  PZ.busy = true;
  renderPuzzle();
  try {
    // "Show the answer" sends give_up, which the server schedules like a miss.
    const res = await api(`/api/puzzles/${encodeURIComponent(p.id)}/answer`, {
      method: "POST", body: JSON.stringify(uci ? { uci } : { give_up: true }) });
    PZ.result = res;
    PZ.stats = res.stats;
    renderPuzzleCount(res.stats);
    PZ.step = res.correct ? 0 : 1;
    playSound(false);
  } catch (e) {
    toast(e.message, true);
  }
  PZ.busy = false;
  PZ.selected = null;
  renderPuzzle();
  if (PZ.result?.correct) setTimeout(() => { if (PZ.result) { PZ.step = 1; renderPuzzle(); } }, 700);
}

// A small, reusable board: squares + pieces + optional click-to-move.
function drawBoard(root, fen, { flipped = false, selected = null, highlight = [], interactive = false } = {}) {
  const pieces = parseFen(fen);
  let sq = "";
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const f = flipped ? 7 - col : col, r = flipped ? row : 7 - row;
      const name = FILES[f] + (r + 1);
      const light = (f + r) % 2 === 1;
      let coords = "";
      if (row === 7) coords += `<span class="coord file">${FILES[f]}</span>`;
      if (col === 0) coords += `<span class="coord rank">${r + 1}</span>`;
      const cls = [light ? "l" : "d", highlight.includes(name) ? "hl" : "", selected === name ? "sel" : ""].join(" ");
      sq += `<div class="sq ${cls}" data-sq="${name}">${coords}</div>`;
    }
  }
  root.querySelector(".squares").innerHTML = sq;
  let pc = "";
  for (const [s, code] of Object.entries(pieces)) {
    const f = FILES.indexOf(s[0]), r = +s[1] - 1;
    const [x, y] = flipped ? [7 - f, r] : [f, 7 - r];
    pc += `<div class="piece" style="background-image:url(/pieces/${code}.png);transform:translate(${x * 100}%,${y * 100}%)"></div>`;
  }
  root.querySelector(".pieces").innerHTML = pc;
  root.classList.toggle("interactive", interactive);
  root.onclick = interactive ? (e) => {
    const cell = e.target.closest(".sq");
    if (!cell) return;
    const name = cell.dataset.sq, turn = fen.split(" ")[1];
    const own = pieces[name] && pieces[name][0] === turn;
    if (own) { PZ.selected = PZ.selected === name ? null : name; renderPuzzle(); return; }
    if (PZ.selected) answerPuzzle(PZ.selected + name);
  } : null;
}
