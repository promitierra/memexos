"use strict";
// MemexOS console · shared script: helpers, spring motion, background, overlays, markdown, charts, page switching,
// live data and the command palette. Every visible word comes from the language pack (window.BOOT.s) through T().
const BOOT = window.BOOT || {s: {}, lang: "en", token: ""};
const LANG = BOOT.lang, LOCALE = LANG === "zh" ? "zh-CN" : "en-US";
function T(k, v) { let s = BOOT.s[k]; if (s == null) return k; if (v) s = String(s).replace(/\{(\w+)\}/g, (m, x) => (v[x] ?? m)); return s; }
const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = n => (n ?? 0).toLocaleString(LOCALE);
const pct = x => x == null || isNaN(x) ? "—" : Math.round(x * 100) + "%";
// Mirrors pricing.money(): two decimals are real money, four keep a sub-cent
// call from printing as "$0.00", which reads as free.
const money = x => {
  const v = Number(x) || 0;
  if (Math.abs(v) >= 100) return "$" + v.toFixed(0);
  if (Math.abs(v) >= 0.01) return "$" + v.toFixed(2);
  return v ? "$" + v.toFixed(4) : "$0.00";
};
// Minutes east of UTC, sent with every request: "today" is the reader's day,
// not the container's. Bogota is -300, so its day ends at 05:00 UTC.
const TZ = -new Date().getTimezoneOffset();
const api = (p, q = {}) => fetch(p + "?" + new URLSearchParams({tz: TZ, ...q})).then(r => r.json());
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const rgba = (hex, a) => { const n = parseInt(hex.replace("#", ""), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };
const plain = s => String(s ?? "").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (m, a, b) => b || a).replace(/`([^`]+)`/g, "$1");
const sinceDays = n => { const d = new Date(); d.setDate(d.getDate() - n + 1); return d.toISOString().slice(0, 10); };
const pad2 = n => String(n).padStart(2, "0");
const hm = ts => { const d = new Date(ts * 1000); return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); };
const mdhm = ts => { const d = new Date(ts * 1000); return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${hm(ts)}`; };
const dur = s => s == null ? "—" : s < 60 ? T("u.sec", {n: Math.round(s)}) : s < 3600 ? T("u.min", {n: Math.round(s / 60)}) : T("u.hmin", {h: Math.floor(s / 3600), m: Math.round(s % 3600 / 60)});
const list = xs => xs.join(T("u.sep"));
// a mono English kicker plus the word in the page's language, without repeating it when they are the same
const kick = (en, word) => en.toLowerCase() === String(word).toLowerCase() ? esc(en) : `${esc(en)} · <b>${esc(word)}</b>`;
function fillDays(rows, days, keys) {
  const by = Object.fromEntries(rows.map(r => [r.label, r])), out = [], d = new Date();
  d.setHours(12); d.setDate(d.getDate() - days + 1);
  for (let i = 0; i < days; i++, d.setDate(d.getDate() + 1)) {
    const k = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    out.push(by[k] || Object.fromEntries([["label", k], ...keys.map(x => [x, 0])]));
  }
  return out;
}

// ================= springs: the same model as Remotion's spring() (stiffness, damping, mass), compiled into a CSS linear() easing
function spring({stiffness = 170, damping = 26, mass = 1} = {}) {
  let x = 0, v = 0, t = 0; const dt = 1 / 240, pts = [0];
  while (t < 3) { const a = (-stiffness * (x - 1) - damping * v) / mass; v += a * dt; x += v * dt; t += dt; pts.push(x); if (Math.abs(x - 1) < 5e-4 && Math.abs(v) < 5e-3) break; }
  const n = 48, out = []; for (let i = 0; i < n; i++) out.push(+pts[Math.round(i * (pts.length - 1) / (n - 1))].toFixed(4));
  out[0] = 0; out[n - 1] = 1; return {easing: `linear(${out.join(",")})`, ms: Math.round(t * 1000)};
}
const SP = {snappy: spring({stiffness: 420, damping: 32}), gentle: spring({stiffness: 170, damping: 24}), bouncy: spring({stiffness: 300, damping: 15})};
for (const [k, v] of Object.entries(SP)) { document.documentElement.style.setProperty(`--sp-${k}-e`, v.easing); document.documentElement.style.setProperty(`--sp-${k}-d`, v.ms + "ms"); }
const anim = (el, kf, sp = "gentle", o = {}) => el.animate(kf, {duration: reduce ? 0 : SP[sp].ms, easing: SP[sp].easing, fill: "backwards", ...o});
// entrance: elements marked data-in appear one after another
function playIn(root, d0 = 0) {
  const els = root.matches && root.matches("[data-in]") ? [root] : $$("[data-in]", root);
  els.forEach((el, i) => { if (!el.style.getPropertyValue("--i")) el.style.setProperty("--i", i); if (d0) el.style.setProperty("--d0", d0 + "ms"); el.classList.remove("in"); });
  void root.offsetWidth; els.forEach(el => el.classList.add("in"));
}
// list reordering: remember old positions, swap content, spring from the old to the new position (FLIP)
function flip(container, sel, mutate) {
  const before = new Map($$(sel, container).map(el => [el.dataset.key, el.getBoundingClientRect()]));
  mutate();
  $$(sel, container).forEach((el, i) => {
    const b = before.get(el.dataset.key), a = el.getBoundingClientRect();
    if (b) { const dx = b.left - a.left, dy = b.top - a.top; if (dx || dy) anim(el, [{transform: `translate(${dx}px,${dy}px)`}, {transform: "none"}], "gentle"); }
    else anim(el, [{opacity: 0, transform: "scale(.9)", filter: "blur(4px)"}, {opacity: 1, transform: "none", filter: "none"}], "gentle", {delay: Math.min(i, 12) * 22});
  });
}

// ================= lens buttons: a displacement map (red = horizontal, green = vertical), strongest at the edges; the highlight follows the pointer; presses ripple
(function lensMap() {
  const N = 128, c = document.createElement("canvas"); c.width = c.height = N; const g = c.getContext("2d"), im = g.createImageData(N, N);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) { const u = x / (N - 1) * 2 - 1, v = y / (N - 1) * 2 - 1, e = Math.pow(Math.max(Math.abs(u), Math.abs(v)), 3.2), i = (y * N + x) * 4;
    im.data[i] = 128 - u * e * 127; im.data[i + 1] = 128 - v * e * 127; im.data[i + 2] = 128; im.data[i + 3] = 255; }
  g.putImageData(im, 0, 0); $("#lensmap").setAttribute("href", c.toDataURL());
})();
document.addEventListener("pointermove", e => { const b = e.target.closest && e.target.closest(".btn"); if (!b) return; const r = b.getBoundingClientRect();
  b.style.setProperty("--gx", (e.clientX - r.left) + "px"); b.style.setProperty("--gy", (e.clientY - r.top) + "px"); }, {passive: true});
document.addEventListener("pointerdown", e => { const b = e.target.closest && e.target.closest(".btn"); if (!b || reduce) return;
  const r = b.getBoundingClientRect(), s = Math.max(r.width, r.height), d = document.createElement("span");
  d.className = "ripple"; d.style.cssText = `width:${s}px;height:${s}px;left:${e.clientX - r.left - s / 2}px;top:${e.clientY - r.top - s / 2}px`; b.appendChild(d); setTimeout(() => d.remove(), 800); });
