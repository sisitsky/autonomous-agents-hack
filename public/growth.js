/* Loop 3 panel: segments, campaigns (with approval status), funnel by segment, playbook. */
(function () {
  const $ = id => document.getElementById(id);
  const { fmt } = Charts, esc = fmt.esc;
  const RAW = "https://raw.githubusercontent.com/sisitsky/autonomous-agents-hack/main/public/data/growth.json";
  const STEPS = [["visits", "visits"], ["score_requests", "free scores"], ["checkout_started", "checkouts"], ["purchases", "purchases"]];

  function md(text) {   // small Markdown subset: headings, bullets, tables, bold, code
    const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    const out = []; let inList = false, table = [];
    const flushTable = () => { if (!table.length) return;
      const rows = table.filter(r => !/^\|\s*-/.test(r)).map(r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim())));
      out.push(`<div class="table-wrap"><table>${rows.map((r, i) => `<tr>${r.map(c => i ? `<td>${c}</td>` : `<th>${c}</th>`).join("")}</tr>`).join("")}</table></div>`); table = []; };
    for (const line of (text || "").split("\n")) {
      if (/^\|/.test(line)) { table.push(line); continue; } flushTable();
      if (/^\s*-\s+/.test(line)) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${inline(line.replace(/^\s*-\s+/, ""))}</li>`); continue; }
      if (inList && !/^\s{2,}/.test(line)) { out.push("</ul>"); inList = false; }
      const h = line.match(/^(#{1,3})\s+(.*)/);
      if (h) out.push(`<h3>${inline(h[2])}</h3>`); else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
    flushTable(); if (inList) out.push("</ul>");
    return out.join("");
  }

  function funnelChart(container, rows) {
    const W = 1080, row = 46, L = 230, R = 30, T = 24, H = T + row * rows.length + 30, pw = W - L - R;
    const max = Math.max(1, ...rows.map(r => r.visits));
    const ns = "http://www.w3.org/2000/svg";
    container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Conversion funnel by segment"></svg>`;
    const s = container.querySelector("svg");
    const add = (tag, a) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); s.appendChild(e); return e; };
    const cols = ["#3987e5", "#9085e9", "#c98500", "#199e70"];
    STEPS.forEach(([, lbl], i) => { add("rect", { x: L + i * 150, y: 4, width: 10, height: 10, rx: 2, fill: cols[i] });
      const t = add("text", { x: L + i * 150 + 16, y: 13, "font-size": 11, fill: "#c3c2b7" }); t.textContent = lbl; });
    rows.forEach((r, i) => {
      const y = T + i * row;
      const t = add("text", { x: L - 12, y: y + 24, "text-anchor": "end", "font-size": 12.5, fill: "#c3c2b7" }); t.textContent = r.segment_id;
      STEPS.forEach(([k], j) => {
        const v = r[k] || 0, w = Math.max(1, pw * v / max), h = 7, yy = y + 6 + j * 8;
        const b = add("rect", { x: L, y: yy, width: w, height: h, rx: 3, fill: cols[j], class: "hit" });
        b.addEventListener("pointerenter", e => { const tip = document.getElementById("tooltip");
          tip.innerHTML = `<div class="tt-title">${esc(r.segment_id)} · ${esc(STEPS[j][1])}</div><div class="tt-big">${v.toLocaleString()}</div>
            <div class="tt-row"><span>visit → score</span><b>${r.visit_to_score != null ? (r.visit_to_score * 100).toFixed(1) + "%" : "–"}</b></div>
            <div class="tt-row"><span>score → checkout</span><b>${r.score_to_checkout != null ? (r.score_to_checkout * 100).toFixed(1) + "%" : "–"}</b></div>
            <div class="tt-row"><span>checkout → purchase</span><b>${r.checkout_to_purchase != null ? (r.checkout_to_purchase * 100).toFixed(1) + "%" : "–"}</b></div>
            <div class="tt-row"><span>cost per purchase</span><b>${r.cost_per_purchase != null ? fmt.money(r.cost_per_purchase) : "–"}</b></div>`;
          tip.classList.add("on"); tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px"; });
        b.addEventListener("pointermove", e => { const tip = document.getElementById("tooltip"); tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px"; });
        b.addEventListener("pointerleave", () => document.getElementById("tooltip").classList.remove("on"));
        if (j === 0) { const n = add("text", { x: L + w + 8, y: yy + 7, "font-size": 10.5, fill: "#898781" }); n.textContent = `${r.visits} visits · ${r.purchases} bought · $${r.spend_usd.toFixed(0)} spend`; }
      });
    });
  }

  function render(g) {
    const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    const plan = g.plan || {}, an = g.analytics || {}, cycles = g.cycles || [];
    const spent = cycles.reduce((a, c) => a + (c.spend_usd || 0), 0), cap = g.guardrails.max_total_hackathon_spend_usd;
    $("g-stats").innerHTML = stat("Egbe", g.egbe_connected ? "connected" : "not connected")
      + stat("Wallet", g.wallet && g.wallet.available_to_spend_cents != null ? fmt.money(g.wallet.available_to_spend_cents / 100) : "–")
      + stat("Cycle", plan.cycle || "–") + stat("Spend vs cap", `${fmt.money(spent)} / ${fmt.money(cap)}`)
      + stat("Full report", g.checkout_url ? `<a href="${esc(g.checkout_url)}" target="_blank" rel="noopener">$${g.full_report_price} checkout</a>` : `$${g.full_report_price} (link pending)`);
    $("g-via").textContent = g.research_via === "egbe_research" ? "researched with Egbe" : g.research_via ? "researched with Claude web search" : "";
    $("g-segs").innerHTML = (g.segments || []).map(s => `<div class="l2-card"><div class="top"><div><div class="ev">${esc(s.name)}</div>
      <div class="meta">${esc(s.id)} · channels: ${s.channels.map(esc).join(", ")} · typical system ${s.archetype.outputs_per_year.toLocaleString()} outputs/yr</div></div>
      <span class="badge ${s.score.risk_score >= 85 ? "error" : s.score.risk_score >= 50 ? "review" : "ok"}">risk score ${s.score.risk_score}</span></div>
      <div class="desc">${esc(s.why_they_need_cover)}</div>
      <div class="l2-change">${Object.entries(s.score.plans).map(([k, v]) => `<span>${esc(k)} <b>${fmt.money(v.premium)}</b></span>`).join("")}
        <a href="/score?seg=${encodeURIComponent(s.id)}" target="_blank" rel="noopener">landing variant →</a></div>
      <details class="explain"><summary>Evidence, buying signals, example companies</summary><div>
        <p><b>Signals:</b> ${s.buying_signals.map(esc).join(" · ")}</p><p><b>Examples:</b> ${s.example_companies.map(esc).join(", ") || "–"}</p>
        <ul>${s.evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul></div></details></div>`).join("") || `<div class="skeleton">No research yet.</div>`;
    const approved = plan.status === "approved";
    $("g-camp-sub").innerHTML = plan.campaigns ? `${plan.campaigns.length} campaigns · ${fmt.money(plan.total_budget_usd)} planned · ${approved ? "<b>approved</b>" : "<b>awaiting approval</b>"}`
      + (plan.approval_pr && !approved ? ` · <a href="${esc(plan.approval_pr.url)}" target="_blank" rel="noopener">review & approve in GitHub →</a>` : "") : "";
    $("g-spend").innerHTML = `<div class="meter"><i style="width:${Math.min(100, (spent + (plan.total_budget_usd || 0)) / cap * 100).toFixed(1)}%"></i></div>
      <div class="muted small">Planned + spent ${fmt.money(spent + (plan.total_budget_usd || 0))} of the ${fmt.money(cap)} hackathon cap · daily cap ${fmt.money(g.guardrails.max_daily_ad_spend_usd)} · max ${g.guardrails.max_outreach_emails_per_day} emails/day · every template needs approval</div>`
      + (plan.guardrail_notes || []).map(n => `<p class="small">${esc(n)}</p>`).join("");
    $("g-camps").innerHTML = (plan.campaigns || []).map(c => `<div class="l2-card"><div class="top"><div><div class="ev">${esc(c.landing.headline)}</div>
      <div class="meta">${esc(c.segment_id)} · ${esc(c.ad.channel)} · ${fmt.money(c.budget_usd)} · <code>${esc(c.utm_campaign)}</code></div></div>
      <span class="badge ${approved ? "ok" : "review"}">${approved ? "approved" : "pending approval"}</span></div>
      <div class="desc">${esc(c.landing.subhead)}</div>
      <details class="explain"><summary>Outreach email and ad copy</summary><div>
        <p><b>Subject:</b> ${esc(c.outreach_email.subject)}</p><div class="email">${esc(c.outreach_email.body)}</div>
        <p><b>Ad:</b> ${esc(c.ad.headline)} | ${esc(c.ad.text)}</p>
        <p><a href="${esc(c.landing_url)}" target="_blank" rel="noopener">landing page with UTM →</a></p></div></details></div>`).join("") || `<div class="skeleton">No campaigns yet.</div>`;
    const rows = an.rows || [];
    $("g-sim").textContent = an.simulated ? "SIMULATED until Egbe analytics is connected" : an.source === "egbe_analytics" ? "Egbe analytics" : "";
    if (rows.length) {
      funnelChart($("chart-funnel"), rows);
      $("g-funnel-table").innerHTML = `<table><tr><th>Segment</th><th class="r">Spend</th><th class="r">Visits</th><th class="r">Scores</th><th class="r">Checkouts</th><th class="r">Purchases</th><th class="r">Cost / purchase</th><th class="r">Revenue</th></tr>` +
        rows.map(r => `<tr><td>${esc(r.segment_id)}</td><td class="r">${fmt.money(r.spend_usd)}</td><td class="r">${r.visits}</td><td class="r">${r.score_requests}</td><td class="r">${r.checkout_started}</td><td class="r">${r.purchases}</td>
          <td class="r">${r.cost_per_purchase != null ? fmt.money(r.cost_per_purchase) : "–"}</td><td class="r">${fmt.money(r.revenue_usd)}</td></tr>`).join("") + "</table>";
    } else { $("chart-funnel").innerHTML = `<div class="skeleton">No conversion data yet.</div>`; $("g-funnel-table").innerHTML = ""; }
    $("g-playbook").innerHTML = md(g.playbook);
  }

  (async () => {
    const got = await Promise.all([RAW + "?t=" + Date.now(), "/data/growth.json?t=" + Date.now()].map(async u => {
      try { const r = await fetch(u, { cache: "no-store" }); return r.ok ? await r.json() : null; } catch { return null; } }));
    const g = got.filter(Boolean).sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
    if (g) render(g);
  })();
})();
