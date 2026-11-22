/* Interactive SVG charts for the quote page. No chart library: every mark is
   drawn from the engine's calculation archive, with a hover layer on each mark. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const C = {
    ink: "#f0efec", ink2: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835",
    blue: "#3987e5", orange: "#d95926", aqua: "#199e70", yellow: "#c98500", red: "#e0524b",
    accent: "#9085e9", card: "#111113",
  };

  // ---------------------------------------------------------------- format
  const fmt = {
    money(x) { return "$" + Math.round(x).toLocaleString("en-US"); },
    short(x) {
      const a = Math.abs(x);
      if (a >= 1e6) return "$" + (x / 1e6).toFixed(a >= 1e7 ? 1 : 2) + "M";
      if (a >= 1e3) return "$" + Math.round(x / 1e3) + "k";
      return "$" + Math.round(x);
    },
    pct(x, d = 1) { return (x * 100).toFixed(d) + "%"; },
    num(x) {
      if (x === null || x === undefined) return "n/a";
      const a = Math.abs(x);
      if (a >= 1000) return Math.round(x).toLocaleString("en-US");
      if (a >= 1) return (+x.toPrecision(3)).toString();
      return (+x.toPrecision(3)).toString();
    },
    esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); },
  };

  const LABELS = {
    "severity.body.median": "claim size (median)", "severity.body.p90": "claim size (P90)",
    "severity.tail.prob": "large-claim share", "severity.tail.alpha": "large-claim tail shape",
    "frequency.p_error": "error rate", "frequency.p_harm_given_error": "P(harm | error)",
    "frequency.p_claim_given_harm": "P(claim | harm)", "frequency.adversarial_to_live": "adversarial-to-live ratio",
    "frequency.rate": "claim rate", "frequency.leak_rate": "leak rate on probes",
    "frequency.reference_leak_rate": "typical leak rate", "annual_prob": "event probability",
    "share_affected": "share of insureds hit", "loss_share_of_limit": "loss per hit (share of limit)",
  };
  function label(path) {
    const p = path.split(".");
    if (p[0] === "systemic") return "systemic: " + (LABELS[p[1]] || p[1].replace(/_/g, " "));
    const rest = p.slice(2).join(".");
    return p[1].replace(/_/g, " ") + ": " + (LABELS[rest] || rest.replace(/_/g, " "));
  }

  // ---------------------------------------------------------------- tooltip
  const tip = document.getElementById("tooltip");
  function showTip(evt, html) {
    tip.innerHTML = html;
    tip.classList.add("on");
    moveTip(evt);
  }
  function moveTip(evt) {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  function hideTip() { tip.classList.remove("on"); }

  // ---------------------------------------------------------------- svg helpers
  function el(tag, attrs = {}, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }
  function svg(container, w, h, title) {
    container.innerHTML = "";
    const s = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": title });
    container.appendChild(s);
    return s;
  }
  function text(parent, x, y, s, o = {}) {
    const t = el("text", {
      x, y, fill: o.fill || C.muted, "font-size": o.size || 11, "text-anchor": o.anchor || "start",
      "font-weight": o.weight || 400, "dominant-baseline": o.baseline || "auto",
    }, parent);
    t.textContent = s;
    return t;
  }
  // Bar with 4px rounded data end, square at the baseline.
  function bar(parent, x, y, w, h, fill, dir = "up", r = 4) {
    w = Math.max(w, 1); h = Math.max(h, 1); r = Math.min(r, w / 2, h / 2);
    let d;
    if (dir === "up") d = `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
    else if (dir === "right") d = `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
    else d = `M${x + w},${y}H${x + r}Q${x},${y} ${x},${y + r}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w}Z`;
    return el("path", { d, fill, class: "mark" }, parent);
  }
  function hover(target, html, group, mark) {
    target.classList.add("hit");
    target.addEventListener("pointerenter", e => {
      showTip(e, typeof html === "function" ? html() : html);
      if (group) { group.classList.add("dim"); (mark ? [].concat(mark) : []).forEach(m => m.classList.add("on")); }
    });
    target.addEventListener("pointermove", moveTip);
    target.addEventListener("pointerleave", () => {
      hideTip();
      if (group) { group.classList.remove("dim"); (mark ? [].concat(mark) : []).forEach(m => m.classList.remove("on")); }
    });
  }
  const step = id => (calc => calc.premium_build_up.find(s => s.id === id));

  // ---------------------------------------------------------------- waterfall
  function waterfall(container, calc, equations) {
    const S = id => step(id)(calc);
    const el_ = S("expected_loss").value, u = S("uncertainty_load").value, k = S("capital_load").value,
      f = S("fixed_expense").value, tech = S("technical_premium").value, g = S("gross_premium").value;
    const gi = S("gross_premium").inputs, ci = S("capital_load").inputs, ui = S("uncertainty_load").inputs;
    const eq = id => (equations.find(e => e.id === id) || {}).formula || "";
    const items = [
      { k: "Expected loss", v: el_, from: 0, color: C.blue, eq: eq("expected_loss"),
        rows: [["Idiosyncratic", fmt.money(S("expected_loss").inputs.idiosyncratic)], ["Systemic", fmt.money(S("expected_loss").inputs.systemic)]] },
      { k: "Uncertainty load", v: u, from: el_, color: C.accent, eq: eq("uncertainty_load"),
        rows: [[`P${ui.percentile} of EL across draws`, fmt.money(ui.expected_loss_at_percentile)], ["minus mean EL", fmt.money(ui.expected_loss)]] },
      { k: "Capital load", v: k, from: el_ + u, color: C.aqua, eq: eq("capital_load"),
        rows: [["Risk capital", fmt.money(ci.capital)], ["Cost of capital", fmt.pct(ci.cost_of_capital, 0)]] },
      { k: "Fixed expense", v: f, from: el_ + u + k, color: C.yellow, eq: "Underwriting cost incl. red-team testing", rows: [] },
      { k: "Expenses & profit", v: g - tech, from: tech, color: C.orange, eq: eq("gross_premium"),
        rows: [["Technical premium", fmt.money(tech)], ["Variable expenses", fmt.pct(gi.variable_ratio, 0)], ["Profit margin", fmt.pct(gi.profit_margin, 0)], ["Divide by", gi.divisor.toFixed(2)]] },
      { k: "Gross premium", v: g, from: 0, color: C.ink, total: true, eq: "", rows: [["Rate on line", fmt.pct(S("rate_on_line").value)]] },
    ];
    const W = 560, H = 300, L = 54, R = 10, T = 26, B = 46, pw = W - L - R, ph = H - T - B;
    const s = svg(container, W, H, "Premium build-up waterfall");
    const g0 = el("g", {}, s);
    const y = v => T + ph * (1 - v / (g * 1.08));
    for (let i = 0; i <= 4; i++) {
      const v = g * 1.08 * i / 4;
      el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: C.grid }, g0);
      text(g0, L - 8, y(v) + 4, fmt.short(v), { anchor: "end" });
    }
    const slot = pw / items.length, bw = Math.min(56, slot * 0.62);
    items.forEach((it, i) => {
      const x = L + slot * i + (slot - bw) / 2;
      const top = y(it.from + it.v), h = y(it.from) - top;
      const m = bar(g0, x, top, bw, h, it.color, "up");
      if (it.total) m.setAttribute("opacity", "0.92");
      if (i < items.length - 1) {
        const nx = L + slot * (i + 1) + (slot - bw) / 2;
        el("line", { x1: x + bw, x2: nx, y1: top, y2: top, stroke: C.axis, "stroke-dasharray": "3 3" }, g0);
      }
      text(g0, x + bw / 2, top - 7, fmt.short(it.v), { anchor: "middle", fill: C.ink, size: 11.5, weight: 600 });
      const words = it.k.split(" ");
      text(g0, x + bw / 2, T + ph + 17, words[0], { anchor: "middle", fill: C.ink2 });
      if (words[1]) text(g0, x + bw / 2, T + ph + 31, words.slice(1).join(" "), { anchor: "middle", fill: C.ink2 });
      const hit = el("rect", { x: L + slot * i, y: T, width: slot, height: ph, fill: "transparent" }, g0);
      hover(hit, () => `<div class="tt-title">${fmt.esc(it.k)}</div><div class="tt-big">${fmt.money(it.v)}</div>
        <div class="muted small">${fmt.pct(it.v / g, 0)} of the gross premium</div>
        ${it.rows.map(r => `<div class="tt-row"><span>${fmt.esc(r[0])}</span><b>${fmt.esc(r[1])}</b></div>`).join("")}
        ${it.eq ? `<div class="tt-eq">${fmt.esc(it.eq)}</div>` : ""}`, g0, m);
    });
    el("line", { x1: L, x2: W - R, y1: T + ph, y2: T + ph, stroke: C.axis }, g0);
  }

  // ---------------------------------------------------------------- loss distribution
  function loss(container, calc, highlight) {
    const ld = calc.loss_distribution, h = ld.histogram, n = h.n_years, edges = h.edges, counts = h.counts;
    const agg = edges[edges.length - 1], perClaim = calc.policy.limit_per_claim;
    const shares = counts.map(c => c / n);
    let cum = h.zero_years / n; const cums = shares.map(s => (cum += s));
    const W = 560, H = 300, L = 48, R = 12, T = 30, B = 52, pw = W - L - R, ph = H - T - B;
    const top = Math.max(...shares, 0.001) * 1.18;
    const s = svg(container, W, H, "Annual loss distribution");
    const g0 = el("g", {}, s);
    const x = v => L + pw * v / agg, y = v => T + ph * (1 - v / top);
    for (let i = 0; i <= 4; i++) {
      const v = top * i / 4;
      el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: C.grid }, g0);
      text(g0, L - 8, y(v) + 4, (v * 100).toFixed(v < 0.1 ? 1 : 0) + "%", { anchor: "end" });
    }
    const bw = pw / counts.length;
    const bars = [];
    shares.forEach((sh, i) => {
      const bx = L + i * bw + 1, mk = sh > 0 ? bar(g0, bx, y(sh), bw - 2, T + ph - y(sh), C.blue, "up", 3) : null;
      bars.push(mk);
      const hit = el("rect", { x: L + i * bw, y: T, width: bw, height: ph, fill: "transparent" }, g0);
      hover(hit, () => `<div class="tt-title">${fmt.short(edges[i])} to ${fmt.short(edges[i + 1])}</div>
        <div class="tt-big">${fmt.pct(sh, 2)} <span class="muted small">of years</span></div>
        <div class="tt-row"><span>Years with loss ≤ ${fmt.short(edges[i + 1])}</span><b>${fmt.pct(Math.min(cums[i], 1), 1)}</b></div>
        <div class="tt-row"><span>Simulated years in bin</span><b>${counts[i].toLocaleString()}</b></div>
        ${edges[i + 1] >= agg ? `<div class="tt-src">Includes years that exhaust the ${fmt.short(agg)} aggregate limit.</div>` : ""}
        ${edges[i] <= perClaim && perClaim < edges[i + 1] ? `<div class="tt-src">Spike: years with one claim capped at the ${fmt.short(perClaim)} per-claim limit.</div>` : ""}`,
        g0, mk ? [mk] : []);
    });
    el("line", { x1: L, x2: W - R, y1: T + ph, y2: T + ph, stroke: C.axis }, g0);
    [0, agg / 4, agg / 2, 3 * agg / 4, agg].forEach(v => text(g0, x(v), T + ph + 16, fmt.short(v), { anchor: "middle" }));
    if (perClaim < agg) {
      el("line", { x1: x(perClaim), x2: x(perClaim), y1: T, y2: T + ph, stroke: C.muted, "stroke-dasharray": "2 3", "pointer-events": "none" }, g0);
      text(g0, x(perClaim), T + ph + 30, "per-claim limit", { anchor: "middle", size: 10 });
    }
    text(g0, W - R, T + ph + 30, "aggregate limit", { anchor: "end", size: 10 });
    text(g0, L, H - 6, `${fmt.pct(h.zero_years / n)} of ${n.toLocaleString()} simulated years have no loss`, { size: 10.5 });

    const p = ld.percentiles;
    const marks = highlight
      ? [{ k: `P${highlight}`, v: p[highlight], c: C.accent }]
      : [{ k: "Expected", v: ld.expected_loss, c: C.yellow }, { k: "P90", v: p["90"], c: C.ink }, { k: "P99.5", v: p["99.5"], c: C.red }];
    marks.forEach((m, i) => {
      const xv = Math.min(x(m.v), W - R - 1);
      el("line", { x1: xv, x2: xv, y1: T - 4, y2: T + ph, stroke: m.c, "stroke-width": 2, "stroke-dasharray": "5 3", "pointer-events": "none" }, g0);
      const right = xv > W - R - 120;
      text(g0, xv + (right ? -6 : 6), T + 8 + i * 15, `${m.k} ${fmt.short(m.v)}`, { anchor: right ? "end" : "start", fill: C.ink, size: 11.5, weight: 600 });
    });
  }

  // ---------------------------------------------------------------- tornado
  function tornado(container, calc, topN) {
    const t = calc.tornado, bars = t.bars.slice(0, topN), base = t.base_premium;
    const W = 1080, row = 34, L = 300, R = 90, T = 10, H = T + row * bars.length + 34;
    let lo = Math.min(base, ...bars.map(b => Math.min(b.premium_at_low, b.premium_at_high)));
    let hi = Math.max(base, ...bars.map(b => Math.max(b.premium_at_low, b.premium_at_high)));
    const pad = (hi - lo) * 0.04 || 1; lo -= pad; hi += pad;
    const pw = W - L - R, x = v => L + pw * (v - lo) / (hi - lo);
    const s = svg(container, W, H, "Tornado chart");
    const g0 = el("g", {}, s);
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = lo + (hi - lo) * i / ticks;
      el("line", { x1: x(v), x2: x(v), y1: T, y2: T + row * bars.length, stroke: C.grid }, g0);
      text(g0, x(v), T + row * bars.length + 18, fmt.short(v), { anchor: "middle" });
    }
    bars.forEach((b, i) => {
      const yy = T + i * row, bh = row - 12, ms = [];
      text(g0, L - 12, yy + row / 2 + 4, label(b.parameter), { anchor: "end", fill: C.ink2, size: 12.5 });
      [[b.premium_at_low, C.blue], [b.premium_at_high, C.red]].forEach(([v, c]) => {
        const left = v < base;
        const x0 = left ? x(v) : x(base), w = Math.abs(x(v) - x(base));
        ms.push(bar(g0, x0 + (left ? 0 : 1), yy + 6, Math.max(w - 1, 1), bh, c, left ? "left" : "right"));
      });
      const right = Math.max(x(b.premium_at_low), x(b.premium_at_high));
      text(g0, right + 8, yy + row / 2 + 4, fmt.short(b.swing), { fill: C.ink2, size: 11.5 });
      const hit = el("rect", { x: 0, y: yy, width: W, height: row, fill: "transparent" }, g0);
      hover(hit, () => `<div class="tt-title">${fmt.esc(label(b.parameter))}</div>
        <div class="tt-row"><span>At P10 (${fmt.num(b.low_value)})</span><b>${fmt.money(b.premium_at_low)}</b></div>
        <div class="tt-row"><span>At P90 (${fmt.num(b.high_value)})</span><b>${fmt.money(b.premium_at_high)}</b></div>
        <div class="tt-row"><span>Swing</span><b>${fmt.money(b.swing)}</b></div>
        <div class="tt-src">${fmt.esc(b.source || "")}</div>
        <div class="tt-eq">${fmt.esc(b.parameter)}</div>`, g0, ms);
    });
    el("line", { x1: x(base), x2: x(base), y1: T - 4, y2: T + row * bars.length + 2, stroke: C.ink, "stroke-width": 1.5, "pointer-events": "none" }, g0);
  }

  // ---------------------------------------------------------------- controls
  function controls(container, calc) {
    const c = calc.controls, opts = c.options.slice(0, 8), base = c.base_premium;
    const lab = o => o.kind === "red_team_remediation" ? "Fix red-team failures, re-test"
      : `${o.factor.replace(/_/g, " ")}: ${o.to_level.replace(/_/g, " ")}`;
    const rows = [{ name: "Current", v: base, cur: true }].concat(opts.map(o => ({ name: lab(o), v: o.premium, o })));
    const W = 1080, row = 34, L = 300, R = 150, T = 6, H = T + row * rows.length + 8;
    const pw = W - L - R, x = v => L + pw * v / base;
    const s = svg(container, W, H, "Premium after each change");
    const g0 = el("g", {}, s);
    rows.forEach((r, i) => {
      const yy = T + i * row;
      text(g0, L - 12, yy + row / 2 + 4, r.name, { anchor: "end", fill: r.cur ? C.ink : C.ink2, size: 12.5, weight: r.cur ? 600 : 400 });
      const m = bar(g0, L, yy + 6, x(r.v) - L, row - 12, r.cur ? "#5b5a56" : C.aqua, "right");
      const note = r.o ? `  −${r.o.saving_pct < 0.005 ? "<1%" : fmt.pct(r.o.saving_pct, 0)}` : "";
      text(g0, x(r.v) + 8, yy + row / 2 + 4, fmt.short(r.v) + note, { fill: C.ink2, size: 11.5 });
      const hit = el("rect", { x: 0, y: yy, width: W, height: row, fill: "transparent" }, g0);
      hover(hit, () => r.cur ? `<div class="tt-title">Current premium</div><div class="tt-big">${fmt.money(base)}</div>` :
        `<div class="tt-title">${fmt.esc(r.name)}</div>
         <div class="tt-row"><span>From</span><b>${fmt.esc(String(r.o.from_level).replace(/_/g, " "))}</b></div>
         <div class="tt-row"><span>Premium</span><b>${fmt.money(r.o.premium)}</b></div>
         <div class="tt-row"><span>Saving</span><b>${fmt.money(r.o.saving)} (${fmt.pct(r.o.saving_pct, 0)})</b></div>
         ${r.o.to_relativity !== undefined ? `<div class="tt-row"><span>Relativity</span><b>×${r.o.from_relativity} → ×${r.o.to_relativity}</b></div>` : ""}
         <div class="tt-src">${fmt.esc(r.o.rationale)}</div>`, g0, m);
    });
  }

  // ---------------------------------------------------------------- red team
  function redteam(container, summary) {
    const cats = Object.entries(summary.by_category);
    const W = 560, row = 46, L = 150, R = 70, T = 8, H = T + row * cats.length + 10, pw = W - L - R;
    const s = svg(container, W, H, "Red-team failures by category");
    const g0 = el("g", {}, s);
    cats.forEach(([cat, v], i) => {
      const yy = T + i * row, bh = 18, f = v.tested ? v.failures / v.tested : 0;
      text(g0, L - 12, yy + 22, cat.replace(/_/g, " "), { anchor: "end", fill: C.ink2, size: 12.5 });
      const ms = [];
      if (v.failures) ms.push(bar(g0, L, yy + 9, pw * f - (f < 1 ? 1 : 0), bh, C.orange, f < 1 ? "left" : "right", f < 1 ? 0 : 4));
      if (v.failures < v.tested) ms.push(bar(g0, L + pw * f + (f > 0 ? 1 : 0), yy + 9, pw * (1 - f) - (f > 0 ? 1 : 0), bh, "#3a3936", "right"));
      text(g0, W - R + 10, yy + 22, `${v.failures} / ${v.tested}`, { fill: C.ink, size: 12.5, weight: 600 });
      const hit = el("rect", { x: 0, y: yy, width: W, height: row, fill: "transparent" }, g0);
      hover(hit, `<div class="tt-title">${fmt.esc(cat.replace(/_/g, " "))}</div>
        <div class="tt-big">${fmt.pct(f, 0)} <span class="muted small">failed</span></div>
        <div class="tt-row"><span>Material failures</span><b>${v.failures}</b></div>
        <div class="tt-row"><span>Cases tested</span><b>${v.tested}</b></div>`, g0, ms);
    });
  }

  // ---------------------------------------------------------------- credibility
  function credCurve(container, c) {
    const W = 560, H = 260, L = 44, R = 16, T = 18, B = 40, pw = W - L - R, ph = H - T - B;
    const nMax = Math.max(120, Math.ceil(c.n * 3 / 20) * 20);
    const x = n => L + pw * n / nMax, y = z => T + ph * (1 - z);
    const s = svg(container, W, H, "Credibility Z versus number of tests");
    const g0 = el("g", {}, s);
    [0, 0.25, 0.5, 0.75, 1].forEach(z => {
      el("line", { x1: L, x2: W - R, y1: y(z), y2: y(z), stroke: C.grid }, g0);
      text(g0, L - 8, y(z) + 4, z.toFixed(2), { anchor: "end" });
    });
    for (let i = 0; i <= 4; i++) text(g0, x(nMax * i / 4), T + ph + 16, String(Math.round(nMax * i / 4)), { anchor: "middle" });
    text(g0, L + pw / 2, H - 4, "cases tested (n)", { anchor: "middle", size: 10.5 });
    let d = "";
    for (let i = 0; i <= 200; i++) { const n = nMax * i / 200; d += (i ? "L" : "M") + x(n).toFixed(1) + "," + y(n / (n + c.k)).toFixed(1); }
    el("path", { d, fill: "none", stroke: C.accent, "stroke-width": 2 }, g0);
    // half-credibility point: n = k
    el("line", { x1: x(c.k), x2: x(c.k), y1: y(0.5), y2: T + ph, stroke: C.muted, "stroke-dasharray": "2 3" }, g0);
    text(g0, x(c.k) + 4, T + ph - 6, `n = k = ${c.k.toFixed(1)}: Z = 0.5`, { size: 10 });
    el("circle", { cx: x(c.n), cy: y(c.z), r: 6, fill: C.ink, stroke: C.card, "stroke-width": 2 }, g0);
    text(g0, x(c.n) + 10, y(c.z) - 8, `this system: n = ${c.n}, Z = ${c.z.toFixed(2)}`, { fill: C.ink, size: 11.5, weight: 600 });
    const guide = el("line", { y1: T, y2: T + ph, stroke: C.ink2, "stroke-width": 1, opacity: 0 }, g0);
    const dot = el("circle", { r: 4, fill: C.accent, opacity: 0 }, g0);
    const hit = el("rect", { x: L, y: T, width: pw, height: ph, fill: "transparent", class: "hit" }, g0);
    hit.addEventListener("pointermove", e => {
      const box = s.getBoundingClientRect(), px = (e.clientX - box.left) * W / box.width;
      const n = Math.max(0, Math.min(nMax, (px - L) / pw * nMax)), z = n / (n + c.k);
      guide.setAttribute("x1", x(n)); guide.setAttribute("x2", x(n)); guide.setAttribute("opacity", 0.5);
      dot.setAttribute("cx", x(n)); dot.setAttribute("cy", y(z)); dot.setAttribute("opacity", 1);
      showTip(e, `<div class="tt-title">${Math.round(n)} cases tested</div><div class="tt-big">Z = ${z.toFixed(2)}</div>
        <div class="muted small">${(z * 100).toFixed(0)}% weight on the system's own result, ${((1 - z) * 100).toFixed(0)}% on the portfolio mean</div>
        <div class="tt-eq">Z = n / (n + k), k = ${c.k.toFixed(2)}</div>`);
    });
    hit.addEventListener("pointerleave", () => { hideTip(); guide.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); });
  }

  function shrinkage(container, c, selfLabel) {
    const rows = c.portfolio.map(g => ({ id: g.insured_id, n: g.n, obs: g.observed, est: g.estimate, z: g.z, self: false }))
      .concat([{ id: selfLabel || "this system", n: c.n, obs: c.observed, est: c.estimate, z: c.z, self: true }]);
    const W = 560, H = 300, L = 110, R = 110, T = 30, B = 24, ph = H - T - B;
    const top = Math.min(1, Math.max(...rows.map(r => Math.max(r.obs, r.est))) * 1.1 || 1);
    const y = v => T + ph * (1 - v / top), xl = L, xr = W - R;
    const s = svg(container, W, H, "Credibility shrinkage toward the portfolio mean");
    const g0 = el("g", {}, s);
    text(g0, xl, 16, "observed rate", { anchor: "middle", fill: C.ink2 });
    text(g0, xr, 16, "credibility-weighted", { anchor: "middle", fill: C.ink2 });
    [0, top / 2, top].forEach(v => { text(g0, xl - 12, y(v) + 4, fmt.pct(v, 0), { anchor: "end" }); text(g0, xr + 12, y(v) + 4, fmt.pct(v, 0)); });
    el("line", { x1: xl, x2: xl, y1: T, y2: T + ph, stroke: C.axis }, g0);
    el("line", { x1: xr, x2: xr, y1: T, y2: T + ph, stroke: C.axis }, g0);
    el("line", { x1: xl, x2: xr, y1: y(c.mu), y2: y(c.mu), stroke: C.yellow, "stroke-dasharray": "5 3", "stroke-width": 1.5 }, g0);
    text(g0, (xl + xr) / 2, y(c.mu) - 6, `portfolio mean μ = ${fmt.pct(c.mu)}`, { anchor: "middle", fill: C.ink, size: 11 });
    rows.sort((a, b) => a.self - b.self).forEach(r => {
      const col = r.self ? C.accent : C.blue, w = r.self ? 3 : 1.5;
      const line = el("line", { x1: xl, x2: xr, y1: y(r.obs), y2: y(r.est), stroke: col, "stroke-width": w, class: "mark", opacity: r.self ? 1 : 0.7 }, g0);
      const a = el("circle", { cx: xl, cy: y(r.obs), r: r.self ? 6 : 4, fill: col, stroke: C.card, "stroke-width": 2, class: "mark" }, g0);
      const b = el("circle", { cx: xr, cy: y(r.est), r: r.self ? 6 : 4, fill: col, stroke: C.card, "stroke-width": 2, class: "mark" }, g0);
      if (r.self) text(g0, xr + 12, y(r.est) + 18, "this system", { fill: C.ink, size: 11, weight: 600 });
      const hitl = el("line", { x1: xl, x2: xr, y1: y(r.obs), y2: y(r.est), stroke: "transparent", "stroke-width": 12 }, g0);
      hover(hitl, `<div class="tt-title">${fmt.esc(r.id.replace(/_/g, " "))}</div>
        <div class="tt-row"><span>Cases tested</span><b>${r.n}</b></div>
        <div class="tt-row"><span>Observed rate</span><b>${fmt.pct(r.obs)}</b></div>
        <div class="tt-row"><span>Credibility Z</span><b>${r.z.toFixed(2)}</b></div>
        <div class="tt-row"><span>Credibility-weighted</span><b>${fmt.pct(r.est)}</b></div>
        <div class="tt-eq">${r.z.toFixed(2)} × ${fmt.pct(r.obs)} + ${(1 - r.z).toFixed(2)} × ${fmt.pct(c.mu)}</div>
        ${r.self ? "" : `<div class="tt-src">FICTIONAL seed portfolio or earlier run</div>`}`, g0, [line, a, b]);
    });
  }

  window.Charts = { credCurve, shrinkage, waterfall, loss, tornado, controls, redteam, fmt, label };
})();