// magnetic: .mag buttons are pulled toward a nearby pointer and spring back when it leaves
document.addEventListener("pointermove", e => {
  for (const b of $$(".mag")) { const r = b.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2, dx = e.clientX - cx, dy = e.clientY - cy, d = Math.hypot(dx, dy);
    const near = d < Math.max(r.width, r.height) * 0.9;
    if (near) { b.style.transition = "transform .25s var(--out)"; b.style.transform = `translate(${dx * .22}px, ${dy * .3}px)`; b.dataset.mag = 1; }
    else if (b.dataset.mag) { delete b.dataset.mag; b.style.transition = `transform ${SP.bouncy.ms}ms ${SP.bouncy.easing}`; b.style.transform = ""; } }
}, {passive: true});

// ================= background: L1 flowing light (WebGL fractal noise; without WebGL a few drifting radial glows) · L2 parallax motes
const hex3 = h => { const n = parseInt(h.slice(1), 16); return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]; };
const BGC = () => ["--c1", "--c2", "--c3", "--bg0"].map(v => hex3(cssv(v)));
(function aurora() {
  const cv = $("#l1"), gl = cv.getContext("webgl", {antialias: false, preserveDrawingBuffer: false}); if (!gl) return auroraFallback(cv);
  const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
  const fs = `precision mediump float;uniform vec2 r;uniform float t;uniform vec3 c1,c2,c3,bg;
    float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
    float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p*=2.03;a*=.5;}return v;}
    void main(){vec2 uv=gl_FragCoord.xy/r;vec2 p=uv*vec2(r.x/r.y,1.)*1.7;
      float q=fbm(p+vec2(t*.035,t*.02));float w=fbm(p+q*1.9+vec2(-t*.022,t*.03));
      vec3 col=bg;col=mix(col,c1,smoothstep(.40,.92,w)*.62*(1.-uv.y*.35));
      col=mix(col,c2,smoothstep(.44,.92,fbm(p*1.25-q+t*.018))*.66);
      col=mix(col,c3,smoothstep(.60,.98,q)*.30*(1.-uv.y));
      float v=1.-smoothstep(.35,1.25,length(uv-vec2(.5,.45)));col*=.45+.55*v;gl_FragColor=vec4(col,1.);}`;
  const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); return o; };
  const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(pr); gl.useProgram(pr);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U = n => gl.getUniformLocation(pr, n), cols = BGC(); let last = 0;
  const size = () => { cv.width = innerWidth * .45; cv.height = innerHeight * .45; gl.viewport(0, 0, cv.width, cv.height); }; size(); addEventListener("resize", size);
  const loop = now => { requestAnimationFrame(loop); if (now - last < 40 || document.hidden) return; last = now;
    gl.uniform2f(U("r"), cv.width, cv.height); gl.uniform1f(U("t"), reduce ? 0 : now / 1000);
    gl.uniform3fv(U("c1"), cols[0]); gl.uniform3fv(U("c2"), cols[1]); gl.uniform3fv(U("c3"), cols[2]); gl.uniform3fv(U("bg"), cols[3]); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); };
  requestAnimationFrame(loop);
})();
function auroraFallback(cv) {
  const g = cv.getContext("2d"), cols = BGC(), blobs = [0, 1, 2, 3, 4].map(i => ({c: [0, 1, 0, 1, 2][i], ph: i * 1.7, r: .45 + (i % 2) * .2}));
  const size = () => { cv.width = innerWidth * .35; cv.height = innerHeight * .35; }; size(); addEventListener("resize", size); let last = 0;
  const loop = now => { requestAnimationFrame(loop); if (now - last < 60 || document.hidden) return; last = now; const t = reduce ? 0 : now / 1000, W = cv.width, H = cv.height;
    g.globalCompositeOperation = "source-over"; g.fillStyle = `rgb(${cols[3].map(v => v * 255 | 0)})`; g.fillRect(0, 0, W, H); g.globalCompositeOperation = "lighter";
    for (const b of blobs) { const x = W * (.5 + .38 * Math.sin(t * .05 + b.ph)), y = H * (.45 + .32 * Math.cos(t * .04 + b.ph * 1.3)), R = Math.max(W, H) * b.r;
      const gr = g.createRadialGradient(x, y, 0, x, y, R); gr.addColorStop(0, `rgba(${cols[b.c].map(v => v * 255 | 0)},${b.c === 2 ? .18 : .34})`); gr.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = gr; g.fillRect(0, 0, W, H); } };
  requestAnimationFrame(loop);
}
(function motes() {
  const cv = $("#l2"), g = cv.getContext("2d"); let W, H, mx = 0, my = 0, tx = 0, ty = 0; const dpr = Math.min(2, devicePixelRatio || 1);
  const P = Array.from({length: 200}, () => ({x: Math.random(), y: Math.random(), z: .15 + Math.random() * .85, ph: Math.random() * 6.28, hot: Math.random() < .1}));
  const size = () => { W = innerWidth; H = innerHeight; cv.width = W * dpr; cv.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0); }; size(); addEventListener("resize", size);
  addEventListener("pointermove", e => { tx = e.clientX / W - .5; ty = e.clientY / H - .5; }, {passive: true});
  const ink = cssv("--ink"), acc = cssv("--accent");
  const loop = now => { requestAnimationFrame(loop); if (document.hidden) return; const t = now / 1000; mx += (tx - mx) * .05; my += (ty - my) * .05;
    g.clearRect(0, 0, W, H);
    for (const p of P) { if (!reduce) { p.y -= .00005 * p.z * 8; if (p.y < -.02) { p.y = 1.02; p.x = Math.random(); } }
      const x = p.x * W - mx * 60 * p.z, y = ((p.y * H - my * 40 * p.z) % H + H) % H, r = p.z * (p.hot ? 2 : 1.2);
      const a = (p.hot ? .5 : .26) * p.z * (.6 + .4 * Math.sin(t * 1.3 + p.ph));
      g.globalAlpha = a; g.fillStyle = p.hot ? acc : ink; g.beginPath(); g.arc(x, y, r, 0, 6.283); g.fill();
      if (p.hot) { g.globalAlpha = a * .22; g.beginPath(); g.arc(x, y, r * 4, 0, 6.283); g.fill(); } }
    g.globalAlpha = 1; };
  requestAnimationFrame(loop);
})();

// ================= tooltip: follows the pointer and springs in; put data-tip (HTML) on any element
const Tip = {el: null, owner: null,
  show(html, x, y, owner) { const t = this.el || (this.el = $("#tip")); this.owner = owner || null; if (t.dataset.h !== html) { t.innerHTML = html; t.dataset.h = html; } this.move(x, y); t.classList.add("on"); },
  move(x, y) { const t = this.el; if (!t) return; const w = t.offsetWidth, h = t.offsetHeight;
    const L = x + 16 + w > innerWidth - 8 ? x - w - 14 : x + 16, Tp = y + 16 + h > innerHeight - 8 ? y - h - 12 : y + 16;
    t.style.setProperty("--tx", L + "px"); t.style.setProperty("--ty", Tp + "px"); },
  hide() { this.owner = null; this.el && this.el.classList.remove("on"); }};
// elements with data-tip show it automatically; charts and canvases call Tip.show(…, owner) and keep it while the pointer is inside the owner
document.addEventListener("pointermove", e => { const el = e.target.closest && e.target.closest("[data-tip]");
  if (el) Tip.show(el.dataset.tip, e.clientX, e.clientY); else if (Tip.owner && Tip.owner.contains(e.target)) return; else if (Tip.el && Tip.el.classList.contains("on")) Tip.hide(); }, {passive: true});
