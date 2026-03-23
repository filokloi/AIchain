#!/usr/bin/env python3
"""Compact OpenClaw dashboard overlay bridge for AIchain controls."""

from __future__ import annotations

import json


_TEMPLATE = r'''(() => {
  const cfg = __AICHAIN_CONFIG__;
  if (window.__AICHAIN_OPENCLAW_BRIDGE__) return;
  window.__AICHAIN_OPENCLAW_BRIDGE__ = true;

  function startBridge() {
    if (!document.body) {
      window.requestAnimationFrame(startBridge);
      return;
    }

    function urlSessionId() {
      try {
        const url = new URL(window.location.href);
        return String(url.searchParams.get("session") || url.searchParams.get("session_id") || "").trim();
      } catch {
        return "";
      }
    }

    function resolveInitialSessionId() {
      return urlSessionId() || localStorage.getItem("aichain.openclaw.sessionId") || cfg.defaultSessionId;
    }

    const state = {
      sessionId: resolveInitialSessionId(),
      last: null,
      loading: false,
      error: "",
    };

    const root = document.createElement("div");
    root.id = "aichain-openclaw-overlay-root";
    document.body.appendChild(root);

    const style = document.createElement("style");
    style.textContent = `
    #aichain-openclaw-overlay-root {
      position: fixed;
      top: 14px;
      right: 16px;
      z-index: 2147483000;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #f6f7fb;
    }
    .aichain-shell {
      position: relative;
    }
    .aichain-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      max-width: 260px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(17, 22, 31, 0.94);
      border: 1px solid rgba(117, 141, 189, 0.35);
      box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
      backdrop-filter: blur(14px);
      cursor: pointer;
      user-select: none;
    }
    .aichain-chip:hover {
      background: rgba(24, 31, 45, 0.97);
    }
    .aichain-chip-dot {
        width: 9px;
        height: 9px;
        border-radius: 999px;
        background: #8fb3ff;
        box-shadow: 0 0 0 3px rgba(143, 179, 255, 0.16);
        flex: 0 0 auto;
      }
    .aichain-chip-dot.is-running {
        background: #9de0ff;
        box-shadow: 0 0 0 3px rgba(157, 224, 255, 0.22);
        animation: aichainPulse 1.05s ease-in-out infinite;
      }
    @keyframes aichainPulse {
        0% { transform: scale(1); opacity: 0.8; }
        50% { transform: scale(1.18); opacity: 1; }
        100% { transform: scale(1); opacity: 0.8; }
      }
    .aichain-chip-main {
      display: flex;
      min-width: 0;
      align-items: baseline;
      gap: 6px;
      flex: 1 1 auto;
    }
    .aichain-chip-title {
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #8fb3ff;
      font-weight: 700;
      flex: 0 0 auto;
    }
    .aichain-chip-sub {
      font-size: 12px;
      color: #eef3ff;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
      flex: 1 1 auto;
    }
    .aichain-chip-caret {
      color: #aeb8d1;
      font-size: 11px;
      flex: 0 0 auto;
    }
    .aichain-popover {
      position: absolute;
      top: calc(100% + 8px);
      right: 0;
      width: 320px;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(17, 22, 31, 0.97);
      border: 1px solid rgba(117, 141, 189, 0.35);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.34);
      backdrop-filter: blur(16px);
      opacity: 0;
      transform: translateY(-6px);
      pointer-events: none;
      transition: opacity 120ms ease, transform 120ms ease;
    }
    .aichain-shell.is-open .aichain-popover {
      opacity: 1;
      transform: translateY(0);
      pointer-events: auto;
    }
    .aichain-popover-title {
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #8fb3ff;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .aichain-popover-sub {
      font-size: 13px;
      color: #eef3ff;
      margin-bottom: 4px;
      line-height: 1.35;
    }
    .aichain-popover-meta {
      font-size: 12px;
      color: #aeb8d1;
      line-height: 1.45;
      margin-bottom: 10px;
    }
    .aichain-chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
    }
    .aichain-chip-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(56, 70, 97, 0.75);
      color: #dfe8ff;
      font-size: 11px;
      font-weight: 600;
    }
    .aichain-btn {
      appearance: none;
      border: 1px solid rgba(135, 161, 215, 0.4);
      background: rgba(35, 47, 72, 0.9);
      color: #ffffff;
      border-radius: 10px;
      font-size: 12px;
      font-weight: 600;
      padding: 8px 10px;
      cursor: pointer;
    }
    .aichain-btn:hover { background: rgba(53, 70, 104, 0.95); }
    .aichain-btn-row {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
    .aichain-error {
      margin-top: 6px;
      color: #ffb4b4;
      font-size: 11px;
      text-align: right;
      max-width: 320px;
    }
    `;
    document.head.appendChild(style);

    function readablePreference(pref) {
      switch ((pref || "balanced").toLowerCase()) {
        case "max_intelligence": return "Max intelligence";
        case "min_cost": return "Cheapest";
        case "prefer_local": return "Prefer local";
        default: return "Balanced";
      }
    }

    function currentLine(data) {
      const session = data.session || {};
      if ((session.request_status || "idle") === "running") {
        return session.request_label || "Thinking…";
      }
      if ((session.routing_mode || "auto") === "manual" && session.locked_model) {
        return session.locked_model;
      }
      if (data.recommended_current?.model) {
        return data.recommended_current.model;
      }
      return "Auto routing";
    }

    function metaLine(data) {
      const session = data.session || {};
      if ((session.request_status || "idle") === "running") {
        return "Request in progress · live routing active";
      }
      const parts = [];
      if ((session.routing_mode || "auto") === "auto") parts.push(readablePreference(session.routing_preference));
      if (data.recommended_current?.access_method) parts.push(`via ${data.recommended_current.access_method}`);
      if (data.recommended_current?.provider) parts.push(data.recommended_current.provider);
      if (state.sessionId) parts.push(`session ${state.sessionId}`);
      return parts.join(" · ") || "Open AIchain panel for details";
    }

    function syncSessionIdFromLocation() {
      const next = urlSessionId();
      if (!next || next === state.sessionId) return false;
      state.sessionId = next;
      try {
        localStorage.setItem("aichain.openclaw.sessionId", state.sessionId);
      } catch {}
      state.last = null;
      state.error = "";
      return true;
    }

    let refreshTimer = null;
    function scheduleRefresh() {
      if (refreshTimer) window.clearTimeout(refreshTimer);
      const session = state.last?.session || {};
      const isRunning = (session.request_status || "idle") === "running";
      const delay = state.loading ? 500 : (isRunning ? 800 : 1200);
      refreshTimer = window.setTimeout(refresh, delay);
    }

    async function api(path) {
      const response = await fetch(`${cfg.apiBase}${path}`, {
        headers: { "Content-Type": "application/json" },
        credentials: "omit",
        cache: "no-store",
      });
      const text = await response.text();
      let body = {};
      try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      return body;
    }

    async function refresh() {
      syncSessionIdFromLocation();
      state.loading = true;
      render();
      try {
        state.last = await api(`/control-state?session_id=${encodeURIComponent(state.sessionId)}`);
        state.error = "";
      } catch (err) {
        state.error = err.message || String(err);
      } finally {
        state.loading = false;
        render();
        scheduleRefresh();
      }
    }

    function openPanel() {
      const url = `${cfg.panelBase}?session_id=${encodeURIComponent(state.sessionId)}`;
      window.open(url, "_blank", "noopener,noreferrer");
    }

    let pinned = false;
    let hideTimer = null;

    function setOpen(open) {
      root.firstElementChild?.classList.toggle("is-open", Boolean(open));
    }

    function scheduleHide() {
      if (hideTimer) window.clearTimeout(hideTimer);
      hideTimer = window.setTimeout(() => {
        if (!pinned) setOpen(false);
      }, 120);
    }

    function render() {
      const data = state.last || {};
      const session = data.session || {};
      const modeLabel = (session.routing_mode || "auto") === "manual" ? "Locked" : "Auto";
      const current = state.loading ? "Loading…" : currentLine(data);
      const meta = metaLine(data);
      const isRunning = (session.request_status || "idle") === "running";
      const tags = [];
      if (isRunning) tags.push("Working");
      if ((session.routing_mode || "auto") === "auto") tags.push(readablePreference(session.routing_preference));
      if (data.recommended_current?.access_method) tags.push(`via ${data.recommended_current.access_method}`);
      if (data.recommended_current?.provider) tags.push(data.recommended_current.provider);

      root.innerHTML = `
      <div class="aichain-shell">
        <div class="aichain-chip" data-action="toggle-popover" title="AIchain routing controls">
          <span class="aichain-chip-dot${isRunning ? ' is-running' : ''}"></span>
          <div class="aichain-chip-main">
            <span class="aichain-chip-title">AIchain</span>
            <span class="aichain-chip-sub">${modeLabel}: ${current}</span>
          </div>
          <span class="aichain-chip-caret">▼</span>
        </div>
        <div class="aichain-popover" role="dialog" aria-label="AIchain controls">
          <div class="aichain-popover-title">AIchain</div>
          <div class="aichain-popover-sub">${current}</div>
          <div class="aichain-popover-meta">${meta}</div>
          <div class="aichain-chip-row">${tags.map((tag) => `<span class="aichain-chip-tag">${tag}</span>`).join("")}</div>
          <div class="aichain-btn-row">
            <button class="aichain-btn" data-action="open-panel">Open Panel</button>
          </div>
        </div>
      </div>
      ${state.error ? `<div class="aichain-error">${state.error}</div>` : ""}
      `;

      const shell = root.querySelector(".aichain-shell");
      const chip = root.querySelector('[data-action="toggle-popover"]');
      const popover = root.querySelector(".aichain-popover");
      const caret = root.querySelector(".aichain-chip-caret");

      function syncCaret() {
        caret.textContent = shell.classList.contains("is-open") ? "▲" : "▼";
      }

      chip?.addEventListener("mouseenter", () => {
        if (hideTimer) window.clearTimeout(hideTimer);
        setOpen(true);
        syncCaret();
      });
      chip?.addEventListener("mouseleave", () => {
        scheduleHide();
      });
      chip?.addEventListener("click", () => {
        const next = !shell.classList.contains("is-open");
        pinned = next;
        setOpen(next);
        syncCaret();
      });
      popover?.addEventListener("mouseenter", () => {
        if (hideTimer) window.clearTimeout(hideTimer);
        setOpen(true);
        syncCaret();
      });
      popover?.addEventListener("mouseleave", () => {
        scheduleHide();
      });
      root.querySelector('[data-action="open-panel"]')?.addEventListener("click", (event) => {
        event.stopPropagation();
        openPanel();
      });
      syncCaret();
    }

    window.addEventListener("popstate", refresh);
    window.addEventListener("hashchange", refresh);
    refresh();
  }

  startBridge();
})();'''


def build_openclaw_bridge_script(*, api_base: str, token: str, default_session_id: str, panel_base: str) -> str:
    cfg = json.dumps({
        "apiBase": api_base.rstrip("/"),
        "defaultSessionId": default_session_id,
        "panelBase": panel_base,
    })
    return _TEMPLATE.replace('__AICHAIN_CONFIG__', cfg)
