"""Monitor agent (Loop 2, step 1): gather new evidence on a schedule.

Connectors pull AI incidents, court and enforcement news, regulatory changes, and
security and research signals from public APIs and feeds, normalize them to one
evidence schema, and append only items not seen before:

    data/evidence/evidence.jsonl      every item ever seen (append-only, deduped by id)
    data/evidence/latest_run.json     the last run: per-source status and new items

    python -m agents.monitor                  # all connectors
    python -m agents.monitor --only federal_register,cisa_kev
    python -m agents.monitor --days 14

Optional keys (environment): COURTLISTENER_TOKEN, REGULATIONS_GOV_KEY,
CONGRESS_GOV_KEY, NVD_API_KEY, GITHUB_TOKEN. Without them the public/DEMO_KEY
tiers are used where available. Every connector fails soft: one broken source
never stops the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import ROOT

EVIDENCE_DIR = ROOT / "data" / "evidence"
STORE = EVIDENCE_DIR / "evidence.jsonl"
LATEST = EVIDENCE_DIR / "latest_run.json"
MANUAL_DIR = ROOT / "data" / "incidents"
UA = "ai-underwriter-monitor/1.0 (hackathon research prototype)"
AI_TERMS = re.compile(r"\b(artificial intelligence|AI|chatbot|generative|LLM|large language model|machine learning|"
                      r"algorithm(ic)?|automated decision|deepfake|neural|model)\b", re.I)


def _get(url: str, headers: dict | None = None, timeout: float = 25.0, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str, **kw):
    return json.loads(_get(url, **kw))


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _item(source: str, kind: str, title: str, url: str, date: str | None, summary: str = "", **extra) -> dict:
    ident = hashlib.sha1(f"{source}|{url or title}".encode()).hexdigest()[:16]
    return {"id": f"{source}:{ident}", "source": source, "type": kind, "title": (title or "").strip()[:300],
            "url": url, "date": (date or "")[:10], "summary": re.sub(r"\s+", " ", (summary or "")).strip()[:800], **extra}


def _rss(url: str, source: str, kind: str, days: int, filter_ai: bool = True) -> list[dict]:
    root = ET.fromstring(_get(url))
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = re.sub("<[^>]+>", " ", it.findtext("description") or "")
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        try:
            d = datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            d = None
        if d and d < _since(days):
            continue
        if filter_ai and not AI_TERMS.search(f"{title} {desc}"):
            continue
        out.append(_item(source, kind, title, link, d.date().isoformat() if d else None, desc))
    return out


# ---------------------------------------------------------------- connectors
# Each returns a list of normalized items. Categories follow the Loop 2 brief.

def federal_register(days):
    q = urllib.parse.urlencode({"conditions[term]": '"artificial intelligence"', "per_page": 20, "order": "newest",
                                "conditions[publication_date][gte]": _since(days).date().isoformat()})
    data = _json(f"https://www.federalregister.gov/api/v1/documents.json?{q}")
    return [_item("federal_register", "regulation", d["title"], d["html_url"], d.get("publication_date"),
                  d.get("abstract") or "", agency=", ".join(a.get("name", "") for a in d.get("agencies", [])))
            for d in data.get("results", [])]


def regulations_gov(days):
    key = os.environ.get("REGULATIONS_GOV_KEY", "DEMO_KEY")
    q = urllib.parse.urlencode({"filter[searchTerm]": "artificial intelligence", "page[size]": 20,
                                "sort": "-postedDate", "filter[postedDate][ge]": _since(days).date().isoformat(),
                                "api_key": key})
    data = _json(f"https://api.regulations.gov/v4/documents?{q}")
    return [_item("regulations_gov", "regulation", d["attributes"].get("title", ""),
                  f"https://www.regulations.gov/document/{d['id']}", d["attributes"].get("postedDate"),
                  d["attributes"].get("documentType", ""), agency=d["attributes"].get("agencyId"))
            for d in data.get("data", [])]


def congress_gov(days):
    key = os.environ.get("CONGRESS_GOV_KEY", "DEMO_KEY")
    q = urllib.parse.urlencode({"format": "json", "limit": 250, "sort": "updateDate+desc", "api_key": key,
                                "fromDateTime": _since(days).strftime("%Y-%m-%dT00:00:00Z")})
    data = _json(f"https://api.congress.gov/v3/bill?{q}")
    out = []
    for b in data.get("bills", []):
        if re.search(r"artificial intelligence|\bAI\b|algorithm|deepfake|chatbot", b.get("title", ""), re.I):
            out.append(_item("congress_gov", "regulation", f"{b.get('type')} {b.get('number')}: {b.get('title')}",
                             b.get("url", "").split("?")[0], b.get("updateDate"),
                             (b.get("latestAction") or {}).get("text", "")))
    return out


def sec_press(days):
    return _rss("https://www.sec.gov/news/pressreleases.rss", "sec_press", "enforcement", days)


def cfpb(days):
    return _rss("https://www.consumerfinance.gov/about-us/newsroom/feed/", "cfpb", "enforcement", days)


def cisa_kev(days):
    data = _json("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    since = _since(days).date().isoformat()
    out = []
    for v in data.get("vulnerabilities", []):
        if v.get("dateAdded", "") < since:
            continue
        text = f"{v.get('vendorProject')} {v.get('product')} {v.get('shortDescription')}"
        out.append(_item("cisa_kev", "security", f"{v['cveID']}: {v.get('vendorProject')} {v.get('product')}",
                         f"https://nvd.nist.gov/vuln/detail/{v['cveID']}", v.get("dateAdded"), v.get("shortDescription", ""),
                         ai_related=bool(re.search(r"\b(AI|ML|model|LLM|inference|torch|tensorflow|hugging)", text, re.I))))
    return out


def nvd(days):
    headers = {"apiKey": os.environ["NVD_API_KEY"]} if os.environ.get("NVD_API_KEY") else {}
    start = _since(min(days, 100)).strftime("%Y-%m-%dT00:00:00.000")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59.999")
    out = []
    for kw in ("LLM", "prompt injection", "machine learning"):
        q = urllib.parse.urlencode({"keywordSearch": kw, "pubStartDate": start, "pubEndDate": end, "resultsPerPage": 20})
        for v in _json(f"https://services.nvd.nist.gov/rest/json/cves/2.0?{q}", headers=headers).get("vulnerabilities", []):
            c = v["cve"]
            desc = next((d["value"] for d in c.get("descriptions", []) if d.get("lang") == "en"), "")
            out.append(_item("nvd", "security", f"{c['id']} ({kw})", f"https://nvd.nist.gov/vuln/detail/{c['id']}",
                             c.get("published"), desc))
        time.sleep(1 if headers else 6)   # NVD public rate limit
    return out


def github_advisories(days):
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    since = _since(days).date().isoformat()
    out = []
    for pkg in ("transformers", "torch", "langchain", "llama-index", "vllm", "gradio", "huggingface-hub", "openai"):
        q = urllib.parse.urlencode({"ecosystem": "pip", "affects": pkg, "per_page": 10, "published": f">={since}"})
        for a in _json(f"https://api.github.com/advisories?{q}", headers=headers):
            out.append(_item("github_advisories", "security", f"{a.get('ghsa_id')}: {a.get('summary')}",
                             a.get("html_url"), a.get("published_at"), a.get("description", "")[:600],
                             package=pkg, severity=a.get("severity")))
    return out


def mitre_atlas(days):
    data = yaml.safe_load(_get("https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS.yaml"))
    studies = []
    for m in data.get("matrices", [data]):
        studies += m.get("case-studies", []) or []
    studies += data.get("case-studies", []) or []
    out = []
    for cs in studies:
        d = str(cs.get("incident-date") or cs.get("date") or "")
        out.append(_item("mitre_atlas", "incident", cs.get("name", ""), f"https://atlas.mitre.org/studies/{cs.get('id')}",
                         d, cs.get("summary", "")[:700], technique_count=len(cs.get("procedure", []) or [])))
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:25]


def avid(days):
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    years = _json("https://api.github.com/repos/avidml/avid-db/contents/reports", headers=headers)
    latest = sorted([y["name"] for y in years if y["type"] == "dir"])[-1:]
    out = []
    for y in latest:
        files = _json(f"https://api.github.com/repos/avidml/avid-db/contents/reports/{y}", headers=headers)
        for f in sorted(files, key=lambda f: f["name"], reverse=True)[:12]:
            try:
                r = json.loads(_get(f["download_url"]))
            except Exception:  # noqa: BLE001 - one bad report shouldn't stop the source
                continue
            desc = ((r.get("problemtype") or {}).get("description") or {}).get("value", "")
            out.append(_item("avid", "incident", f"{r.get('data_type', 'report')} {f['name']}: {desc[:120]}",
                             f["html_url"], (r.get("reported_date") or "")[:10], desc,
                             affects=str((r.get("affects") or {}).get("artifacts", ""))[:200]))
    return out


def arxiv(days):
    q = urllib.parse.urlencode({"search_query": 'all:"prompt injection" OR all:"hallucination" AND all:"LLM"',
                                "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 15})
    root = ET.fromstring(_get(f"https://export.arxiv.org/api/query?{q}"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        pub = e.findtext("a:published", default="", namespaces=ns)
        if pub and pub[:10] < _since(days).date().isoformat():
            continue
        out.append(_item("arxiv", "research", e.findtext("a:title", default="", namespaces=ns),
                         e.findtext("a:id", default="", namespaces=ns), pub,
                         e.findtext("a:summary", default="", namespaces=ns)))
    return out


def gdelt(days):
    q = urllib.parse.urlencode({"query": '("AI chatbot" OR "artificial intelligence") (lawsuit OR fined OR settlement OR ruling)',
                                "mode": "artlist", "format": "json", "maxrecords": 25,
                                "timespan": f"{min(days, 30)}d", "sort": "datedesc"})
    for attempt in range(3):
        try:
            data = _json(f"https://api.gdeltproject.org/api/v2/doc/doc?{q}", timeout=30)
            break
        except (json.JSONDecodeError, urllib.error.HTTPError):
            time.sleep(6)
    else:
        raise RuntimeError("GDELT rate-limited (one request per 5 seconds)")
    return [_item("gdelt", "news", a.get("title", ""), a.get("url"),
                  datetime.strptime(a["seendate"][:8], "%Y%m%d").date().isoformat() if a.get("seendate") else None,
                  a.get("domain", ""), country=a.get("sourcecountry")) for a in data.get("articles", [])]


def edgar_fulltext(days):
    q = urllib.parse.urlencode({"q": '"artificial intelligence" "hallucination"', "forms": "10-K,10-Q",
                                "dateRange": "custom", "startdt": _since(days).date().isoformat(),
                                "enddt": datetime.now(timezone.utc).date().isoformat()})
    data = _json(f"https://efts.sec.gov/LATEST/search-index?{q}")
    out = []
    for h in (data.get("hits") or {}).get("hits", [])[:20]:
        s = h.get("_source", {})
        name = (s.get("display_names") or ["?"])[0]
        out.append(_item("edgar", "disclosure", f"{s.get('form')} {name}: AI hallucination risk disclosure",
                         f"https://www.sec.gov/Archives/edgar/data/{(s.get('ciks') or ['0'])[0]}/{h.get('_id', '').split(':')[0].replace('-', '')}",
                         s.get("file_date"), f"{s.get('form')} filed {s.get('file_date')} by {name}"))
    return out


def courtlistener(days):
    token = os.environ.get("COURTLISTENER_TOKEN")
    if not token:
        raise RuntimeError("needs COURTLISTENER_TOKEN (free account at courtlistener.com)")
    q = urllib.parse.urlencode({"q": '"chatbot" OR "artificial intelligence" OR "generative AI"', "type": "o",
                                "order_by": "dateFiled desc", "filed_after": _since(days).date().isoformat()})
    data = _json(f"https://www.courtlistener.com/api/rest/v4/search/?{q}", headers={"Authorization": f"Token {token}"})
    return [_item("courtlistener", "court", r.get("caseName", ""), "https://www.courtlistener.com" + r.get("absolute_url", ""),
                  r.get("dateFiled"), (r.get("opinions") or [{}])[0].get("snippet", ""), court=r.get("court"))
            for r in data.get("results", [])[:20]]


def manual_incidents(days):
    """Files dropped into data/incidents/ (front matter + text), e.g. a ruling a human flags."""
    out = []
    for f in sorted(MANUAL_DIR.glob("*.md")):
        text = f.read_text()
        meta = {}
        if text.startswith("---"):
            _, fm, body = text.split("---", 2)
            meta = yaml.safe_load(fm) or {}
        else:
            body = text
        it = _item("manual", meta.get("type", "incident"), meta.get("title") or body.strip().splitlines()[0][:200],
                   meta.get("source_url", f"file://{f.name}"), str(meta.get("date", "")), body.strip(),
                   jurisdiction=meta.get("jurisdiction"), perils=meta.get("perils"))
        it["id"] = f"manual:{meta.get('id', f.stem)}"
        out.append(it)
    return out


# The hourly default set (10 sources + manual drops). nvd, avid, gdelt and
# courtlistener are slower, rate-limited, or need a key: run them with --only or --all.
DEFAULT_SOURCES = ["federal_register", "regulations_gov", "congress_gov", "sec_press", "cfpb", "cisa_kev",
                   "github_advisories", "mitre_atlas", "arxiv", "edgar", "manual"]

CONNECTORS = {
    "federal_register": ("regulation", "Federal Register", federal_register),
    "regulations_gov": ("regulation", "Regulations.gov", regulations_gov),
    "congress_gov": ("regulation", "Congress.gov", congress_gov),
    "sec_press": ("enforcement", "SEC press releases", sec_press),
    "cfpb": ("enforcement", "CFPB newsroom", cfpb),
    "cisa_kev": ("security", "CISA Known Exploited Vulnerabilities", cisa_kev),
    "nvd": ("security", "National Vulnerability Database", nvd),
    "github_advisories": ("security", "GitHub Security Advisories", github_advisories),
    "mitre_atlas": ("incident", "MITRE ATLAS case studies", mitre_atlas),
    "avid": ("incident", "AI Vulnerability Database (AVID)", avid),
    "arxiv": ("research", "arXiv", arxiv),
    "gdelt": ("news", "GDELT news", gdelt),
    "edgar": ("disclosure", "SEC EDGAR full-text search", edgar_fulltext),
    "courtlistener": ("court", "CourtListener", courtlistener),
    "manual": ("incident", "Manual evidence (data/incidents/)", manual_incidents),
}


# ---------------------------------------------------------------- run

def load_store() -> dict[str, dict]:
    if not STORE.exists():
        return {}
    return {r["id"]: r for r in map(json.loads, STORE.read_text().splitlines()) if r}


def run(days: int = 30, only: list[str] | None = None, echo: bool = True, all_sources: bool = False) -> dict:
    only = only or (None if all_sources else DEFAULT_SOURCES)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    seen = load_store()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sources, new_items = {}, []
    for key, (kind, name, fn) in CONNECTORS.items():
        if only and key not in only:
            continue
        t = time.monotonic()
        try:
            items = fn(days)
            fresh = [dict(i, first_seen=now) for i in items if i["id"] not in seen]
            for i in fresh:
                seen[i["id"]] = i
            new_items += fresh
            sources[key] = {"name": name, "category": kind, "status": "ok", "fetched": len(items),
                            "new": len(fresh), "seconds": round(time.monotonic() - t, 1)}
        except Exception as e:  # noqa: BLE001 - connectors fail soft
            sources[key] = {"name": name, "category": kind, "status": "error", "error": str(e)[:200],
                            "fetched": 0, "new": 0, "seconds": round(time.monotonic() - t, 1)}
        if echo:
            s = sources[key]
            print(f"  {'✓' if s['status'] == 'ok' else '✗'} {name:<40} "
                  + (f"{s['fetched']:>3} fetched, {s['new']:>3} new" if s["status"] == "ok" else s["error"]))
    with open(STORE, "a") as f:
        for i in new_items:
            f.write(json.dumps(i) + "\n")
    summary = {"run_at": now, "window_days": days, "sources": sources, "new_items": new_items,
               "totals": {"sources_ok": sum(s["status"] == "ok" for s in sources.values()),
                          "sources": len(sources), "new": len(new_items), "store": len(seen)}}
    LATEST.write_text(json.dumps(summary, indent=2))
    return summary


def main():
    ap = argparse.ArgumentParser(prog="agents.monitor")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--only", help="Comma-separated connector keys")
    ap.add_argument("--all", action="store_true", help="Every connector, including slow and keyed ones")
    a = ap.parse_args()
    print("Monitor (Loop 2): gathering evidence")
    s = run(a.days, a.only.split(",") if a.only else None, all_sources=a.all)
    t = s["totals"]
    print(f"{t['sources_ok']}/{t['sources']} sources ok · {t['new']} new items · {t['store']} in store")


if __name__ == "__main__":
    main()