document.addEventListener("pointerout", e => { const el = e.target.closest && e.target.closest("[data-tip]"); if (el && !el.contains(e.relatedTarget)) Tip.hide(); });

// ================= toasts: slide in from the right with a progress bar, optionally with one action (such as undo)
function toast(title, text = "", {action, ms = 4200} = {}) {
  const t = document.createElement("div"); t.className = "toast glass warm";
  t.innerHTML = `<div><div class="tt">${esc(title)}</div>${text ? `<div class="tx">${esc(text)}</div>` : ""}</div>${action ? `<button class="btn xs ghost act">${esc(action.label)}</button>` : ""}<i class="bar"></i>`;
  $("#toasts").prepend(t); requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add("in")));
  t.querySelector(".bar").animate([{transform: "scaleX(1)"}, {transform: "scaleX(0)"}], {duration: ms, easing: "linear", fill: "forwards"});
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 320); };
  if (action) t.querySelector(".act").onclick = () => { close(); action.fn(); };
  setTimeout(close, ms); return close;
}

// ================= scrim, modal (grows from the click), confirm
const Scrim = {n: 0, el: () => $("#scrim"), on() { this.n++; this.el().classList.add("on"); }, off() { this.n = Math.max(0, this.n - 1); if (!this.n) this.el().classList.remove("on"); }};
function modal({title, html = "", actions = [{label: T("c.ok"), primary: true, value: true}], origin, width} = {}) {
  return new Promise(res => {
    const m = document.createElement("div"); m.className = "modal glass sweep warm"; if (width) m.style.width = width;
    m.innerHTML = `<h3>${esc(title)}</h3><div class="bd">${html}</div><div class="acts">${actions.map((a, i) => `<button class="btn ${a.primary ? "primary lens" : a.danger ? "danger" : "ghost"}" data-i="${i}">${esc(a.label)}</button>`).join("")}</div>`;
    document.body.appendChild(m);
    const x = origin ? origin.clientX : innerWidth / 2, y = origin ? origin.clientY : innerHeight / 2;
    m.style.transformOrigin = `${x - (innerWidth - m.offsetWidth) / 2}px ${y - (innerHeight - m.offsetHeight) / 2}px`;
    Scrim.on(); requestAnimationFrame(() => requestAnimationFrame(() => m.classList.add("on")));
    const done = v => { m.classList.add("out"); m.classList.remove("on"); Scrim.off(); document.removeEventListener("keydown", key, true); $("#scrim").removeEventListener("click", bg); setTimeout(() => m.remove(), 260); res(v); };
    const key = e => { if (e.key === "Escape") { e.stopPropagation(); done(undefined); } if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); const p = actions.findIndex(a => a.primary); if (p >= 0) done(actions[p].value); } };
    const bg = () => done(undefined);
    m.querySelectorAll(".acts button").forEach(b => b.onclick = () => done(actions[+b.dataset.i].value));
    document.addEventListener("keydown", key, true); $("#scrim").addEventListener("click", bg);
  });
}
const confirmBox = (e, title, html, ok = T("c.confirm"), danger = false) => modal({title, html, origin: e, actions: [{label: T("c.cancel"), value: false}, {label: ok, primary: !danger, danger, value: true}]});
// write actions: done directly or after confirming; the result is toasted and live data refreshed
async function post(action, extra = {}) {
  const r = await fetch("/api/action", {method: "POST", headers: {"Content-Type": "application/json", "X-Memex-Token": BOOT.token}, body: JSON.stringify({action, ...extra})})
    .then(r => r.json()).catch(() => ({ok: false, message: T("c.no_server")}));
  return {ok: r.ok !== false && !r.error, message: r.message || r.error || T("c.done")};
}
async function doAction(e, action, extra = {}, {confirm, title} = {}) {
  if (confirm && !(await confirmBox(e, title || T("c.confirm"), confirm))) return null;
  const r = await post(action, extra); toast(title || T("c.submitted"), r.message); pollLive(); return r;
}

// ================= markdown: headings, paragraphs, lists, tables, code, quotes, links, [[wiki links]], raw/ paths
function md(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n"); let out = "", i = 0;
  const inline = s => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/!\[\[([^\]]+)\]\]/g, (m, a) => `<span class="muted">［${esc(T("c.image"))}: ${a}］</span>`)
    .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (m, a, b) => `<a class="wl" data-name="${a.trim()}">${b || a}</a>`)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, a, b) => /^https?:/.test(b) ? `<a href="${b}" target="_blank" rel="noopener noreferrer">${a}</a>` :
      /^(wiki|raw|inbox)\//.test(b) ? `<span class="link" data-path="${b}">${a}</span>` : a)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^\w*])\*([^*\s][^*]*?)\*(?=[^\w*]|$)/g, "$1<i>$2</i>")
    .replace(/(^|[^`\w"])(raw\/[^\s<`，。；）)"]+?\.md)/g, '$1<span class="link" data-path="$2">$2</span>');
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) { let b = ""; i++; while (i < lines.length && !/^```/.test(lines[i])) b += esc(lines[i++]) + "\n"; i++; out += `<pre>${b}</pre>`; continue; }
    const h = l.match(/^(#{1,4})\s+(.*)/); if (h) { out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; i++; continue; }
    if (/^\|/.test(l)) { const rows = []; while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      out += "<table>" + rows.filter(r => !/^\|[\s:|-]+\|$/.test(r)).map((r, k) => "<tr>" + cells(r).map(c => k === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</table>"; continue; }
    if (/^\s*([-*]|\d+\.)\s/.test(l)) { let b = ""; while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) {
        const ind = lines[i].match(/^\s*/)[0].length; let text = lines[i].replace(/^\s*([-*]|\d+\.)\s/, ""); i++;
        // a wrapped item continues on indented lines that are not items themselves
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s/.test(lines[i])) text += " " + lines[i++].trim();
        b += `<li style="margin-left:${ind * 8}px">${inline(text)}</li>`; }
      out += `<ul>${b}</ul>`; continue; }
    if (/^>/.test(l)) { let b = ""; while (i < lines.length && /^>/.test(lines[i])) b += inline(lines[i++].replace(/^>\s?/, "")) + "<br>"; out += `<blockquote>${b}</blockquote>`; continue; }
    if (!l.trim()) { i++; continue; }
    let p = ""; while (i < lines.length && lines[i].trim() && !/^(#|\||```|>|\s*([-*]|\d+\.)\s)/.test(lines[i])) p += inline(lines[i++]) + " ";
    out += `<p>${p}</p>`;
  }
  return out;
}

// ================= page types (stable ids from the server; words from the language pack)
const TYPE_COL = {topic: "#f2f5f9", concept: "#4f7dff", entity: "#4cc7b6", source: "#d2a868", raw: "#6c7482"};
const typeName = t => T("type." + (t || "page"));
const PLURAL = {topic: "topics", concept: "concepts", entity: "entities", source: "sources"};
const typesName = t => T("type." + (PLURAL[t] || t));
const typeBadge = t => `<span class="badge"><i class="tdot" style="background:${TYPE_COL[t] || "#7b8697"};box-shadow:0 0 8px ${TYPE_COL[t] || "#7b8697"}"></i>${esc(typeName(t))}</span>`;
const jobName = j => T("job." + j) === "job." + j ? j : T("job." + j);
const stepName = s => T("step." + s) === "step." + s ? s : T("step." + s);
const pageTitle = p => String(p || "").split("/").pop().replace(/\.md$/, "");

