/* AI Underwriter web app: loads run bundles, renders the quote, and runs the
   loop live against the API (one endpoint per step). */
(function () {
  const { fmt } = Charts;
  const $ = id => document.getElementById(id);
  const esc = fmt.esc;
  const state = { runs: [], bundles: {}, current: null, live: 0, plan: null, planCache: {} };
  const STEP_NAMES = { intake: "Intake", redteam: "Red team", pricing: "Pricing", explainer: "Explainer", review: "Human review" };
  const STEP_DESC = {
    intake: "Claude reads the documents and fills in the risk profile, citing evidence.",
    redteam: "40 adversarial cases sent to the bot; Claude grades each against a rubric.",
    pricing: "Deterministic Monte Carlo engine. Priced twice to prove reproducibility.",
    explainer: "Claude writes the report; code checks every number against the pricing record.",
    review: "The quote is indicative. A human underwriter reviews open questions before anything binds.",
  };

  const paras = t => (t || "").split(/\n+/).filter(Boolean).map(p => `<p>${esc(p)}</p>`).join("");
  const gross = calc => calc.premium_build_up.find(s => s.id === "gross_premium").value;

  // Access code: entered once, remembered in this browser, sent with every call.
  const CODE_KEY = "uw_access_code";
  const getCode = () => { try { return localStorage.getItem(CODE_KEY) || ""; } catch { return ""; } };
  const setCode = c => { try { c ? localStorage.setItem(CODE_KEY, c) : localStorage.removeItem(CODE_KEY); } catch {} renderLock(); };
  function renderLock() {
    const b = document.getElementById("unlock"); if (!b) return;
    const has = !!getCode();
    b.textContent = has ? "🔓 Unlocked" : "🔒 Enter access code";
    b.classList.toggle("on", has);
    const live = document.getElementById("live-code"); if (live && has && !live.value) live.value = getCode();
  }
  window.uwAccess = { getCode, setCode };
  const looksLikeCode = t => /^uw-[0-9a-f]{6,}$/i.test((t || "").trim());
  async function api(path, body, retried) {
    body = { ...(body || {}) };
    if (!body.access_code) body.access_code = getCode() || undefined;
    else if (body.access_code !== getCode()) setCode(body.access_code);
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data; try { data = await r.json(); } catch { data = {}; }
    if (r.status === 401 && !retried) {
      // Ask once, remember it in this browser, and retry the same step.
      const c = prompt("This step calls Claude. Enter the access code once; this browser will remember it:", "");
      if (c && c.trim()) { setCode(c.trim()); return api(path, { ...body, access_code: c.trim() }, true); }
    }
    if (r.status === 401) setCode("");
    if (!r.ok) throw new Error(data.detail || `${path} failed (${r.status})`);
    return data;
  }

  // ---------------------------------------------------------------- loading
  async function init() {
    state.runs = await (await fetch("/data/runs/index.json")).json();
    await Promise.all(state.runs.map(async r => { state.bundles[r.id] = await (await fetch("/data/runs/" + r.file)).json(); }));
    renderPicker();
    select(state.runs[0].id);
    bindControls();
  }

  function renderPicker() {
    const sel = $("run-select");
    sel.innerHTML = state.runs.map(r => `<option value="${esc(r.id)}">${esc(r.label)}</option>`).join("");
    if (state.current) sel.value = state.current;
  }

  function select(id) {
    state.current = id;
    state.plan = null;
    $("run-select").value = id;
    const r = state.runs.find(x => x.id === id);
    $("run-blurb").textContent = r.blurb || "";
    render(state.bundles[id]);
  }
  // The bundle as shown: the quoted plan, or another plan re-priced live.
  function shown() {
    const b = state.bundles[state.current];
    const quoted = b.calculation.insured.plan;
    if (!state.plan || state.plan === quoted) return b;
    const calc = state.planCache[state.current + ":" + state.plan];
    return calc ? { ...b, calculation: calc, planView: state.plan } : b;
  }

  // ---------------------------------------------------------------- render
  function render(b) {
    const calc = b.calculation, rep = b.report || {}, n = rep.narrative || {}, facts = rep.facts || {};
    renderPlans(state.bundles[state.current], calc);
    const g = gross(calc);
    const uw = calc.underwriting || { decision: "quote", reasons: [] };
    const label = { quote: "Indicative · pending underwriter review", refer: "Referred to underwriter · remediation advised",
                    indicative_untested: "Indicative · untested, priced on portfolio average" }[uw.decision] || "Indicative";
    $("status-text").textContent = `${calc.plan ? calc.plan.name + " plan · " : ""}${label}`;
    document.querySelector(".premium-card .status").classList.toggle("refer", uw.decision === "refer");
    let note = document.getElementById("uw-note");
    if (!note) { note = document.createElement("div"); note.id = "uw-note"; note.className = "uw-note"; $("premium-sub").after(note); }
    note.textContent = uw.reasons.join(" ");
    animateNumber($("premium"), g);
    $("premium-sub").innerHTML = `per year · rate on line <b>${fmt.pct(calc.premium_build_up.find(s => s.id === "rate_on_line").value)}</b>
      · ${fmt.short(calc.policy.limit_per_claim)} per claim / ${fmt.short(calc.policy.aggregate_limit)} aggregate · ${fmt.short(calc.policy.deductible)} deductible`;
    const qp = state.bundles[state.current].calculation.plan;
    $("headline").innerHTML = b.planView
      ? `<span class="banner">Showing the <b>${esc(calc.plan.name)}</b> plan, re-priced live by the engine. The written report below is for the quoted <b>${esc(qp.name)}</b> plan.</span>`
      : esc(n.headline || "");
    $("summary").innerHTML = paras(n.summary);
    renderCompare();
    renderTimeline($("timeline"), b.events || []);

    renderEngine(b);

    Charts.waterfall($("chart-waterfall"), calc, calc.equations);
    $("explain-buildup").innerHTML = paras(n.price_build_up);
    tex($("eq-gross"), calc.equations.find(e => e.id === "gross_premium").latex);

    drawLoss();
    $("explain-loss").innerHTML = paras(n.loss_distribution);
    const p = calc.loss_distribution.percentiles;
    $("pct-table").innerHTML = `<table><tr><th>Percentile of annual loss</th><th class="r">Amount</th><th class="r">Means</th></tr>` +
      Object.entries(p).map(([k, v]) => `<tr data-p="${k}"><td>P${k}</td><td class="r">${fmt.money(v)}</td><td class="r muted">${
        { 50: "typical year", 75: "1 in 4", 90: "1 in 10", 95: "1 in 20", 99: "1 in 100", "99.5": "1 in 200" }[k] || ""}</td></tr>`).join("") + "</table>";

    drawTornado();
    $("tornado-sub").textContent = `Each parameter at its P10 and P90, others at their medians · point premium ${fmt.money(calc.tornado.base_premium)} (excludes the uncertainty load)`;
    $("explain-tornado").innerHTML = paras(n.tornado);
    $("evidence").innerHTML = (n.evidence_to_narrow || []).map(e => `<li><b>${esc(e.parameter)}</b>: ${esc(e.evidence)}</li>`).join("");

    renderCredibility(b);

    Charts.controls($("chart-controls"), calc);
    const recs = Object.fromEntries((n.controls || []).map(c => [c.control_id, c.recommendation]));
    $("explain-controls").innerHTML = (facts.controls || []).map(c =>
      `<p><b>${esc(c.change)}</b> → ${esc(c.premium)} (saves ${esc(c.saving)}, ${esc(c.saving_pct)}).<br>${esc(recs[c.control_id] || "")}</p>`).join("");
    renderWhatIf(b);

    renderProfile(b);
    renderRedTeam(b);
    renderMethod(calc, rep.check);
  }

  function animateNumber(node, target) {
    const start = performance.now(), from = node._v || 0, dur = 700;
    node._v = target;
    const tick = t => {
      const k = Math.min(1, (t - start) / dur), e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmt.money(from + (target - from) * e);
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function tex(node, latex) {
    if (!node) return;
    try { katex.render(latex, node, { throwOnError: false, displayMode: true }); } catch { node.textContent = latex; }
  }

  function renderCompare() {
    const rows = state.runs.map(r => ({ r, g: gross(state.bundles[r.id].calculation) }));
    const max = Math.max(...rows.map(x => x.g));
    $("compare").innerHTML = rows.map(({ r, g }) => `
      <div class="compare-row ${r.id === state.current ? "active" : ""}" data-id="${esc(r.id)}">
        <span class="nm">${esc(r.label)}</span><span class="val">${fmt.money(g)}</span>
        <span class="bar"><i style="width:${(g / max * 100).toFixed(1)}%"></i></span></div>`).join("");
    $("compare").querySelectorAll(".compare-row").forEach(n => n.onclick = () => select(n.dataset.id));
  }

  function renderTimeline(root, events) {
    const by = {};
    events.forEach(e => { by[e.step] = e; });
    root.innerHTML = Object.keys(STEP_NAMES).map((s, i) => {
      const e = by[s];
      const status = e ? e.status : "pending";
      const badge = { ok: "ok", skipped: "", review: "review", error: "error", running: "run", pending: "" }[status];
      const detail = e && e.data ? Object.entries(e.data).filter(([k]) => k !== "trace").map(([k, v]) =>
        `<div><span class="muted">${esc(k.replace(/_/g, " "))}:</span> ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</div>`).join("") : "";
      return `<li class="step" data-step="${s}">
        <div class="n">0${i + 1}</div>
        <div class="nm">${STEP_NAMES[s]} <span class="badge ${badge}">${esc(status)}</span></div>
        <div class="sm">${esc(e ? e.summary : STEP_DESC[s])}</div>
        ${e && e.duration_s !== undefined ? `<div class="dur">${e.duration_s}s</div>` : ""}
        <div class="detail"><p class="muted">${esc(STEP_DESC[s])}</p>${detail}</div></li>`;
    }).join("");
    root.querySelectorAll(".step").forEach(li => li.onclick = () => li.classList.toggle("open"));
  }

  function drawLoss() {
    const b = state.bundles[state.current];
    Charts.loss($("chart-loss"), b.calculation, $("pct-select").value || null);
    document.querySelectorAll("#pct-table tr").forEach(tr => tr.classList.toggle("hl", tr.dataset.p === $("pct-select").value));
  }
  function drawTornado() {
    Charts.tornado($("chart-tornado"), state.bundles[state.current].calculation, +$("tornado-n").value);
  }

  // ---------------------------------------------------------------- plans
  function renderPlans(b, calc) {
    const plans = b.calculation.plans;
    if (!plans) { $("plan-cards").innerHTML = ""; return; }
    const active = calc.plan ? calc.plan.id : b.calculation.insured.plan;
    $("plan-cards").innerHTML = Object.values(plans).map(p => `
      <button type="button" class="plan ${p.id === active ? "on" : ""}" data-plan="${esc(p.id)}">
        <div class="pn">${esc(p.name)} ${p.id === b.calculation.insured.plan ? '<span class="badge review">quoted</span>' : ""}</div>
        <div class="pp">${p.gross_premium ? fmt.money(p.gross_premium) : "—"}</div>
        <div class="pl">${p.gross_premium ? "per year · rate on line " + fmt.pct(p.rate_on_line, 2) : esc(p.note || "")}</div>
        <div class="pl">${fmt.short(p.policy.limit_per_claim)} per claim · ${fmt.short(p.policy.aggregate_limit)} aggregate · ${fmt.short(p.policy.deductible)} deductible</div>
        <div class="pb">${esc(p.blurb)}${p.minimum_applied ? " · minimum premium applies" : ""}</div>
        <div class="chips">${p.covers.map(c => `<span class="chip on">${esc(c.replace(/_/g, " "))}</span>`).join("")}
          ${p.not_covered.map(c => `<span class="chip no" title="Not covered on this plan">${esc(c.replace(/_/g, " "))}</span>`).join("")}</div>
      </button>`).join("");
    $("plan-note").textContent = "Crossed-out risks apply to this system but are not covered by that plan.";
    const n = (b.report || {}).narrative || {};
    $("explain-plans").innerHTML = paras(n.plans);
    $("plan-cards").querySelectorAll(".plan").forEach(el => el.onclick = () => choosePlan(el.dataset.plan));
  }
  async function choosePlan(pid) {
    const b = state.bundles[state.current], key = state.current + ":" + pid;
    if (pid === b.calculation.insured.plan) { state.plan = null; return render(b); }
    if (!state.planCache[key]) {
      const card = document.querySelector(`.plan[data-plan="${pid}"] .pl`);
      if (card) card.innerHTML = `<span class="badge run">pricing</span>`;
      try {
        const r = await api("/api/price", { insured: b.calculation.insured_full || b.insured, plan: pid });
        state.planCache[key] = r.calculation;
      } catch (e) { if (card) card.textContent = e.message; return; }
    }
    state.plan = pid;
    render(shown());
  }

  // ---------------------------------------------------------------- Monte Carlo
  let mc = null, mcSeen = false, mcRuns = [], mcSim = null;
  function renderEngine(b) {
    mcRuns = []; mcSim = null; $("mc-runs").innerHTML = "";
    const calc = b.calculation, sim = calc.meta.simulation, il = calc.simulation_illustration;
    const stat = (k, v) => `<div>${k}<b>${v}</b></div>`;
    $("mc-stats").innerHTML = stat("Parameter draws", sim.n_outer.toLocaleString()) + stat("Years per draw", sim.n_inner.toLocaleString())
      + stat("Simulated years", sim.n_years.toLocaleString()) + stat("Seed", sim.seed)
      + (il ? stat("Expected loss", fmt.money(il.expected_loss)) : "");
    drawEngine(false);
    Charts.convergence($("chart-conv"), calc);
  }
  function drawEngine(play) {
    const b = shown();
    mc = Charts.monteCarlo($("chart-mc"), mcSim || b.calculation, { layers: $("mc-layers").value, lines: +$("mc-lines").value });
    if (play && mc.play) mc.play();
  }
  function renderRuns(official) {
    const chip = (r, i) => `<span class="mc-run ${i === mcRuns.length - 1 ? "latest" : ""}">seed <b>${r.seed}</b> · expected loss <b>${fmt.money(r.el)}</b>
      ${r.se ? `±${fmt.money(2 * r.se)}` : ""} · premium <b>${fmt.money(r.g)}</b></span>`;
    $("mc-runs").innerHTML = `<span class="mc-run">official quote · seed <b>42</b> · expected loss <b>${fmt.money(official.el)}</b> · premium <b>${fmt.money(official.g)}</b></span>`
      + mcRuns.map(chip).join("")
      + (mcRuns.length > 1 ? `<span class="mc-run">spread across ${mcRuns.length} runs: <b>${fmt.money(Math.min(...mcRuns.map(r => r.g)))}</b>–<b>${fmt.money(Math.max(...mcRuns.map(r => r.g)))}</b></span>` : "");
  }
  async function runSimulation() {
    const b = shown(), btn = $("mc-play");
    btn.disabled = true; btn.textContent = "Simulating 400,000 years…";
    try {
      const r = await api("/api/simulate", { insured: b.calculation.insured, plan: b.calculation.insured.plan, lines: +$("mc-lines").value });
      mcSim = r.calculation_like;
      mcRuns.push({ seed: r.seed, el: r.expected_loss, g: r.gross_premium, se: r.standard_error });
      renderRuns({ el: b.calculation.simulation_illustration.expected_loss, g: gross(b.calculation) });
      const s = r.calculation_like.meta.simulation;
      $("mc-stats").querySelectorAll("div")[3].innerHTML = `Seed<b>${r.seed}</b>`;
      $("mc-stats").querySelectorAll("div")[4] && ($("mc-stats").querySelectorAll("div")[4].innerHTML = `Expected loss<b>${fmt.money(r.expected_loss)}</b>`);
      drawEngine(true);
    } catch (e) {
      $("mc-runs").insertAdjacentHTML("beforeend", `<span class="mc-run error">${esc(e.message)}</span>`);
    } finally { btn.disabled = false; btn.textContent = "▶ Run simulation"; }
  }

  // ---------------------------------------------------------------- credibility
  // Turn the Dirichlet/Jewell record into one view per measure (all failures or one type).
  function credView(c, measure) {
    const val = (o) => measure === "all" ? 1 - o.pass : o[measure];
    return { n: c.n, z: c.z, k: c.A, mu: val(c.mu), observed: c.n ? val(c.observed) : 0, estimate: val(c.estimate),
             portfolio: c.portfolio.map(g => ({ insured_id: g.insured_id, n: g.n, z: g.z, observed: val(g.observed), estimate: val(g.estimate) })) };
  }
  function renderCredibility(b) {
    const c = b.calculation.red_team_credibility, card = $("cred-card");
    if (!c) { card.style.display = "none"; return; }
    card.style.display = "";
    const sel = $("cred-measure"), prev = sel.value;
    const types = Object.keys(c.mu).filter(k => k !== "pass");
    sel.innerHTML = `<option value="all">All failure types</option>` + types.map(k => `<option value="${k}">${k.replace(/_/g, " ")}</option>`).join("");
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
    drawCredibility();
    const n = (b.report || {}).narrative || {};
    $("explain-cred").innerHTML = paras(n.credibility);
    const eq = id => b.calculation.equations.find(e => e.id === id);
    ["jewell_credibility:eq-cred", "buhlmann_straub:eq-bs", "dirichlet:eq-dir"].forEach(x => {
      const [id, el] = x.split(":"); if (eq(id)) tex($(el), eq(id).latex);
    });
    $("eq-cprior").innerHTML = "";
  }
  function drawCredibility() {
    const b = shown(), c = b.calculation.red_team_credibility;
    if (!c) return;
    const v = credView(c, $("cred-measure").value || "all");
    const stat = (k, x) => `<div class="stat"><div class="k">${k}</div><div class="v">${x}</div></div>`;
    $("cred-stats").innerHTML = (c.status === "insufficient_experience" ? `<div class="muted small">${esc(c.note)}</div>` : "")
      + stat("Observed share", c.n ? fmt.pct(v.observed) : "not tested") + stat("Jewell Z", c.z.toFixed(2))
      + stat("Portfolio μ", fmt.pct(v.mu)) + stat("Priced share", fmt.pct(v.estimate))
      + stat("Dirichlet A", `${c.A.toFixed(1)} <span class="muted small">(${c.r} insureds)</span>`);
    // Beta marginals of the prior Dirichlet(Aμ) and posterior Dirichlet(Aμ + counts).
    const m = $("cred-measure").value || "all", A = c.A, a0 = c.alpha0;
    const pm = m === "all" ? 1 - c.mu.pass : c.mu[m];
    const pa = m === "all" ? a0 - c.alpha.pass : c.alpha[m];
    Charts.credCloud($("chart-cred-cloud"), { prior: [A * pm, A * (1 - pm)], post: [pa, a0 - pa], observed: v.observed, n: c.n, z: c.z,
      label: m === "all" ? "all failure types" : m.replace(/_/g, " ") });
    Charts.credCurve($("chart-cred-curve"), v);
    if (v.portfolio.length) Charts.shrinkage($("chart-cred-shrink"), v, b.id);
    $("cred-sub").textContent = "Every test case ends in a pass or exactly one failure type (a Dirichlet). Jewell credibility: one Z for all types, "
      + `Z = n / (n + A) = ${c.n} / (${c.n} + ${c.A.toFixed(1)}).`;
  }

  // ---------------------------------------------------------------- what-if
  function renderWhatIf(b) {
    const opts = b.calculation.controls.options;
    const box = $("whatif-options");
    box.innerHTML = opts.map((o, i) => `<button type="button" class="chip" data-i="${i}">${esc(
      o.kind === "red_team_remediation" ? "Fix red-team failures" : `${o.factor.replace(/_/g, " ")} → ${o.to_level.replace(/_/g, " ")}`)}</button>`).join("");
    $("whatif-result").innerHTML = `<span class="muted">Pick one or more changes.</span>`;
    box.querySelectorAll(".chip").forEach(c => c.onclick = () => {
      const o = opts[+c.dataset.i];
      // one level per factor
      box.querySelectorAll(".chip.on").forEach(x => { if (x !== c && opts[+x.dataset.i].factor === o.factor) x.classList.remove("on"); });
      c.classList.toggle("on");
      queueWhatIf(b);
    });
  }
  let whatIfTimer = null, whatIfSeq = 0;
  function queueWhatIf(b) {
    clearTimeout(whatIfTimer);
    whatIfTimer = setTimeout(() => runWhatIf(b), 250);
  }
  async function runWhatIf(b) {
    const opts = b.calculation.controls.options;
    const on = [...document.querySelectorAll("#whatif-options .chip.on")].map(c => opts[+c.dataset.i]);
    const base = gross(b.calculation);
    if (!on.length) { $("whatif-result").innerHTML = `<span class="muted">Pick one or more changes.</span>`; return; }
    const ins = JSON.parse(JSON.stringify(b.calculation.insured));
    on.forEach(o => {
      if (o.kind === "red_team_remediation") {
        Object.keys(ins.red_team.failures_by_type || {}).forEach(k => { ins.red_team.failures_by_type[k] = 0; });
      } else { ins.factors = ins.factors || {}; ins.factors[o.factor] = o.to_level; }
    });
    const seq = ++whatIfSeq;
    $("whatif-result").innerHTML = `<span class="badge run">pricing</span> <span class="muted">Running the Monte Carlo engine…</span>`;
    try {
      const r = await api("/api/price", { insured: ins, plan: ins.plan, tornado: false, controls: false });
      if (seq !== whatIfSeq) return;
      const g = gross(r.calculation), save = base - g;
      const sumSingles = on.reduce((a, o) => a + o.saving, 0);
      $("whatif-result").innerHTML = `Combined premium <b>${fmt.money(g)}</b> · saves ${fmt.money(save)} (${fmt.pct(save / base, 0)})
        <span class="muted small"> · the changes priced one at a time would add up to ${fmt.money(sumSingles)}; combined effects aren't additive.</span>`;
    } catch (e) {
      if (seq === whatIfSeq) $("whatif-result").innerHTML = `<span class="error">${esc(e.message)}</span>`;
    }
  }

  // ---------------------------------------------------------------- profile
  function renderProfile(b) {
    const p = b.profile;
    if (!p) { $("profile").innerHTML = `<div class="skeleton">No intake profile (priced from an existing insured).</div>`; }
    else {
      const rows = [
        ["Company", `${p.company_name} · ${p.system_name}`, p.summary],
        ["Use case", `${p.use_case.category.replace(/_/g, " ")} · criticality ${p.use_case.criticality}`, p.use_case.evidence],
        ["Autonomy", p.autonomy.level, p.autonomy.evidence],
        ["Data sensitivity", p.data_sensitivity.level, p.data_sensitivity.evidence],
        ["Jurisdictions", p.jurisdictions.regions.join(", "), p.jurisdictions.details + " · " + p.jurisdictions.evidence],
        ["Customer facing", p.customer_facing.level, p.customer_facing.evidence],
        ["Governance", p.governance.level, p.governance.evidence],
        ["Vendor indemnity", p.vendor_indemnity.level, p.vendor_indemnity.evidence],
        ["Model provider", `${p.model_provider.name} (${p.model_provider.hosting})`, p.model_provider.evidence],
        ["Volume", p.exposure.outputs_per_year ? p.exposure.outputs_per_year.toLocaleString() + " outputs / yr" : "unknown", p.exposure.evidence],
      ];
      $("profile").innerHTML = `<div class="kv">` + rows.map(([k, v, ev]) => `<details><summary><span class="k">${esc(k)}</span>
        <span class="v">${esc(String(v).replace(/_/g, " "))}</span></summary><div class="ev">${esc(ev)}</div></details>`).join("") + `</div>`;
    }
    const calc = b.calculation;
    $("perils").innerHTML = Object.entries(calc.perils).map(([name, x]) => `
      <div class="peril"><div class="top"><span>${esc(name.replace(/_/g, " "))}</span><span class="num">${fmt.money(x.expected_loss)}</span></div>
      <div class="facts"><span>${x.expected_claims_per_year.toFixed(2)} claims / yr</span><span>modifier ×${x.modifier.toFixed(2)}</span>
      ${x.failure_type ? `<span class="tag">red-team measured</span>` : `<span class="tag">profile priced</span>`}
      ${x.rating_factors.map(f => `<span class="tag">${esc(f.factor)} ${esc(f.level)} ×${f.relativity}</span>`).join("")}</div></div>`).join("");
    const qs = ((p && p.open_questions) || []).concat(b.uwFollowUps || []);
    const done = new Set(b.uwAnswered || []);
    $("questions").innerHTML = qs.map((q, i) => `<li class="${done.has(i) ? "done" : ""}">${esc(q)}</li>`).join("")
      + (b.review_notes || []).map(q => `<li class="muted">${esc(q)}</li>`).join("") || "<li>None.</li>";
    $("uw-log").innerHTML = (b.uwLog || []).map(m => m.html).join("") || `<div class="muted small">No answers yet.</div>`;
  }

  // ---------------------------------------------------------------- underwriter chat
  async function uwSend(e) {
    e.preventDefault();
    const input = $("uw-input"), msg = input.value.trim();
    if (!msg) return;
    const b = state.bundles[state.current];
    if (looksLikeCode(msg)) {
      setCode(msg); input.value = "";
      b.uwLog = (b.uwLog || []).concat({ html: `<div class="msg bot">Access code saved in this browser. Now answer a question and I'll update the risk profile and re-price.</div>` });
      $("uw-log").innerHTML = b.uwLog.map(m => m.html).join(""); return;
    }
    b.uwLog = b.uwLog || []; b.uwHistory = b.uwHistory || []; b.uwAnswered = b.uwAnswered || [];
    b.uwLog.push({ html: `<div class="msg me">${esc(msg)}</div>` });
    b.uwLog.push({ html: `<div class="msg bot" id="uw-pending">Updating the risk profile…</div>` });
    $("uw-log").innerHTML = b.uwLog.map(m => m.html).join(""); input.value = "";
    const before = gross(b.calculation);
    try {
      const qs = ((b.profile && b.profile.open_questions) || []).concat(b.uwFollowUps || []);
      const r = await api("/api/underwriter/chat", { insured: b.calculation.insured_full || b.insured, profile: b.profile,
        open_questions: qs, history: b.uwHistory, message: msg, plan: b.calculation.insured.plan,
        access_code: $("live-code").value || getCode() });
      b.uwHistory.push({ role: "underwriter", content: msg }, { role: "assistant", content: r.reply });
      r.answered.forEach(i => { if (!b.uwAnswered.includes(i)) b.uwAnswered.push(i); });
      b.uwFollowUps = (b.uwFollowUps || []).concat(r.follow_up_questions || []);
      let px = "";
      if (r.calculation) {
        b.calculation = r.calculation; b.insured = r.insured; state.planCache = {};
        const after = gross(r.calculation);
        px = `<div class="px">Premium ${fmt.money(before)} → ${fmt.money(after)} (${after >= before ? "+" : ""}${((after / before - 1) * 100).toFixed(1)}%)</div>`;
      }
      b.uwLog.pop();
      b.uwLog.push({ html: `<div class="msg bot">${esc(r.reply)}${r.changes.length ? `<ul class="chg">${r.changes.map(c => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}${px}</div>` });
      render(shown());
      document.getElementById("uw-log").scrollTop = 1e9;
    } catch (err) {
      b.uwLog.pop(); b.uwLog.push({ html: `<div class="msg bot error">${esc(err.message)}</div>` });
      $("uw-log").innerHTML = b.uwLog.map(m => m.html).join("");
    }
  }

  // ---------------------------------------------------------------- red team
  function renderRedTeam(b) {
    const rt = b.redteam, n = (b.report || {}).narrative || {};
    if (!rt) {
      $("rt-sub").textContent = "No red-team test for this quote.";
      $("chart-redteam").innerHTML = ""; $("cases").innerHTML = ""; $("explain-redteam").innerHTML = ""; return;
    }
    const s = rt.summary;
    $("rt-sub").textContent = `${s.failures} of ${s.tested} material failures · target ${s.endpoint} · scored by ${s.scorer === "judge" ? "Claude judge" : "patterns"}`
      + (s.disagreements && s.disagreements.length ? ` · ${s.disagreements.length} judge-vs-pattern disagreements` : "");
    Charts.redteam($("chart-redteam"), s);
    $("explain-redteam").innerHTML = paras(n.red_team);
    $("case-cat").innerHTML = `<option value="">All categories</option>` + Object.keys(s.by_category)
      .map(c => `<option value="${c}">${c.replace(/_/g, " ")}</option>`).join("");
    drawCases();
  }
  function drawCases() {
    const b = state.bundles[state.current];
    if (!b.redteam) return;
    const cat = $("case-cat").value, ver = $("case-verdict").value;
    const cases = b.redteam.cases.filter(c => (!cat || c.category === cat) && (!ver || c.verdict === ver));
    $("cases").innerHTML = cases.map(c => {
      const v = c.judge_verdict || c.pattern_verdict || {};
      const fail = c.verdict === "material_failure";
      return `<details class="case"><summary><span class="tag">${esc(c.id)}</span><span class="q">${esc(c.input)}</span>
        <b class="${fail ? "v-fail" : "v-pass"}">${fail ? "fail" : c.status === "error" ? "error" : "pass"}</b></summary>
        <div class="body"><div class="muted small">Bot replied</div><div class="reply">${esc(c.reply || c.error || "")}</div>
        <div><b>Rubric:</b> ${esc(c.rubric)}</div>
        ${v.reason ? `<div><b>Why:</b> ${esc(v.reason)}</div>` : ""}
        ${v.quote ? `<div><b>Quote:</b> “${esc(v.quote)}”</div>` : ""}
        ${c.disagreement ? `<div class="muted">Pattern scorer disagreed: ${esc(c.pattern_verdict.verdict)}</div>` : ""}</div></details>`;
    }).join("") || `<div class="skeleton">No cases match.</div>`;
  }

  // ---------------------------------------------------------------- method
  function renderMethod(calc, check) {
    $("equations").innerHTML = calc.equations.map(e => `<details open><summary>${esc(e.name)}</summary>
      <div class="tex" data-tex="${esc(e.latex)}"></div><div class="meaning">${esc(e.meaning)}</div>
      <div class="muted small"><code>${esc(e.formula)}</code></div></details>`).join("");
    $("equations").querySelectorAll(".tex").forEach(n => tex(n, n.dataset.tex));
    const m = calc.meta;
    $("provenance").innerHTML = `Priced ${esc(m.priced_at)} · code <code>${esc(String(m.git_commit || "n/a").slice(0, 12))}</code> ·
      parameters ${esc(Object.entries(m.params_versions).map(([k, v]) => k + " " + v).join(", "))} ·
      seed ${m.simulation.seed}, ${m.simulation.n_years.toLocaleString()} simulated years · ${esc(m.reproducibility_check || "")}
      ${check ? ` · narrative: ${esc(check.source)}${check.model ? " (" + esc(check.model) + ")" : ""}, every number checked against the pricing record` : ""}`;
  }

  // ---------------------------------------------------------------- chat
  async function chat(e) {
    e.preventDefault();
    const input = $("chat-input"), msg = input.value.trim();
    if (!msg) return;
    if (looksLikeCode(msg)) { setCode(msg); input.value = ""; $("chat-log").insertAdjacentHTML("beforeend", `<div class="msg bot">Access code saved in this browser.</div>`); return; }
    input.value = "";
    const log = $("chat-log");
    if (log.querySelector(".muted")) log.innerHTML = "";
    log.insertAdjacentHTML("beforeend", `<div class="msg me">${esc(msg)}</div>`);
    const pending = document.createElement("div");
    pending.className = "msg bot"; pending.textContent = "…"; log.appendChild(pending);
    log.scrollTop = log.scrollHeight;
    try {
      const r = await api("/api/chat", { message: msg, bot: $("chat-bot").value, access_code: $("live-code").value || getCode() });
      pending.textContent = r.reply;
    } catch (err) { pending.textContent = err.message; pending.classList.add("error"); }
    log.scrollTop = log.scrollHeight;
  }

  // ---------------------------------------------------------------- live run
  // Uploads must fit Vercel's 4.5 MB request limit. Small PDFs go as-is (Claude reads
  // the layout); large ones are converted to text in the browser, and very long
  // documents keep the pages most relevant to underwriting.
  let uploads = [];
  const BODY_BUDGET = 1_200_000;        // characters of document content per request (~300k tokens)
  const PDF_INLINE_MAX = 1_500_000;     // PDFs smaller than this are sent as PDF
  const KEYWORDS = /\b(artificial intelligence|AI|model|models|Claude|LLM|chatbot|agent|safety|risk factors?|customers?|users?|revenue|liabilit|lawsuit|litigation|regulat|privacy|personal data|data protection|security|breach|copyright|intellectual property|indemn|hallucinat|misuse|outage|incident|insurance|governance|EU AI Act|enterprise|API|volume|tokens?)\b/gi;
  const readAs = (f, how) => new Promise((res, rej) => { const r = new FileReader(); r.onerror = () => rej(r.error); r.onload = () => res(r.result); r[how](f); });

  async function pdfToText(f, onProgress) {
    if (!window.pdfjsLib) throw new Error("PDF reader failed to load; try a smaller file or paste text");
    pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(await readAs(f, "readAsArrayBuffer")) }).promise;
    const pages = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      const tc = await (await pdf.getPage(i)).getTextContent();
      pages.push(tc.items.map(it => it.str).join(" ").replace(/\s+/g, " ").trim());
      if (i % 10 === 0) onProgress(`${f.name}: reading page ${i} of ${pdf.numPages}…`);
    }
    return pages;
  }

  function fitPages(name, pages, budget) {
    const total = pages.reduce((a, p) => a + p.length, 0);
    if (total <= budget) return { text: pages.map((p, i) => `[page ${i + 1}] ${p}`).join("\n"), note: null };
    // Keep the opening pages (cover, summary, business overview) and then the
    // pages with the densest underwriting-relevant language, in page order.
    const scored = pages.map((p, i) => ({ i, len: p.length, score: (p.match(KEYWORDS) || []).length / Math.sqrt(p.length + 200) + (i < 8 ? 100 : 0) }));
    scored.sort((a, b) => b.score - a.score);
    const keep = new Set(); let used = 0;
    for (const s of scored) { if (used + s.len > budget) continue; keep.add(s.i); used += s.len; }
    const text = pages.map((p, i) => keep.has(i) ? `[page ${i + 1}] ${p}` : null).filter(Boolean).join("\n");
    return { text, note: `${name}: ${pages.length} pages, ${Math.round(total / 1000)}k characters. Kept the ${keep.size} most underwriting-relevant pages (opening pages plus business, AI, risk-factor, data, customer and regulation content); the rest were omitted to fit the upload limit.` };
  }

  async function readFiles(files) {
    const list = [...files], status = $("file-list");
    const raw = [];
    for (const f of list) {
      const lower = f.name.toLowerCase();
      if (lower.endsWith(".pdf") && f.size <= PDF_INLINE_MAX) {
        raw.push({ name: f.name, pdf_base64: String(await readAs(f, "readAsDataURL")).split(",")[1] });
      } else if (lower.endsWith(".pdf")) {
        status.textContent = `${f.name}: large PDF (${(f.size / 1e6).toFixed(1)} MB), extracting text in your browser…`;
        raw.push({ name: f.name, pages: await pdfToText(f, m => { status.textContent = m; }) });
      } else {
        raw.push({ name: f.name, text: String(await readAs(f, "readAsText")) });
      }
    }
    // Share the budget across documents (inline PDFs count at their encoded size).
    const fixed = raw.reduce((a, d) => a + (d.pdf_base64 ? d.pdf_base64.length : 0), 0);
    const flexible = raw.filter(d => !d.pdf_base64);
    const each = Math.max(150_000, Math.floor((BODY_BUDGET - fixed) / Math.max(1, flexible.length)));
    const notes = [];
    uploads = raw.map(d => {
      if (d.pdf_base64) return d;
      const pages = d.pages || [d.text];
      const fit = d.pages ? fitPages(d.name, pages, each)
        : (d.text.length > each ? { text: d.text.slice(0, each), note: `${d.name}: truncated to the first ${Math.round(each / 1000)}k characters.` } : { text: d.text, note: null });
      if (fit.note) notes.push(fit.note);
      return { name: d.name + (d.pages ? " (text extracted)" : ""), text: fit.text };
    });
    state.uploadNotes = notes;
    status.textContent = uploads.map(u => u.name).join(" · ") + (notes.length ? " · " + notes.join(" ") : "");
  }
  async function loadSample() {
    const names = ["01_application.md", "02_architecture.md", "03_customer_policy.md"];
    uploads = await Promise.all(names.map(async n => ({ name: n, text: await (await fetch("/data/submissions/" + n)).text() })));
    $("file-list").textContent = "Loaded: " + names.join(" · ");
    $("live-bot").value = "deterministic"; $("endpoint-fields").hidden = true;
  }

  async function runLive(e) {
    e.preventDefault();
    const btn = $("live-run"), code = $("live-code").value, bot = $("live-bot").value, desc = $("live-desc").value.trim();
    $("live-error").textContent = "";
    if (!uploads.length && !desc) { $("live-error").textContent = "Describe the system or attach documents (or use the Northwind sample)."; return; }
    btn.disabled = true;
    const events = [];
    const tl = $("live-timeline");
    const push = ev => { const i = events.findIndex(x => x.step === ev.step); i >= 0 ? (events[i] = ev) : events.push(ev); renderTimeline(tl, events); };
    const timed = async (step, fn, summarize) => {
      push({ step, status: "running", summary: STEP_DESC[step] });
      const t0 = performance.now();
      try {
        const r = await fn();
        push({ step, status: "ok", summary: summarize(r), duration_s: +((performance.now() - t0) / 1000).toFixed(1) });
        return r;
      } catch (err) { push({ step, status: "error", summary: err.message }); throw err; }
    };
    try {
      state.live += 1;
      const id = "live_" + state.live;
      const vol = +$("live-volume").value || null, plan = $("live-plan").value || null;
      const ep = { endpoint: $("ep-url").value.trim(), message_field: $("ep-msg").value.trim() || "message",
                   reply_path: $("ep-reply").value.trim() || "reply", auth_header: $("ep-auth").value.trim() || null,
                   authorized: $("ep-ok").checked };
      if (bot === "endpoint" && (!ep.endpoint || !ep.authorized)) throw new Error("Enter the endpoint URL and confirm you are authorized to test it.");
      const descFull = [desc, ...(state.uploadNotes || []).map(n => "Upload note: " + n)].filter(Boolean).join("\n") || null;
      const intake = await timed("intake", () => api("/api/intake", { documents: uploads, description: descFull, insured_id: id,
        outputs_per_year: vol, plan, endpoint: bot === "endpoint" ? ep.endpoint : null, access_code: code }),
        r => `${r.profile.company_name} / ${r.profile.system_name}: ${r.profile.use_case.category.replace(/_/g, " ")}, ${Object.keys(r.insured.perils).length} perils, ${r.open_questions.length} open questions`);
      let rt = null;
      if (bot === "none") {
        push({ step: "redteam", status: "skipped", summary: "No red-team test: the four tested risks are priced on the portfolio average (Jewell prior) with its full uncertainty." });
      } else {
        const target = bot === "endpoint" ? ep.endpoint : `the demo ShopBot (${bot === "llm" ? "Claude-powered" : "planted flaws"})`;
        rt = await timed("redteam", () => api("/api/redteam", { insured: intake.insured, profile: intake.profile, bot, ...ep, access_code: code }),
          r => `${r.summary.failures} of ${r.summary.tested} material failures on ${target}` + (r.summary.errors ? ` (${r.summary.errors} cases errored)` : ""));
      }
      const insured = rt ? rt.insured : intake.insured;
      const pr = await timed("pricing", () => api("/api/price", { insured }),
        r => `${r.calculation.plan.name} plan ${fmt.money(gross(r.calculation))}; all plans: ` +
             Object.values(r.calculation.plans).map(p => `${p.name} ${p.gross_premium ? fmt.money(p.gross_premium) : "n/a"}`).join(", "));
      const ex = await timed("explainer", () => api("/api/explain", {
        calculation: pr.calculation, profile: intake.profile, review_notes: intake.review_notes,
        redteam: rt ? { summary: rt.summary, cases: rt.cases } : null, access_code: code }),
        r => `Report written (${r.check.source} narrative, numbers verified${r.check.fallback_sections.length ? "; template fallback for " + r.check.fallback_sections.join(", ") : ""})`);
      push({ step: "review", status: "review", summary: `Indicative quote ready. Awaiting underwriter review of ${intake.open_questions.length} open questions. Not bound.` });
      const bundle = { id, profile: intake.profile, insured, review_notes: intake.review_notes,
        redteam: rt ? { summary: rt.summary, cases: rt.cases } : null, calculation: pr.calculation, report: ex, events: events.slice() };
      state.bundles[id] = bundle;
      const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      state.runs.push({ id, file: null, label: `Live run ${state.live} (${t}): ${intake.profile.system_name}`, blurb: "Run live in this browser session." });
      renderPicker();
      select(id);
      document.getElementById("top").scrollIntoView({ behavior: "smooth" });
    } catch (err) {
      $("live-error").textContent = err.message;
    } finally { btn.disabled = false; }
  }

  // ---------------------------------------------------------------- bindings
  function bindControls() {
    $("run-select").onchange = e => select(e.target.value);
    $("pct-select").onchange = drawLoss;
    $("tornado-n").onchange = drawTornado;
    $("cred-measure").onchange = drawCredibility;
    $("mc-layers").onchange = () => drawEngine(false);
    $("mc-lines").onchange = () => drawEngine(true);
    $("mc-play").onclick = runSimulation;
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries, obs) => {
        if (entries.some(e => e.isIntersecting) && !mcSeen) { mcSeen = true; drawEngine(true); obs.disconnect(); }
      }, { threshold: 0.35 }).observe($("chart-mc"));
    }
    $("case-cat").onchange = drawCases;
    $("case-verdict").onchange = drawCases;
    $("chat-form").onsubmit = chat;
    $("uw-form").onsubmit = uwSend;
    $("unlock").onclick = () => {
      if (getCode() && confirm("Forget the access code in this browser?")) return setCode("");
      const c = prompt("Access code (needed for steps that call Claude):", getCode());
      if (c !== null) setCode(c.trim());
    };
    renderLock();
    $("live-form").onsubmit = runLive;
    $("live-files").onchange = e => readFiles(e.target.files);
    $("load-sample").onclick = loadSample;
    $("live-bot").onchange = e => { $("endpoint-fields").hidden = e.target.value !== "endpoint"; };
    renderTimeline($("live-timeline"), []);
  }

  init().catch(err => { document.querySelector(".hero").insertAdjacentHTML("beforeend", `<p class="error">Could not load quotes: ${esc(err.message)}</p>`); });
})();
