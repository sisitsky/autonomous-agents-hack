/* Loop 2 panel: reads public/data/loop2.json live from GitHub main (updated hourly by
   the monitor workflow), falling back to the copy deployed with the site. */
(function () {
  const $ = id => document.getElementById(id);
  const { fmt } = Charts, esc = fmt.esc;
  const RAW = "https://raw.githubusercontent.com/sisitsky/autonomous-agents-hack/main/public/data/loop2.json";
  let data = null; const prState = {};

  async function load() {
    for (const url of [RAW + "?t=" + Date.now(), "/data/loop2.json"]) {
      try { const r = await fetch(url, { cache: "no-store" }); if (r.ok) { data = await r.json(); data._from = url; break; } } catch {}
    }
    if (!data) { $("l2-changes").innerHTML = `<div class="skeleton">Loop 2 data not available yet.</div>`; return; }
    render();
    // Branches pushed without an API token: find the PR a human opened from them.
    const branches = [...new Set(data.changes.filter(c => c.pr && c.pr.needs_click).map(c => c.pr.branch))];
    await Promise.all(branches.map(async br => {
      try {
        const r = await fetch(`https://api.github.com/repos/${data.repo}/pulls?state=all&head=${data.repo.split("/")[0]}:${encodeURIComponent(br)}`);
        const list = r.ok ? await r.json() : [];
        if (list[0]) data.changes.forEach(c => { if (c.pr && c.pr.branch === br) c.pr = { ...c.pr, number: list[0].number, url: list[0].html_url, needs_click: false }; });
      } catch {}
    }));
    const prs = [...new Set(data.changes.filter(c => c.pr && c.pr.number).map(c => c.pr.number))];
    await Promise.all(prs.map(async n => {
      try {
        const r = await fetch(`https://api.github.com/repos/${data.repo}/pulls/${n}`);
        if (r.ok) { const p = await r.json(); prState[n] = p.merged_at ? "merged" : p.state === "closed" ? "closed" : "open"; }
      } catch {}
    }));
    render();
  }

  const ago = iso => {
    if (!iso) return "never";
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    return m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : new Date(iso).toLocaleDateString();
  };
  const label = p => p.replace(/^perils\./, "").replace(/\.frequency\.|\.severity\./, ": ").replace(/_/g, " ");

  function status(c) {
    if (!c.pr) return { k: "dry", t: "proposed (dry run)" };
    if (c.pr.needs_click) return { k: "open", t: "branch pushed · PR awaiting creation" };
    const s = prState[c.pr.number];
    return s === "merged" ? { k: "merged", t: `merged · PR #${c.pr.number}` } : s === "closed" ? { k: "closed", t: `rejected · PR #${c.pr.number}` }
      : { k: "open", t: `awaiting approval · PR #${c.pr.number}` };
  }

  function render() {
    const m = data.monitor || {}, t = m.totals || {}, src = m.sources || {};
    const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    const latestRun = (data.runs || [])[0] || {};
    $("l2-stats").innerHTML = stat("Last scan", ago(m.run_at)) + stat("Sources live", `${t.sources_ok ?? "–"} / ${t.sources ?? "–"}`)
      + stat("Evidence items", (t.store ?? 0).toLocaleString()) + stat("Model changes", data.changes.length)
      + stat("Latest run", esc((latestRun.status || "–").replace(/_/g, " ")));
    $("l2-scan").textContent = `Window: last ${m.window_days || 30} days · scanned ${ago(m.run_at)} · runs hourly (GitHub Actions)`;
    $("l2-sources").innerHTML = Object.entries(src).map(([k, s]) => `<div class="l2-src"><span>${esc(s.name)} <span class="tag">${esc(s.category)}</span></span>
      <span class="${s.status === "ok" ? "ok" : "err"}">${s.status === "ok" ? `${s.fetched} fetched · ${s.new} new` : esc(s.error || "error")}</span></div>`).join("");

    const f = $("l2-filter").value;
    const cards = data.changes.filter(c => !f || status(c).k === f);
    $("l2-changes").innerHTML = cards.map(c => {
      const st = status(c), up = c.mode_change_pct >= 0;
      return `<div class="l2-card"><div class="top"><div><div class="ev">${esc(c.event)}</div>
        <div class="meta">${esc(c.evidence_type || "")} · ${esc(c.event_date || "")} · source:
          ${c.source_url && /^https?:/.test(c.source_url) ? `<a href="${esc(c.source_url)}" target="_blank" rel="noopener">${esc(c.source)}</a>` : esc(c.source)}</div></div>
        <span class="badge ${st.k === "merged" ? "ok" : st.k === "closed" ? "error" : "review"}">${esc(st.t)}</span></div>
        <div class="desc">${esc(c.event_description)}</div>
        <div class="l2-change"><span>Changed <code>${esc(label(c.parameter))}</code></span>
          <span>${fmt.num(c.old.mode)} → <b>${fmt.num(c.new.mode)}</b> <span class="delta ${up ? "up" : "down"}">${up ? "+" : ""}${(c.mode_change_pct * 100).toFixed(1)}%</span></span>
          <span class="muted">range ${fmt.num(c.old.min)}–${fmt.num(c.old.max)} → ${fmt.num(c.new.min)}–${fmt.num(c.new.max)}</span>
          <span class="tag">${esc(c.strength)} evidence · weight ${c.evidence_weight} vs prior ${c.prior_weight}</span>
          ${c.pr && c.pr.url ? `<a href="${esc(c.pr.url)}" target="_blank" rel="noopener">view pull request →</a>` : ""}</div>
        <details class="explain" open><summary>Calibration reasoning and Bayesian update</summary><div>
          <p>${esc(c.reasoning)}</p>
          <p class="muted small">Implied change ${(c.implied_change * 100).toFixed(0)}% · mode′ = (w₀·mode + wₑ·implied) / (w₀ + wₑ) = (${c.prior_weight} × ${fmt.num(c.old.mode)} + ${c.evidence_weight} × ${fmt.num(c.old.mode * (1 + c.implied_change))}) / ${c.prior_weight + c.evidence_weight} = ${fmt.num(c.new.mode)}${c.scaled_by ? ` · scaled to ${c.scaled_by * 100}% to pass guardrails` : ""} · run ${esc(c.run_id)}</p></div></details></div>`;
    }).join("") || `<div class="skeleton">No model changes yet${f ? " in this filter" : ""}.</div>`;

    const v = data.latest_validation;
    if (v) {
      $("l2-guard").innerHTML = `<table><tr><th>Benchmark insured</th><th class="r">Current</th><th class="r">Proposed</th><th class="r">Change</th></tr>` +
        v.benchmark.map(r => `<tr><td>${esc(r.insured)}</td><td class="r">${fmt.money(r.current)}</td><td class="r">${fmt.money(r.proposed)}</td>
          <td class="r">${(r.change * 100).toFixed(1)}%</td></tr>`).join("") + `</table>
        <p class="small ${v.passed ? "" : "error"}">${v.passed ? `✓ All guardrails passed (each insured within ±${v.limits.max_benchmark_premium_change * 100}%, book within ±${v.limits.max_book_premium_change * 100}%, parameters in bounds, no protected file touched). A human must still merge.`
          : "Guardrails failed: " + v.problems.map(esc).join("; ")}</p>`;
      const before = Object.fromEntries(v.accumulation.before.by_provider.map(r => [r.provider, r]));
      $("l2-accum").innerHTML = `<table><tr><th>Provider</th><th class="r">Policies</th><th class="r">Limits</th><th class="r">Expected loss</th><th class="r">Systemic capital</th></tr>` +
        v.accumulation.after.by_provider.map(r => { const b = before[r.provider] || r; return `<tr><td>${esc(r.provider)}</td><td class="r">${r.policies}</td>
          <td class="r">${fmt.short(r.aggregate_limits)} (${(r.limit_share * 100).toFixed(0)}%)</td>
          <td class="r">${fmt.short(b.expected_loss)} → ${fmt.short(r.expected_loss)}</td><td class="r">${fmt.short(b.systemic_capital)} → ${fmt.short(r.systemic_capital)}</td></tr>`; }).join("") + "</table>";
    } else { $("l2-guard").innerHTML = `<div class="skeleton">No proposal validated yet.</div>`; $("l2-accum").innerHTML = ""; }

    $("l2-evidence").innerHTML = (data.evidence_recent || []).map(e => `<div class="row"><span class="tag">${esc(e.source)}</span> <span class="tag">${esc(e.type)}</span>
      ${e.url && /^https?:/.test(e.url) ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)} <span class="muted small">${esc(e.date || "")}</span></div>`).join("");
  }

  // ---- run now: dispatch the GitHub workflow, then follow it live
  const RUNS = "https://api.github.com/repos/sisitsky/autonomous-agents-hack/actions/workflows/calibrate.yml/runs?per_page=1";
  async function latestRun() {
    const r = await fetch(RUNS + "&t=" + Date.now());
    return r.ok ? ((await r.json()).workflow_runs || [])[0] : null;
  }
  async function runNow() {
    const btn = $("l2-run"), st = $("l2-run-status");
    btn.disabled = true;
    const before = await latestRun().catch(() => null);
    try {
      const r = await fetch("/api/loop2/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: "{}" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Could not start the run");
      st.innerHTML = `<span class="badge run">starting</span> Loop 2 dispatched on GitHub Actions…`;
      const t0 = Date.now();
      const poll = async () => {
        const run = await latestRun().catch(() => null);
        const fresh = run && (!before || run.id !== before.id);
        if (fresh) {
          const secs = Math.round((Date.now() - t0) / 1000);
          if (run.status !== "completed") {
            st.innerHTML = `<span class="badge run">${esc(run.status.replace("_", " "))}</span> monitor → calibrate → validate → PR · ${secs}s ·
              <a href="${esc(run.html_url)}" target="_blank" rel="noopener">watch on GitHub</a>`;
          } else {
            st.innerHTML = `<span class="badge ${run.conclusion === "success" ? "ok" : "error"}">${esc(run.conclusion)}</span>
              Loop 2 finished in ${secs}s · <a href="${esc(run.html_url)}" target="_blank" rel="noopener">run log</a>`;
            btn.disabled = false; data = null; await load(); return;
          }
        }
        if (Date.now() - t0 < 20 * 60 * 1000) setTimeout(poll, 8000);
        else { btn.disabled = false; st.textContent = "Still running; check GitHub Actions."; }
      };
      setTimeout(poll, 5000);
    } catch (e) { st.innerHTML = `<span class="error">${esc(e.message)}</span>`; btn.disabled = false; }
  }

  function bind() { $("l2-filter").onchange = () => data && render(); $("l2-run").onclick = runNow; }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
  load();
})();