// ================= drawer: a wiki page or a raw source in full, with where it came from and what happened to it
async function openPage(path) {
  const D = $("#drawer");
  if (!D.classList.contains("on")) { Scrim.on(); D.classList.add("on"); }
  $("#d-title").textContent = pageTitle(path); $("#d-sub").textContent = path;
  $("#d-main").innerHTML = `<div class="shimmer skel" style="width:60%"></div><div class="shimmer skel" style="width:90%"></div><div class="shimmer skel" style="width:80%"></div><div class="shimmer skel" style="width:70%"></div>`; $("#d-side").innerHTML = "";
  const d = await api("/api/page", {path});
  if (d.error) { $("#d-main").innerHTML = `<div class="empty">${esc(T("drawer.not_found"))}</div>`; return; }
  const fm = d.frontmatter || {}, side = [];
  $("#d-title").textContent = pageTitle(d.path);
  $("#d-sub").innerHTML = `${d.type ? typeBadge(d.type) + " " : ""}<span class="mono tiny muted">${esc(d.path)}</span>`;
  $("#d-graph").hidden = !(typeof GRAPH !== "undefined" && GRAPH.byId.has(d.path)); $("#d-graph").dataset.id = d.path;
  const c = d.catalog;
  if (c) side.push(`<h4>${esc(T("drawer.catalog"))}</h4><div class="it">${esc(list([c.domain, c.content_type, c.level].filter(Boolean)))}<br><span class="muted">${esc(T("drawer.evidence_durability", {e: c.evidence || "—", d: c.durability || "—"}))}</span>${c.scope_note ? `<br><span class="muted">${esc(c.scope_note)}</span>` : ""}</div>`);
  if (d.cited_by && d.cited_by.length) side.push(`<h4>${esc(T("drawer.cited_by", {n: d.cited_by.length}))}</h4>` + d.cited_by.map(p => `<div class="it"><span class="link" data-path="${esc(p)}">${esc(pageTitle(p))}</span></div>`).join(""));
  if (d.sources.length && !d.path.startsWith("raw/")) side.push(`<h4>${esc(T("drawer.sources", {n: d.sources.length}))}</h4>` + d.sources.map(s => `<div class="it"><span class="link" data-path="${esc(s.path)}">${esc(s.path.replace(/^raw\//, ""))}</span>${s.exists ? "" : ` <span class="badge crit">✕ ${esc(T("drawer.missing"))}</span>`}<br><span class="muted">${esc(s.domain || "")}${s.evidence ? " · " + esc(s.evidence) : ""}</span></div>`).join(""));
  side.push(`<h4>${esc(T("drawer.issues", {n: d.issues.length}))}</h4>` + (d.issues.length ? d.issues.map(x => `<div class="it"><span class="badge ${x.fixed ? "ok" : "warn"}">${x.fixed ? "✓ " + esc(T("drawer.issue_fixed")) : "… " + esc(T("drawer.issue_open"))}</span> ${esc(x.label)}<br>“${esc(x.claim)}”<br><span class="muted">${esc(x.reason)}</span></div>`).join("") : `<div class="muted small">${esc(T("c.none"))}</div>`));
  side.push(`<h4>${esc(T("drawer.used", {n: d.used.length}))}</h4>` + (d.used.length ? d.used.map(u => `<div class="it">${esc(u.when)} · ${esc(u.agent)} · ${esc(u.project)}<br><span class="muted">${esc(u.question)}</span></div>`).join("") : `<div class="muted small">${esc(T("drawer.not_used"))}</div>`));
  if (d.in.length) side.push(`<h4>${esc(T("drawer.linked_from", {n: d.in.length}))}</h4><div class="it">${d.in.map(n => `<a class="wl" data-name="${esc(n)}">${esc(n)}</a>`).join(T("u.sep"))}</div>`);
  side.push(`<h4>${esc(T("drawer.history", {n: d.history.length}))}</h4>` + d.history.map(h => `<div class="it">${esc(h.when)} <span class="mono tiny muted">${esc(h.sha)}</span><br><span class="ink2">${esc(h.subject.slice(0, 140))}</span></div>`).join(""));
  const meta = Object.entries(fm).filter(([k]) => !["tags"].includes(k)).map(([k, v]) => `<span class="badge">${esc(k)}: ${esc(Array.isArray(v) ? list(v) : typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join(" ");
  $("#d-main").innerHTML = `${meta ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px" data-in="fade">${meta}</div>` : ""}<div class="md" data-in>${md(d.body)}</div>`;
  $("#d-side").innerHTML = side.map(s => `<div data-in="right">${s}</div>`).join("");
  $("#d-main").scrollTop = 0; $("#d-side").scrollTop = 0; playIn($("#drawer"), 60);
}
function closeDrawer() { const D = $("#drawer"); if (!D.classList.contains("on")) return; D.classList.remove("on"); Scrim.off(); }
document.addEventListener("click", async e => {
  const p = e.target.closest("[data-path]"); if (p && !e.target.closest("[data-nopath]")) { e.preventDefault(); openPage(p.dataset.path); return; }
  const w = e.target.closest("a.wl"); if (w) { e.preventDefault(); const r = await api("/api/resolve", {name: w.dataset.name}); r.path ? openPage(r.path) : toast(T("drawer.no_page"), T("drawer.no_page_tx", {name: w.dataset.name})); }
});

// ================= rolling digits
function tick(el, value, f = fmt) {
  if (!el) return; const s = typeof value === "number" ? f(value) : String(value ?? "—");
  if (el.dataset.v === s) return; const first = el.dataset.v == null; el.dataset.v = s;
  if (el.childElementCount !== s.length || !el.classList.contains("tick")) {
    el.classList.add("tick");
    el.innerHTML = [...s].map(ch => /\d/.test(ch) ? `<span class="d"><i>${[...Array(10).keys()].map(i => `<b>${i}</b>`).join("")}</i></span>` : `<span>${esc(ch)}</span>`).join("");
    void el.offsetWidth;
  }
  [...s].forEach((ch, i) => { const c = el.children[i]; if (/\d/.test(ch) && c.firstElementChild) { const col = c.firstElementChild; col.style.transitionDelay = (first ? i * 50 : (s.length - i) * 30) + "ms"; col.style.transform = `translateY(-${+ch * 1.1}em)`; } else if (!/\d/.test(ch)) c.textContent = ch; });
}

// ================= list hover highlight: a patch of light springs between rows; the selected row has its own
function hlList(host, sel) {
  host.classList.add("hl-host");
  let bar = host.querySelector(":scope > .hl-bar"), pick = host.querySelector(":scope > .hl-sel");
  if (!bar) { bar = document.createElement("i"); bar.className = "hl-bar"; host.prepend(bar); }
  if (!pick) { pick = document.createElement("i"); pick.className = "hl-sel"; host.prepend(pick); }
  const to = (el, b, sp) => { const y = el.offsetTop, h = el.offsetHeight; const from = {top: b.style.top || y + "px", height: b.style.height || h + "px"};
    b.style.top = y + "px"; b.style.height = h + "px"; b.animate([from, {top: y + "px", height: h + "px"}], {duration: SP[sp].ms, easing: SP[sp].easing}); };
  host.onpointerover = e => { const it = e.target.closest(sel); if (it && host.contains(it)) to(it, bar, "snappy"); };
  // the selection follows the row's real position and height: rows that wrap or rows inserted above keep it aligned
  let cur = null; const snap = () => { if (cur && cur.isConnected) { pick.style.top = cur.offsetTop + "px"; pick.style.height = cur.offsetHeight + "px"; } };
  const ro = new ResizeObserver(snap); ro.observe(host);
  return {select(el) { if (cur) ro.unobserve(cur); cur = el; if (!el) { pick.style.opacity = 0; return; } ro.observe(el); pick.style.opacity = 1; to(el, pick, "bouncy"); }};
}
// segmented control: the pill springs to the clicked segment
function seg(el, onPick) {
  let pill = el.querySelector(".pill"); if (!pill) { pill = document.createElement("span"); pill.className = "pill"; el.prepend(pill); }
  const move = (b, now) => { const x = b.offsetLeft, w = b.offsetWidth;
    const from = {left: pill.style.left || x + "px", width: pill.style.width || w + "px"}; pill.style.left = x + "px"; pill.style.width = w + "px";
    if (!now) pill.animate([from, {left: x + "px", width: w + "px"}], {duration: SP.bouncy.ms, easing: SP.bouncy.easing}); $$("button", el).forEach(x => x.classList.toggle("on", x === b)); };
  el.onclick = e => { const b = e.target.closest("button"); if (!b || b.classList.contains("on")) return; move(b); onPick && onPick(b.dataset.v, b); };
  const on = el.querySelector("button.on") || el.querySelector("button"); requestAnimationFrame(() => on && move(on, true));
  return {set(v) { const b = el.querySelector(`button[data-v="${v}"]`); if (b) move(b); }};
}

// ================= charts (SVG at the container's real width; bars grow from the bottom, lines draw from the left)
// x labels: every few; the last one (today) always, dropping the one before when they would collide
function xLab(i, n) { if (n <= 10) return true; const k = Math.ceil(n / 8); if (i === n - 1) return true; return i % k === 0 && n - 1 - i >= k * 0.6; }
function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p; }
// Axis for real data: pick a round step first, then the top of the scale.
// Using niceMax alone jumps 1, 2, 2.5, 5, 10, so a 0.48 maximum became a 1.00
// axis and every sub-dollar chart looked flat.
function niceAxis(max, n = 5) {
  if (!(max > 0)) return {max: 0, ticks: [0]};
  const raw = max / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  const top = Math.ceil(max / step - 1e-9) * step, ticks = [];
  for (let v = 0; v <= top + step * 1e-6; v += step) ticks.push(+v.toFixed(10));
  return {max: top, ticks};
}
function moneyTick(v) {
  if (!v) return "$0";
  if (v < 1) return "$" + v.toFixed(2);                 // cents, or the labels repeat
  if (v < 1000) return "$" + (Number.isInteger(v) ? v : v.toFixed(1));
  return "$" + Math.round(v);
}
function sized(el, draw) {
  let lastW = 0; const go = () => { const w = el.clientWidth; if (!w || Math.abs(w - lastW) < 2) return; lastW = w; draw(w); };
  go(); new ResizeObserver(go).observe(el);
}
function columns(el, rows, series, {h = 170, stacked = true, onClick, fmtV = fmt, tipX} = {}) {
  el.classList.add("chart");
  if (!rows.length) { el.innerHTML = `<div class="empty">${esc(T("c.no_data"))}</div>`; return; }
  const legend = series.length > 1 ? `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
  sized(el, W => {
    const padL = 34, padB = 20, padT = 6, H = h, tot = r => stacked ? series.reduce((a, s) => a + (r[s.key] || 0), 0) : Math.max(...series.map(s => r[s.key] || 0));
    const axis = niceAxis(Math.max(...rows.map(tot)), 4), mx = axis.max || 1, step = (W - padL) / rows.length, bw = Math.max(2, Math.min(22, step * .62));
    const y = v => padT + (H - padT - padB) * (1 - v / mx);
    let s = `<svg height="${H}" viewBox="0 0 ${W} ${H}">`;
    for (const v of axis.ticks) s += `<line class="gl" x1="${padL}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${padL - 6}" y="${y(v) + 3.5}" text-anchor="end">${fmtV(v)}</text>`;
    rows.forEach((r, i) => {
      const x = padL + i * step + (step - bw) / 2; let base = 0;
      series.forEach((se, j) => { const v = r[se.key] || 0; if (!v) return;
        const x0 = stacked ? x : x + j * (bw / series.length), w = stacked ? bw : bw / series.length - 1.5;
        const top = y(stacked ? base + v : v), bot = y(stacked ? base : 0) - (stacked && base ? 2 : 0), hh = Math.max(1, bot - top);
        const isTop = !stacked || series.slice(j + 1).every(t => !r[t.key]), rr = isTop ? Math.min(3, hh, w / 2) : 0;
        s += `<path class="bar" style="--i:${i}" data-i="${i}" fill="${se.color}" d="M${x0},${bot} V${top + rr} Q${x0},${top} ${x0 + rr},${top} H${x0 + w - rr} Q${x0 + w},${top} ${x0 + w},${top + rr} V${bot} Z"/>`;
        base += v; });
      if (xLab(i, rows.length)) s += `<text x="${x + bw / 2}" y="${H - 5}" text-anchor="middle">${esc(String(r.label).slice(5))}</text>`;
      s += `<rect class="hit" data-i="${i}" x="${padL + i * step}" y="${padT}" width="${step}" height="${H - padT - padB}"/>`;
    });
    s += `<line class="ax" x1="${padL}" x2="${W}" y1="${y(0)}" y2="${y(0)}"/></svg>`;
    el.innerHTML = legend + s;
    const svg = el.querySelector("svg");
    svg.querySelectorAll("rect.hit").forEach(hit => { const i = +hit.dataset.i, r = rows[i];
      hit.dataset.tip = `<b>${esc(r.label)}</b><br>` + series.map(se => `<span class="tdot" style="background:${se.color}"></span> ${esc(se.name)}: ${fmtV(r[se.key] || 0)}`).join("<br>") + (tipX ? tipX(r) : "");
      hit.onpointerenter = () => svg.querySelectorAll("path.bar").forEach(p => p.style.opacity = +p.dataset.i === i ? 1 : .35);
      hit.onpointerleave = () => svg.querySelectorAll("path.bar").forEach(p => p.style.opacity = "");
      if (onClick) { hit.style.cursor = "pointer"; hit.onclick = () => onClick(r, i); } });
  });
}
function hbars(items, {onClick, max, color} = {}) {
  const m = max || Math.max(1, ...items.map(x => x[1])), wrap = document.createElement("div"); wrap.className = "hbars";
  items.forEach(([label, n, extra], i) => {
    const row = document.createElement("div"); row.className = "hb" + (onClick ? " click" : "");
    row.innerHTML = `<div class="l">${esc(label)}</div><div class="tr"><div class="f" style="--i:${i};width:${(n / m * 100).toFixed(1)}%${color ? `;background:${color}` : ""}"></div></div><div class="n">${fmt(n)}</div>`;
    row.dataset.tip = `<b>${esc(label)}</b><br>${fmt(n)}${extra ? `<br><span class="m">${esc(extra)}</span>` : ""}`;
    if (onClick) row.onclick = () => onClick(label, i);
    wrap.appendChild(row);
  });
  return wrap;
}
// smooth curve (Catmull-Rom to Bézier)
function smoothPath(pts) { if (pts.length < 2) return pts.length ? `M${pts[0][0]},${pts[0][1]}` : "";
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) { const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    d += ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}`; }
  return d; }
