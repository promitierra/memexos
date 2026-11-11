"use strict";
// Quality & cost: "Can what goes into the knowledge base be trusted? What did it cost?"
// Layout (bullet graphs and time-series panels): the first-review pass rate trend top left with why reviews fail beside it;
// model backends and today's cost in the right column; below, cost per day by role and every compile batch as time × cost.
const ROLE_COL = ["#4f84ff", "#d2a868", "#d5deea", "#4cc7b6", "#8fb0e8", "#f0a35e", "#8d97a8"];
VIEWS.quality = {title: T("nav.quality"),
  init(el) {
    el.innerHTML = `<div class="ph"><h1>${esc(T("nav.quality"))}<small>QUALITY</small></h1><div class="q">${esc(T("q.q"))}</div></div><div id="q-body"></div>`;
  },
  async enter(el) {
    const B = $("#q-body", el);
    if (!B.childElementCount) B.innerHTML = `<div class="qual">${[0, 1, 2].map(() => `<div class="glass card"><div class="shimmer skel" style="width:40%"></div><div class="shimmer skel" style="height:160px"></div></div>`).join("")}</div>`;
    const [q, R] = await Promise.all([api("/api/quality"), api("/api/runs", {hours: 24 * 30})]);
    renderQuality(B, q, R);
  }
};
function renderQuality(B, q, R) {
  const sum = rows => rows.reduce((a, d) => ({p: a.p + d.pass, f: a.f + d.fail, u: a.u + d.unfinished}), {p: 0, f: 0, u: 0});
  const all = sum(q.gate2_days), recent = sum(q.gate2_days.filter(d => d.day >= sinceDays(7))), rc = q.gate2_rechecks;
  const rate7 = recent.p + recent.f ? recent.p / (recent.p + recent.f) : null, rateAll = all.p + all.f ? all.p / (all.p + all.f) : null;
  const days = fillDays(q.gate2_days.map(d => ({label: d.day, p: d.pass, f: d.fail})), 21, ["p", "f"]).map(d => ({...d, r: d.p + d.f ? d.p / (d.p + d.f) : null}));
  const roles = q.roles, costs = fillDays(q.cost_days.map(d => ({label: d.day, ...d})), 21, roles), total = d => roles.reduce((a, r) => a + (d[r] || 0), 0);
  const c7 = costs.slice(-7).reduce((a, d) => a + total(d), 0) / 7;
  // q.cost_today is priced from tokens, so it also counts calls recorded before a price existed
  const today = q.cost_today != null ? q.cost_today : (LIVE.data ? LIVE.data.metrics.cost_today : total(costs[costs.length - 1]));
  // cost 0.00 with no prices configured is not 'free': the card says which backends are unpriced
  const priced = q.cost_priced !== false, unpriced = q.unpriced_backends || [];
  // a partial bill is still worth showing: hide the number only when there is nothing to show
  const anyCost = today > 0 || c7 > 0;
  const todayShown = anyCost || !unpriced.length ? money(today) : esc(T("q.unpriced"));
  const todaySub = unpriced.length ? esc(T("q.unpriced_sub", {backends: unpriced.join(", ")}))
                                   : esc(T("q.today_sub", {c: money(c7)}));
  const models = q.gate2_models, mtot = models.reduce((a, [, n]) => a + n, 0) || 1, frs = q.fail_reasons || [];
  const MCOL = ["#4f84ff", "#d5deea", "#d2a868", "#4cc7b6"];
  B.innerHTML = `<div class="qual">
    <div class="glass card kpi hero warm" data-in style="view-transition-name:vt-quality"><div class="lb"><span>${esc(T("q.first_pass"))}</span><span class="kicker">GATE-2 · FIRST PASS</span></div>
      <div style="display:flex;align-items:baseline;gap:16px"><div class="v"><span id="q-rate"></span></div><div class="sb">${esc(T("q.first_pass_sb", {p: recent.p, n: recent.p + recent.f, all: pct(rateAll), ap: all.p, an: all.p + all.f}))}</div></div>
      <div id="q-trend"></div><div class="sub" style="margin:4px 0 0">${esc(T("q.first_pass_note"))}</div></div>
    <div class="glass card" data-in><h2 class="h">${esc(T("q.why"))} <small>WHY · 30D</small></h2><div class="sub">${esc(T("q.why_sub", {n: q.fail_reports_30d}))}</div><div id="q-why"></div></div>
    <div class="side">
      <div class="glass card" data-in><h2 class="h">${esc(T("q.budget"))} <small>BUDGET</small></h2>
        ${q.backends.filter(b => b.available).map(b => `<div class="qrow"><div class="t"><span>${esc(b.name)}</span><span class="badge ${b.exhausted ? "crit" : "ok"}">${b.exhausted ? "✕ " + esc(T("q.out")) : "✓ " + esc(T("q.ok"))}</span></div>
          ${b.exhausted && b.until ? `<div class="s">${esc(T("q.out_since", {t: b.until}))}</div>` : ""}</div>`).join("") || `<div class="s">${esc(T("q.no_backends"))}</div>`}
        <div class="qrow"><div class="t"><span>${esc(T("q.today"))}</span><b>${todayShown}</b></div>
          ${bullet(today, Math.max(today, c7) * 1.4 || 1, {ref: c7, refLabel: T("q.avg7"), fmtV: money, color: today > c7 * 1.3 ? "var(--warn)" : "var(--accent-mark)"})}
          <div class="s">${todaySub}</div></div>
        <div class="qrow"><div class="t"><span>${esc(T("q.reviewers"))}</span><b>${mtot}<span class="muted small" style="font:12px var(--f-ui)"> ${esc(T("us.times"))}</span></b></div>
          <div class="vbar" style="height:10px">${models.map(([m, n], i) => `<i style="--i:${i};width:${n / mtot * 100}%;background:${MCOL[i % MCOL.length]}" data-tip="${esc(m)}: ${n}"></i>`).join("")}</div>
          <div class="legend" style="margin-top:6px">${models.map(([m, n], i) => `<span><i style="background:${MCOL[i % MCOL.length]}"></i>${esc(m)} ${n}</span>`).join("")}</div><div class="s">${esc(T("q.reviewers_sub"))}</div></div>
        <div class="qrow"><div class="t"><span>${esc(T("q.rechecks"))}</span><b>${rc.pass + rc.fail}</b></div>
          <div class="s">${esc(T("q.rechecks_sub", {p: rc.pass, f: rc.fail, u: rc.unfinished}))}</div></div></div></div>
    <div class="glass card" data-in><h2 class="h">${esc(T("q.cost"))} <small>COST · 21D</small></h2><div class="sub">${esc(T("q.cost_sub"))}</div><div id="q-cost"></div></div>
    <div class="glass card" data-in><h2 class="h">${esc(T("q.usage_header"))} <small>30D</small></h2>
      <div class="sub">${esc(T("q.usage_sub"))}</div>
      ${(q.model_usage || []).map(u => `<div class="s"><b>${esc(u.model)}</b> · ${u.calls} ${esc(T("q.calls"))} ·
        ${u.input} / ${u.cached} / ${u.output} ${esc(T("q.tokens"))} ·
        ${u.priced && u.cost ? (u.price_source === "list" ? "≈" : "") + money(u.cost) : esc(T("q.no_price"))}${u.price_source === "list" ? " " + esc(T("q.list_price")) : ""}</div>`).join("") || `<div class="s">—</div>`}
    </div>
    <div class="glass card" data-in><h2 class="h">${esc(T("q.runs"))} <small>RUNS · 30D</small></h2><div class="sub">${esc(T("q.runs_sub"))}</div><div id="q-sc"></div></div>
  </div>
  <div class="glass card" style="margin-top:14px" data-in><h2 class="h">${esc(T("q.evals"))} <small>RETRIEVAL EVALS</small></h2><div class="sub">${esc(T("q.evals_sub"))}</div>
    ${q.retrieval_evals.length ? q.retrieval_evals.slice().reverse().map((e, i) => `<details class="fold" style="margin-top:8px"${i === 0 ? " open" : ""}><summary>${esc(e.run)}${e.questions ? " · " + esc(T("q.eval_n", {n: e.questions})) : ""}</summary><div class="in-fold"><table class="t"><tr><th>${esc(T("q.eval_mode"))}</th><th class="r">${esc(T("q.top1"))}</th><th class="r">${esc(T("q.top3"))}</th><th class="r">${esc(T("q.top10"))}</th></tr>${(e.rows || []).map(r => `<tr><td>${esc(r[0])}</td><td class="r mono">${pct(r[1])}</td><td class="r mono">${pct(r[2])}</td><td class="r mono">${pct(r[3])}</td></tr>`).join("")}</table></div></details>`).join("") :
      `<div class="muted small" style="margin-top:8px">${esc(T("q.no_evals"))}</div>`}</div>`;
  playIn(B); tick($("#q-rate"), pct(rate7));
  area($("#q-trend"), days, {key: "r", color: "#e7c792", h: 200, yMax: 1, fmtV: v => pct(v), ref: rateAll == null ? null : {v: rateAll, label: T("q.avg_all", {p: pct(rateAll)}), color: "#b9c3d1"},
    tipX: r => `<br><span class=m>${esc(T("q.trend_tip", {p: r.p, f: r.f}))}</span>`});
  const why = $("#q-why"); if (frs.length) why.appendChild(hbars(frs.map(([, l, n, k]) => [l, n, T("q.why_tip", {n, k})]), {color: "linear-gradient(90deg, rgba(255,107,107,.35), #ff6b6b)"})); else why.innerHTML = `<div class="muted small">${esc(T("c.none"))}</div>`;
  columns($("#q-cost"), costs, roles.map((r, i) => ({key: r, name: T("role." + r) === "role." + r ? r : T("role." + r), color: ROLE_COL[i % ROLE_COL.length]})), {h: 210, fmtV: v => !v ? "$0" : "$" + (v >= 10 ? Math.round(v) : v.toFixed(1))});
  scatter($("#q-sc"), R.runs.filter(r => r.job === "ingest" && r.status === "published" && r.t1));
}
// axis money: below a dollar the rounded label repeats ($0 $0 $1 $1) and the chart looks wrong
function axisMoney(v) { return !v ? "$0" : v >= 10 ? "$" + Math.round(v) : v >= 1 ? "$" + v.toFixed(1) : "$" + v.toFixed(2); }
function scatter(el, runs) {
  el.classList.add("chart");
  if (!runs.length) { el.innerHTML = `<div class="empty">${esc(T("c.no_data"))}</div>`; return; }
  sized(el, W => {
    const H = 230, padL = 40, padB = 24, padT = 8, xs = runs.map(r => (r.t1 - r.t0) / 60), ys = runs.map(r => Number(r.cost) || 0);
    // the x scale follows the 95th percentile; the rare very slow batch sits on the right edge, drawn hollow (its real time is in the tooltip)
    const p95 = xs.slice().sort((a, b) => a - b)[Math.floor(xs.length * 0.95)] || 1;
    const mx = niceMax(p95 * 1.15), my = niceMax(Math.max(...ys, 1)), X = v => padL + (W - padL - 10) * Math.min(v, mx) / mx, Y = v => padT + (H - padT - padB) * (1 - v / my);
    let s = `<svg height="${H}" viewBox="0 0 ${W} ${H}">`;
    for (const t of [0, .25, .5, .75, 1]) s += `<line class="gl" x1="${padL}" x2="${W - 10}" y1="${Y(my * t)}" y2="${Y(my * t)}"/><text x="${padL - 6}" y="${Y(my * t) + 3.5}" text-anchor="end">${axisMoney(my * t)}</text><text x="${X(mx * t)}" y="${H - 6}" text-anchor="middle">${esc(T("u.min", {n: Math.round(mx * t)}))}</text>`;
    runs.forEach((r, i) => { const g = (r.gate2 || []).find(x => x.mode === "ingest"), c = !g ? "#7b8697" : g.passed ? "var(--ok)" : g.passed === false ? "var(--crit)" : "#7b8697";
      s += `<circle class="pt" cx="${X(xs[i])}" cy="${Y(ys[i])}" r="${3.5 + Math.min(5, (r.files.length || 1))}" fill="${c}" fill-opacity="${xs[i] > mx ? 0 : .55}" stroke="${c}" stroke-width="1.2" data-id="${esc(r.id)}" data-tip="${esc(runTip(r))}" style="cursor:pointer;transform-box:fill-box;transform-origin:center;animation:inPop var(--sp-bouncy-d) var(--sp-bouncy-e) both;animation-delay:${Math.min(i, 60) * 12}ms"/>`; });
    el.innerHTML = s + `</svg><div class="legend" style="margin-top:4px"><span><i style="background:var(--ok)"></i>${esc(T("q.sc_pass"))}</span><span><i style="background:var(--crit)"></i>${esc(T("q.sc_fail"))}</span><span><i style="background:#7b8697"></i>${esc(T("q.sc_none"))}</span><span class="muted">${esc(T("q.sc_note"))}</span></div>`;
    el.querySelectorAll(".pt").forEach(p => { p.onclick = () => go("tasks", {run: p.dataset.id}); p.onpointerenter = () => anim(p, [{transform: "scale(1)"}, {transform: "scale(1.5)"}, {transform: "scale(1)"}], "bouncy"); });
  });
}
