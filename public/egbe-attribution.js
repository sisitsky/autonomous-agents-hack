/* Egbe analytics: first-touch attribution + campaign-visit (from GET /analytics/iacta/tracking) */
(function () {
  if (typeof window === "undefined" || window.__egbeAnalyticsEngagementInstalled) return;
  window.__egbeAnalyticsEngagementInstalled = true;
  var engagedTimeSeconds = 10;
  var scrollDepthThreshold = 50;
  var attributionEventName = "campaign-visit";
  var attributionStorageKey = "egbe_attribution_v1";
  var visitorCookieName = "egbe_visitor_id";
  var visitStorageKey = "egbe_visit_id";
  var visitSignatureStorageKey = "egbe_visit_signature";
  var activeMs = 0;
  var visibleSince = document.visibilityState === "hidden" ? 0 : Date.now();
  var sent = Object.create(null);
  var pending = [];
  var flushTimer = 0;
  var attributionState = null;
  var localStore = safeStorage("localStorage");
  var sessionStore = safeStorage("sessionStorage");

  function safeStorage(name) {
    try {
      return window[name] || null;
    } catch (_) {
      return null;
    }
  }

  function cleanString(value) {
    if (value == null) return null;
    var normalized = String(value).trim();
    return normalized ? normalized : null;
  }

  function parseJson(value) {
    if (!value) return null;
    try {
      return JSON.parse(value);
    } catch (_) {
      return null;
    }
  }

  function readCookie(name) {
    var prefix = name + "=";
    var entries = document.cookie ? document.cookie.split(/;\s*/) : [];
    for (var i = 0; i < entries.length; i += 1) {
      if (entries[i].indexOf(prefix) === 0) {
        return decodeURIComponent(entries[i].slice(prefix.length));
      }
    }
    return "";
  }

  function writeCookie(name, value) {
    document.cookie = name + "=" + encodeURIComponent(value) + "; Path=/; Max-Age=" + String(60 * 60 * 24 * 365) + "; SameSite=Lax";
  }

  function generateId(prefix) {
    return prefix + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function isoNow() {
    return new Date().toISOString();
  }

  function hasTouchAttribution(touch) {
    return !!(touch && (
      touch.utmSource ||
      touch.utmMedium ||
      touch.utmCampaign ||
      touch.utmContent ||
      touch.utmTerm ||
      touch.gclid ||
      touch.fbclid ||
      touch.msclkid ||
      touch.ttclid
    ));
  }

  function touchSignature(touch) {
    return [
      touch && touch.utmSource || "",
      touch && touch.utmMedium || "",
      touch && touch.utmCampaign || "",
      touch && touch.utmContent || "",
      touch && touch.utmTerm || "",
      touch && touch.gclid || "",
      touch && touch.fbclid || "",
      touch && touch.msclkid || "",
      touch && touch.ttclid || "",
    ].join("|");
  }

  function loadAttributionState() {
    if (!localStore) return {};
    var parsed = parseJson(localStore.getItem(attributionStorageKey));
    return parsed && typeof parsed === "object" ? parsed : {};
  }

  function saveAttributionState(state) {
    if (!localStore) return;
    try {
      localStore.setItem(attributionStorageKey, JSON.stringify(state));
    } catch (_) {}
  }

  function ensureVisitorId(state) {
    var existing = cleanString(state && state.visitorId) || cleanString(readCookie(visitorCookieName));
    if (!existing) {
      existing = generateId("visitor");
    }
    writeCookie(visitorCookieName, existing);
    return existing;
  }

  function ensureVisitId() {
    if (!sessionStore) return generateId("visit");
    var existing = cleanString(sessionStore.getItem(visitStorageKey));
    if (!existing) {
      existing = generateId("visit");
      sessionStore.setItem(visitStorageKey, existing);
    }
    return existing;
  }

  function buildTouch() {
    var search = new URLSearchParams(window.location.search || "");
    return {
      utmSource: cleanString(search.get("utm_source")),
      utmMedium: cleanString(search.get("utm_medium")),
      utmCampaign: cleanString(search.get("utm_campaign")),
      utmContent: cleanString(search.get("utm_content")),
      utmTerm: cleanString(search.get("utm_term")),
      gclid: cleanString(search.get("gclid")),
      fbclid: cleanString(search.get("fbclid")),
      msclkid: cleanString(search.get("msclkid")),
      ttclid: cleanString(search.get("ttclid")),
      landingPath: cleanString(window.location.pathname) || "/",
      referrer: cleanString(document.referrer),
      seenAt: isoNow(),
    };
  }

  function refreshAttributionState() {
    var touch = buildTouch();
    var state = attributionState && typeof attributionState === "object" ? attributionState : loadAttributionState();
    state.visitorId = ensureVisitorId(state);
    var currentVisitId = ensureVisitId();
    if (hasTouchAttribution(touch) && sessionStore) {
      var nextSignature = touchSignature(touch);
      var lastSignature = cleanString(sessionStore.getItem(visitSignatureStorageKey));
      if (nextSignature && lastSignature && nextSignature !== lastSignature) {
        currentVisitId = generateId("visit");
        sessionStore.setItem(visitStorageKey, currentVisitId);
      }
      if (nextSignature) {
        sessionStore.setItem(visitSignatureStorageKey, nextSignature);
      }
    }
    state.visitId = currentVisitId;
    if (!state.firstTouch) {
      state.firstTouch = touch;
    }
    if (!state.lastTouch || state.visitId !== state.lastTouch.visitId || (touchSignature(touch) && touchSignature(touch) !== touchSignature(state.lastTouch))) {
      touch.visitId = currentVisitId;
      state.lastTouch = touch;
    }
    attributionState = state;
    saveAttributionState(state);
    return state;
  }

  function publicAttributionState() {
    var state = refreshAttributionState();
    var firstTouch = state.firstTouch || null;
    var lastTouch = state.lastTouch || {};
    return {
      visitorId: cleanString(state.visitorId),
      visitId: cleanString(state.visitId),
      firstTouch: firstTouch,
      lastTouch: state.lastTouch || null,
      visitor_id: cleanString(state.visitorId),
      visit_id: cleanString(state.visitId),
      first_utm_source: firstTouch && firstTouch.utmSource || null,
      first_utm_medium: firstTouch && firstTouch.utmMedium || null,
      first_utm_campaign: firstTouch && firstTouch.utmCampaign || null,
      first_utm_content: firstTouch && firstTouch.utmContent || null,
      first_utm_term: firstTouch && firstTouch.utmTerm || null,
      first_gclid: firstTouch && firstTouch.gclid || null,
      first_fbclid: firstTouch && firstTouch.fbclid || null,
      first_msclkid: firstTouch && firstTouch.msclkid || null,
      first_ttclid: firstTouch && firstTouch.ttclid || null,
      first_landing_path: firstTouch && firstTouch.landingPath || null,
      first_referrer: firstTouch && firstTouch.referrer || null,
      first_seen_at: firstTouch && firstTouch.seenAt || null,
      last_utm_source: lastTouch.utmSource || null,
      last_utm_medium: lastTouch.utmMedium || null,
      last_utm_campaign: lastTouch.utmCampaign || null,
      last_utm_term: lastTouch.utmTerm || null,
      last_utm_content: lastTouch.utmContent || null,
      last_landing_path: lastTouch.landingPath || null,
      last_referrer: lastTouch.referrer || null,
    };
  }

  function buildCampaignVisitData() {
    var snapshot = publicAttributionState();
    var touch = snapshot.firstTouch;
    if (!hasTouchAttribution(touch)) return null;
    return {
      visitor_id: snapshot.visitorId,
      visit_id: snapshot.visitId,
      utm_source: touch && touch.utmSource || null,
      utm_medium: touch && touch.utmMedium || null,
      utm_campaign: touch && touch.utmCampaign || null,
      utm_content: touch && touch.utmContent || null,
      utm_term: touch && touch.utmTerm || null,
      gclid: touch && touch.gclid || null,
      fbclid: touch && touch.fbclid || null,
      msclkid: touch && touch.msclkid || null,
      ttclid: touch && touch.ttclid || null,
      landing_path: touch && touch.landingPath || null,
      referrer: attributionReferrerDomain(touch && touch.referrer),
      first_seen_at: snapshot.firstTouch && snapshot.firstTouch.seenAt || null,
      last_seen_at: snapshot.lastTouch && snapshot.lastTouch.seenAt || null,
    };
  }

  function attributionReferrerDomain(value) {
    if (!value) return null;
    try { return new URL(value).hostname; } catch (_) { return null; }
  }

  function buildAttributionEventData() {
    var snapshot = publicAttributionState();
    var first = snapshot.firstTouch || {};
    var last = snapshot.lastTouch || {};
    return {
      visitor_id: snapshot.visitorId || null,
      visit_id: snapshot.visitId || null,
      first_utm_source: first.utmSource || null,
      first_utm_medium: first.utmMedium || null,
      first_utm_campaign: first.utmCampaign || null,
      first_utm_content: first.utmContent || null,
      first_utm_term: first.utmTerm || null,
      last_utm_source: last.utmSource || null,
      last_utm_medium: last.utmMedium || null,
      last_utm_campaign: last.utmCampaign || null,
      last_utm_content: last.utmContent || null,
      last_utm_term: last.utmTerm || null,
      first_landing_path: first.landingPath || null,
      first_referrer: attributionReferrerDomain(first.referrer),
      first_seen_at: first.seenAt || null,
      last_landing_path: last.landingPath || null,
      last_referrer: attributionReferrerDomain(last.referrer),
    };
  }

  window.__egbeAttribution = {
    read: function () {
      return publicAttributionState();
    },
    eventData: function () {
      return buildAttributionEventData();
    },
    track: function (name, data) {
      if (!name) return false;
      var payload = Object.assign({}, buildAttributionEventData(), data && typeof data === "object" ? data : {});
      if (!hasTracker()) {
        pending.push({ key: "product-event:" + String(name) + ":" + Date.now(), name: name, data: payload });
        scheduleFlush();
        return false;
      }
      try { window.umami.track(name, payload); return true; } catch (_) { return false; }
    },
  };

  function hasTracker() {
    return !!(window.umami && typeof window.umami.track === "function");
  }

  function flushPending() {
    if (!hasTracker()) return false;
    while (pending.length) {
      var entry = pending.shift();
      if (!entry || sent[entry.key]) continue;
      try {
        window.umami.track(entry.name, entry.data);
        sent[entry.key] = true;
        if (entry.persistKey && sessionStore) {
          sessionStore.setItem(entry.persistKey, "1");
        }
      } catch (_) {
        pending.unshift(entry);
        return false;
      }
    }
    if (flushTimer) {
      window.clearInterval(flushTimer);
      flushTimer = 0;
    }
    return true;
  }

  function scheduleFlush() {
    if (flushTimer) return;
    var attempts = 0;
    flushTimer = window.setInterval(function () {
      attempts += 1;
      if (flushPending() || attempts >= 20) {
        window.clearInterval(flushTimer);
        flushTimer = 0;
      }
    }, 500);
  }

  function trackOnce(key, name, data, persistKey) {
    if (sent[key]) return;
    pending.push({ key: key, name: name, data: data || {}, persistKey: persistKey || null });
    if (!flushPending()) scheduleFlush();
  }

  function addVisibleTime() {
    if (!visibleSince) return;
    activeMs += Date.now() - visibleSince;
    visibleSince = Date.now();
  }

  function maybeTrackActiveTime() {
    if (sent["engaged-time"]) return;
    addVisibleTime();
    if (activeMs >= engagedTimeSeconds * 1000) {
      trackOnce("engaged-time", "engagement-time", { thresholdSeconds: engagedTimeSeconds });
    }
  }

  function computeScrollDepthPercent() {
    var doc = document.documentElement || {};
    var body = document.body || {};
    var scrollTop = window.pageYOffset || doc.scrollTop || body.scrollTop || 0;
    var viewportHeight = window.innerHeight || doc.clientHeight || body.clientHeight || 0;
    var pageHeight = Math.max(doc.scrollHeight || 0, body.scrollHeight || 0, viewportHeight);
    if (!pageHeight) return 100;
    var percent = ((scrollTop + viewportHeight) / pageHeight) * 100;
    if (percent < 0) return 0;
    if (percent > 100) return 100;
    return Math.round(percent);
  }

  function onScroll() {
    if (sent["scroll-depth"]) return;
    var scrollDepthPercent = computeScrollDepthPercent();
    if (scrollDepthPercent >= scrollDepthThreshold) {
      trackOnce("scroll-depth", "engagement-scroll", {
        thresholdPercent: scrollDepthThreshold,
        scrollDepthPercent: scrollDepthPercent,
      });
      window.removeEventListener("scroll", onScroll);
    }
  }

  function onInteraction(event) {
    if (sent["interaction-click"]) return;
    var target = event.target;
    if (!target || typeof target.closest !== "function") return;
    var interactive = target.closest("a[href],button,[role='button'],input[type='submit'],input[type='button']");
    if (!interactive) return;
    trackOnce("interaction-click", "engagement-click", {
      element: String((interactive.tagName || "unknown")).toLowerCase(),
    });
  }

  function onVisibilityChange() {
    if (document.visibilityState === "hidden") {
      maybeTrackActiveTime();
      visibleSince = 0;
      flushPending();
      return;
    }
    if (!visibleSince) visibleSince = Date.now();
  }

  function onPageHide() {
    refreshAttributionState();
    maybeTrackActiveTime();
    visibleSince = 0;
    flushPending();
  }

  document.addEventListener("visibilitychange", onVisibilityChange);
  document.addEventListener("click", onInteraction, true);
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("pagehide", onPageHide);

  var activeTimer = window.setInterval(function () {
    if (sent["engaged-time"]) {
      window.clearInterval(activeTimer);
      return;
    }
    if (document.visibilityState !== "hidden") maybeTrackActiveTime();
  }, 1000);

  function maybeTrackCampaignVisit() {
    var data = buildCampaignVisitData();
    if (!data) return;
    var signature = [
      data.utm_source || "",
      data.utm_medium || "",
      data.utm_campaign || "",
      data.utm_content || "",
      data.utm_term || "",
      data.gclid || "",
      data.fbclid || "",
      data.msclkid || "",
      data.ttclid || "",
    ].join("|");
    var persistKey = "campaign-visit:" + String(data.visit_id || "visit") + ":" + signature;
    if (sessionStore && sessionStore.getItem(persistKey) === "1") return;
    trackOnce(persistKey, attributionEventName, data, persistKey);
  }

  refreshAttributionState();
  onScroll();
  maybeTrackCampaignVisit();
  flushPending();
})();