function spark(vals, {w = 120, h = 30, color = "#5b8cff", fill = true, dot = true, max} = {}) {
  if (!vals.length) return `<svg class="spark" height="${h}"></svg>`;
  const mx = max ?? Math.max(...vals), mn = Math.min(0, ...vals), sx = w / Math.max(1, vals.length - 1), y = v => h - 3 - (h - 6) * ((v - mn) / ((mx - mn) || 1));
  const pts = vals.map((v, i) => [i * sx, y(v)]), d = smoothPath(pts), id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" height="${h}" preserveAspectRatio="none"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    ${fill ? `<path d="${d} L${w},${h} L0,${h} Z" fill="url(#${id})" class="fadein"/>` : ""}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke" class="draw" pathLength="1" style="--len:1"/>
    ${dot ? `<circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="2.6" fill="${color}" class="fadein" style="--d0:900ms"/>` : ""}</svg>`;
}
// area / line chart: one series, hover line and tip, optional reference line
function area(el, rows, {key, color = "#5b8cff", h = 170, fmtV = fmt, ref, yMax, onClick, label = r => r.label, tipX} = {}) {
  el.classList.add("chart");
  if (!rows.length) { el.innerHTML = `<div class="empty">${esc(T("c.no_data"))}</div>`; return; }
  sized(el, W => {
    const padL = 34, padB = 20, padT = 10, H = h, vals = rows.map(r => r[key]), defined = vals.filter(v => v != null);
    const mx = yMax || niceMax(Math.max(1e-9, ...defined, ref ? ref.v : 0)), sx = (W - padL - 8) / Math.max(1, rows.length - 1);
    const X = i => padL + 4 + i * sx, Y = v => padT + (H - padT - padB) * (1 - v / mx);
    const pts = rows.map((r, i) => r[key] == null ? null : [X(i), Y(r[key])]).filter(Boolean), d = smoothPath(pts), id = "a" + Math.random().toString(36).slice(2, 8);
    let s = `<svg height="${H}" viewBox="0 0 ${W} ${H}"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".32"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
    for (const t of [0, .5, 1]) s += `<line class="gl" x1="${padL}" x2="${W}" y1="${Y(mx * t)}" y2="${Y(mx * t)}"/><text x="${padL - 6}" y="${Y(mx * t) + 3.5}" text-anchor="end">${fmtV(mx * t)}</text>`;
    if (pts.length > 1) s += `<path d="${d} L${pts[pts.length - 1][0]},${Y(0)} L${pts[0][0]},${Y(0)} Z" fill="url(#${id})" class="fadein"/>`;
    if (ref) s += `<line x1="${padL}" x2="${W}" y1="${Y(ref.v)}" y2="${Y(ref.v)}" stroke="${ref.color || "var(--accent)"}" stroke-dasharray="5 4" stroke-width="1.2" opacity=".85"/><text x="${W - 4}" y="${Y(ref.v) - 6}" text-anchor="end" style="fill:${ref.color || "var(--accent)"}">${esc(ref.label)}</text>`;
    s += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" class="draw" pathLength="1" style="--len:1"/>`;
    pts.forEach((p, i) => { if (pts.length < 40) s += `<circle cx="${p[0]}" cy="${p[1]}" r="2.6" fill="${color}" class="fadein" style="--d0:${600 + i * 20}ms"/>`; });
    rows.forEach((r, i) => { if (xLab(i, rows.length)) s += `<text x="${X(i)}" y="${H - 5}" text-anchor="middle">${esc(String(label(r)).slice(5))}</text>`; });
    s += `<line class="hover-line" x1="0" x2="0" y1="${padT}" y2="${Y(0)}" opacity="0"/><circle class="hover-dot" r="4.5" fill="${color}" stroke="#06080c" stroke-width="2" opacity="0"/>`;
    s += `<rect class="hit" x="${padL}" y="0" width="${W - padL}" height="${H}"/></svg>`;
    el.innerHTML = s;
    const svg = el.querySelector("svg"), hl = svg.querySelector(".hover-line"), hd = svg.querySelector(".hover-dot"), hit = svg.querySelector(".hit");
    const at = e => Math.max(0, Math.min(rows.length - 1, Math.round((e.clientX - svg.getBoundingClientRect().left - padL - 4) / sx)));
    hit.onpointermove = e => { const i = at(e), r = rows[i]; hl.setAttribute("x1", X(i)); hl.setAttribute("x2", X(i)); hl.setAttribute("opacity", 1);
      if (r[key] != null) { hd.setAttribute("cx", X(i)); hd.setAttribute("cy", Y(r[key])); hd.setAttribute("opacity", 1); } else hd.setAttribute("opacity", 0);
      Tip.show(`<b>${esc(label(r))}</b><br>${r[key] == null ? esc(T("c.no_data")) : fmtV(r[key])}${tipX ? tipX(r) : ""}`, e.clientX, e.clientY, svg); };
    hit.onpointerleave = () => { hl.setAttribute("opacity", 0); hd.setAttribute("opacity", 0); Tip.hide(); };
    if (onClick) { hit.style.cursor = "pointer"; hit.onclick = e => onClick(rows[at(e)]); }
  });
}
// bullet graph: the value bar, a reference line, a warning zone (Stephen Few)
function bullet(v, max, {ref, refLabel, warnFrom, color = "var(--blue-mark)", fmtV = x => x} = {}) {
  const w = x => Math.max(0, Math.min(100, x / max * 100)).toFixed(1) + "%";
  return `<div class="bullet" style="position:relative;height:12px;border-radius:4px;background:rgba(255,255,255,.05);overflow:hidden">
    ${warnFrom != null ? `<i style="position:absolute;left:${w(warnFrom)};right:0;top:0;bottom:0;background:repeating-linear-gradient(-45deg,rgba(240,163,94,.16) 0 4px,transparent 4px 8px)"></i>` : ""}
    <i class="bf" style="position:absolute;left:0;top:3px;bottom:3px;width:${w(v)};border-radius:3px;background:${color};transform-origin:left;animation:growX var(--sp-gentle-d) var(--sp-gentle-e) both"></i>
    ${ref != null ? `<i data-tip="${esc(refLabel || T("c.reference"))}: ${esc(fmtV(ref))}" style="position:absolute;left:${w(ref)};top:0;bottom:0;width:2px;background:var(--ink);opacity:.8"></i>` : ""}</div>`;
}

// ================= pages: registration and switching (View Transitions: the old page blurs out, the new one springs in)
const VIEWS = {}, ORDER = ["overview", "inbox", "knowledge", "tasks", "usage", "quality", "profile"];
let CUR = null;
function go(name, params = {}) { const q = new URLSearchParams(params).toString(); location.hash = "#/" + name + (q ? "?" + q : ""); }
function parseHash() { const h = location.hash.replace(/^#\/?/, ""), [name, qs] = h.split("?"); return {name: VIEWS[name] ? name : "overview", params: Object.fromEntries(new URLSearchParams(qs || ""))}; }
function moveInd(name, now) {
  const a = $(`#nav a[data-v="${name}"]`), ind = $("#ind"); if (!a) return; const x = a.offsetLeft, w = a.offsetWidth;
  const from = {left: ind.style.left || x + "px", width: ind.style.width || w + "px"}; ind.style.left = x + "px"; ind.style.width = w + "px";
  if (!now) ind.animate([from, {left: x + "px", width: w + "px"}], {duration: SP.bouncy.ms, easing: SP.bouncy.easing}); $$("#nav a").forEach(x => x.classList.toggle("on", x === a));
}
async function route() {
  const {name, params} = parseHash();
  if (CUR && CUR.name === name) { VIEWS[name].update && VIEWS[name].update($("#v-" + name), params); CUR.params = params; return; }
  const prev = CUR; CUR = {name, params};
  document.documentElement.style.setProperty("--vtdir", prev ? (ORDER.indexOf(name) >= ORDER.indexOf(prev.name) ? 1 : -1) : 1);
  document.title = `${VIEWS[name].title} · MemexOS`;
  moveInd(name, !prev); closeDrawer(); Tip.hide();
  const swap = () => {
    if (prev) { VIEWS[prev.name].leave && VIEWS[prev.name].leave($("#v-" + prev.name)); $("#v-" + prev.name).hidden = true; }
    const el = $("#v-" + name); el.hidden = false; el.classList.toggle("fit", !!VIEWS[name].fit);
    if (!el.dataset.inited) { el.dataset.inited = 1; VIEWS[name].init(el, params); }
    VIEWS[name].enter && VIEWS[name].enter(el, params);
  };
  if (prev && document.startViewTransition && !reduce) document.startViewTransition(swap); else swap();
}
addEventListener("hashchange", route);
// text widths change once fonts load or the window resizes: realign the nav highlight
new ResizeObserver(() => CUR && moveInd(CUR.name, true)).observe($("#nav"));
document.fonts && document.fonts.addEventListener && document.fonts.addEventListener("loadingdone", () => CUR && moveInd(CUR.name, true));
$("#nav").addEventListener("click", e => { const a = e.target.closest("a[data-v]"); if (a) { e.preventDefault(); go(a.dataset.v); } });
$("#nav").addEventListener("pointerover", e => { const a = e.target.closest("a[data-v]"), h = $("#hov"); if (!a) return; const x = a.offsetLeft, w = a.offsetWidth;
  const from = {left: h.style.left || x + "px", width: h.style.width || w + "px"}; h.style.left = x + "px"; h.style.width = w + "px";
  h.animate([from, {left: x + "px", width: w + "px"}], {duration: SP.snappy.ms, easing: SP.snappy.easing}); });
$(".brand").onclick = () => go("overview");
if (!BOOT.profile) { const p = $('#nav a[data-v="profile"]'); if (p) p.remove(); }

// ================= live data: every 3 seconds; the top bar's system state, inbox count and clock
const LIVE = {data: null, subs: new Set()};
const onLive = f => { LIVE.subs.add(f); if (LIVE.data) f(LIVE.data); };
async function pollLive() {
  try { const L = await api("/api/live"); if (L.error) throw new Error(L.error); LIVE.data = L; renderHeader(L); LIVE.subs.forEach(f => { try { f(L); } catch (e) { console.error(e); } }); }
  catch (e) { $("#sys").innerHTML = `<i class="dot crit"></i>${esc(T("top.no_server"))}`; }
}
const stageName = id => T("stage." + id);
function stageDetail(s) { return (s.jobs || []).length ? list([...new Set(s.jobs)].map(jobName)) : ""; }
function sysState(L) {
  const act = L.stages.filter(s => s.active);
  if (L.alerts.length) return {cls: "crit", text: T("top.attention"), tip: L.alerts.map(esc).join("<br>")};
  if (L.paused) return {cls: "warn", text: T("top.paused"), tip: esc(T("top.paused_tip"))};
  if (act.length) return {cls: "run", text: T("top.running", {what: act.map(s => stageName(s.id)).join(" · ")}), tip: (L.doing || []).map(esc).join("<br>")};
  return {cls: "ok", text: T("top.idle"), tip: esc(L.state)};
}
function renderHeader(L) {
  const s = sysState(L), el = $("#sys");
  el.dataset.tip = s.tip; const key = s.cls + s.text;
  if (el.dataset.k !== key) { el.dataset.k = key; el.innerHTML = `<i class="dot ${s.cls}"></i><span>${esc(s.text)}</span>`; anim(el, [{transform: "scale(.94)", opacity: .4}, {transform: "none", opacity: 1}], "bouncy"); }
  const n = (L.inbox || {}).decide || 0;
  $("#ibx-chip").hidden = !n; tick($("#ibx-n"), n);
  const b = $('#nav a[data-v="inbox"] .bdg'); if (b) { b.hidden = !n; b.textContent = n; }
}
function clock() { const d = new Date(); $("#clock").textContent = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; }
setInterval(clock, 1000); clock();
setInterval(() => { if (!document.hidden) pollLive(); }, 3000);

// ================= graph data (shared by the globe, the knowledge page and the palette; fetched once)
let _graph = null; const getGraph = () => _graph || (_graph = api("/api/graph"));

// ================= command palette ⌘K: go to a page, find a page, run an action
const CMDS = () => [
  ...ORDER.filter(v => VIEWS[v] && (v !== "profile" || BOOT.profile)).map(v => ({t: T("cmd.go", {page: VIEWS[v].title}), k: T("cmd.k_page"), run: () => go(v)})),
  {t: T("act.ingest"), k: T("cmd.k_action"), run: e => doAction(e, "ingest", {}, {title: T("act.ingest"), confirm: esc(T("act.ingest_confirm"))})},
  {t: T("act.intake"), k: T("cmd.k_action"), run: e => doAction(e, "intake", {}, {title: T("act.intake"), confirm: esc(T("act.intake_confirm"))})},
  {t: T("act.pause"), k: T("cmd.k_action"), run: e => doAction(e, "pause", {}, {title: T("act.pause"), confirm: esc(T("act.pause_confirm"))})},
  {t: T("act.resume"), k: T("cmd.k_action"), run: e => doAction(e, "resume", {}, {title: T("act.resume")})},
  ...((LIVE.data || {}).logins || []).map(s => ({t: T("act.login", {site: s.name}), k: T("cmd.k_action"),
    run: e => doAction(e, "login", {site: s.id}, {title: T("act.login", {site: s.name}), confirm: esc(T("act.login_confirm", {site: s.name}))})})),
  {t: T("act.inbox_apply"), k: T("cmd.k_action"), run: e => doAction(e, "inbox-apply", {}, {title: T("act.inbox_apply"), confirm: esc(T("act.inbox_apply_confirm"))})},
];
const Cmdk = {open: false, items: [], idx: 0,
  async show() { if (this.open) return; this.open = true; Scrim.on(); const c = $("#cmdk"); c.classList.add("on"); const i = $("#cmdk input"); i.value = ""; i.focus(); this.render("");
    const G = await getGraph(); this.nodes = G.nodes; if (this.open) this.render(i.value); },
  hide() { if (!this.open) return; this.open = false; $("#cmdk").classList.remove("on"); Scrim.off(); },
  render(q) {
    q = q.trim().toLowerCase();
    const cmds = CMDS().filter(c => !q || c.t.toLowerCase().includes(q)).slice(0, q ? 6 : 12);
    const pages = q && this.nodes ? this.nodes.filter(n => n.name.toLowerCase().includes(q)).sort((a, b) => a.layer - b.layer || (b.in || 0) - (a.in || 0)).slice(0, 10) : [];
    this.items = [...cmds.map(c => ({...c, g: T("cmd.g_actions")})), ...pages.map(n => ({t: n.name, k: typeName(n.type), g: T("cmd.g_pages"), node: n, run: () => openPage(n.id)})),
      ...(q ? [{t: T("cmd.search", {q}), k: T("cmd.k_search"), g: T("cmd.g_search"), run: () => go("knowledge", {search: q})}] : [])];
    this.idx = 0; let g = "";
    $("#cmdk .res").innerHTML = this.items.map((it, i) => { const h = it.g !== g ? `<div class="grp">${esc(it.g)}</div>` : ""; g = it.g;
      return h + `<div class="ci${i === 0 ? " on" : ""}" data-i="${i}">${it.node ? `<i class="tdot" style="background:${TYPE_COL[it.node.type]}"></i>` : ""}<span>${esc(it.t)}</span><span class="tp">${esc(it.k)}</span></div>`; }).join("") || `<div class="empty">${esc(T("c.no_match"))}</div>`;
  },
  pick(i, e) { const it = this.items[i]; if (!it) return; this.hide(); setTimeout(() => it.run(e), 60); },
  hl(i) { this.idx = (i + this.items.length) % this.items.length; $$("#cmdk .ci").forEach(x => x.classList.toggle("on", +x.dataset.i === this.idx)); const on = $("#cmdk .ci.on"); on && on.scrollIntoView({block: "nearest"}); }};
$("#cmdk input").addEventListener("input", e => Cmdk.render(e.target.value));
$("#cmdk input").addEventListener("keydown", e => { if (e.key === "ArrowDown") { e.preventDefault(); Cmdk.hl(Cmdk.idx + 1); } else if (e.key === "ArrowUp") { e.preventDefault(); Cmdk.hl(Cmdk.idx - 1); }
  else if (e.key === "Enter") { e.preventDefault(); const on = $("#cmdk .ci.on"); if (!on) return; const r = on.getBoundingClientRect(); Cmdk.pick(Cmdk.idx, {clientX: r.left + 40, clientY: r.top + 10}); } });
$("#cmdk .res").addEventListener("click", e => { const c = e.target.closest(".ci"); if (c) Cmdk.pick(+c.dataset.i, e); });
$("#cmdk-btn").onclick = () => Cmdk.show();
// settings: the languages come from the server (BOOT.langs = i18n.available()), never a list kept here
const LANG_NAME = {en: "English", es: "Español", zh: "中文"};
$("#set-btn").onclick = async e => {
  const langs = (BOOT.langs && BOOT.langs.length) ? BOOT.langs : [LANG];
  const pick = await modal({
    title: T("set.title"),
    html: `<div class="muted" style="margin-bottom:10px">${esc(T("set.language_hint"))}</div>`,
    actions: [...langs.map(l => ({label: LANG_NAME[l] || l, primary: l === LANG, value: l})),
              {label: T("c.close"), value: null}],
    origin: e,
  });
  if (!pick || pick === LANG) return;
  const r = await doAction(e, "set-language", {lang: pick}, {title: T("set.language")});
  if (r && r.ok) location.reload();
};
$("#scrim").addEventListener("click", () => { if (Cmdk.open) Cmdk.hide(); else if ($("#drawer").classList.contains("on")) closeDrawer(); });
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); Cmdk.open ? Cmdk.hide() : Cmdk.show(); return; }
  if (e.key === "Escape") { if (Cmdk.open) { Cmdk.hide(); e.stopImmediatePropagation(); return; } if ($("#drawer").classList.contains("on")) { closeDrawer(); e.stopImmediatePropagation(); } }
}, true);
$("#d-close").onclick = closeDrawer;
$("#d-graph").onclick = () => { const id = $("#d-graph").dataset.id; closeDrawer(); go("overview", {focus: id}); };
$("#ibx-chip").onclick = () => go("inbox");
document.addEventListener("click", e => { const o = e.target.closest("[data-go-other]"); if (o) go(o.dataset.goOther); });
$("#sys").onclick = () => go("tasks");

// ================= runs (/api/runs): step names and colours, a run's title, status and tooltip
const STEP_COL = {collect: "--s-intake", admit: "--s-intake", capture: "--s-intake", visual: "--s-intake", research: "--s-intake", investigate: "--s-intake",
  classify: "--s-classify", triage: "--s-classify", write: "--s-write", rework: "--s-rework", selfcheck: "--s-check", usage: "--s-check", verify: "--s-check",
  gate2: "--s-gate2", index: "--s-classify", embed: "--s-classify", eval_write: "--s-check", eval_check: "--s-check", eval_judge: "--s-check",
  eval_search: "--s-check", eval_answer: "--s-write", eval_grade: "--s-gate2"};
const segName = s => stepName(s.step) + (s.step === "gate2" ? ` · ${T(s.data && s.data.mode === "ingest" ? "run.first_review" : "run.recheck")}` : "") +
  (s.step === "admit" && s.data && s.data.channel ? ` · ${s.data.channel}` : "") + (s.step === "rework" && s.data && s.data.reason ? ` · ${T("run.reason_" + s.data.reason)}` : "");
const segCol = s => cssv(STEP_COL[s.step] || "--mute");
const shortRaw = r => String(r).split("/").pop().replace(/^\d{4}-\d{2}-\d{2}-[a-z]+-[a-z0-9]+-/, "").replace(/\.md$/, "");
function runTitle(u) {
  if (u.job === "intake") return T("run.t_intake", {n: u.items ?? "—"});
  if (u.files && u.files.length) return u.files.map(x => shortRaw(x).slice(0, 26)).join(T("u.sep"));
  if (u.job === "fix") return T("run.t_fix", {n: u.problems ?? "—"});
  if (u.job === "inbox") return T("run.t_inbox");
  if (u.job === "lint") return T("run.t_lint");
  if (u.subject) return u.subject;
  return jobName(u.job);
}
const RUN_ST = {published: ["✓", "ok"], done: ["✓", "ok"], running: ["▸", "run"], failed: ["✕", "crit"], held: ["‖", "warn"], interrupted: ["!", "warn"], discarded: ["↺", "warn"]};
const runStatus = u => [...(RUN_ST[u.status] || ["?", ""]), T("run.st_" + u.status)];
function g2Summary(u) { return (u.gate2 || []).map(g => `${T(g.mode === "ingest" ? "run.first_review" : "run.recheck")}${g.passed === true ? " ✓" : g.passed === false ? " ✕" : " …"}${g.model ? ` (${g.model}${g.cost_usd ? " " + money(g.cost_usd) : ""})` : ""}`).join(" → "); }
function runTip(u) {
  const st = runStatus(u);
  return `<b>${esc(jobName(u.job))} · ${esc(runTitle(u).slice(0, 80))}</b><br><span class="m">${mdhm(u.t0)} → ${u.t1 ? hm(u.t1) : esc(T("run.ongoing"))} · ${dur((u.t1 || Date.now() / 1000) - u.t0)} · ${esc(st[2])}</span>` +
    `<br>${u.segs.map(s => `<span class="tdot" style="background:${segCol(s)}"></span> ${esc(segName(s))} ${s.t1 ? dur(s.t1 - s.t0) : esc(T("run.ongoing"))}${s.step === "gate2" && s.data ? (s.data.result === "passed" ? " ✓" : s.data.result === "failed" ? " ✕" : "") : ""}`).join("<br>")}` +
    (u.cost ? `<br>${esc(T("run.cost", {c: money(u.cost)}))}` : "") + `<br><span class="h">${esc(T("run.click"))}</span>`;
}
