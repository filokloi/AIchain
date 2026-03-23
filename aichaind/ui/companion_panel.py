#!/usr/bin/env python3
"""Standalone local AIchain companion panel served by aichaind."""

from __future__ import annotations

import json


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AIchain Control Panel</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0d1118;
      --border: rgba(128, 150, 198, 0.28);
      --text: #f3f7ff;
      --muted: #b8c4dd;
      --accent: #8fb3ff;
      --accent-2: #d7e4ff;
      --danger: #ffb4b4;
      --ok: #92e0ae;
      --warn: #ffd596;
      --panel: rgba(18, 24, 34, 0.92);
      --card: rgba(27, 35, 50, 0.78);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(94, 126, 195, 0.22), transparent 34%),
        radial-gradient(circle at bottom right, rgba(63, 101, 171, 0.18), transparent 28%),
        var(--bg);
      color: var(--text);
    }
    .shell { width: min(1240px, calc(100vw - 40px)); margin: 28px auto; }
    .hero {
      display: flex; justify-content: space-between; gap: 16px; align-items: flex-start;
      margin-bottom: 20px; padding: 22px 24px; border: 1px solid var(--border);
      border-radius: 22px; background: rgba(19, 26, 37, 0.92);
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
    }
    .hero-title { margin: 0 0 8px 0; font-size: 13px; letter-spacing: 0.09em; text-transform: uppercase; color: var(--accent); }
    .hero-main { margin: 0; font-size: clamp(24px, 4vw, 38px); line-height: 1.05; }
    .hero-sub { margin: 10px 0 0; color: var(--muted); max-width: 62ch; line-height: 1.5; font-size: 14px; }
    .hero-stat {
      min-width: 280px; padding: 16px 18px; border-radius: 18px;
      border: 1px solid var(--border); background: rgba(27, 35, 50, 0.85);
    }
    .hero-stat-label { color: var(--accent); text-transform: uppercase; letter-spacing: 0.08em; font-size: 11px; margin-bottom: 6px; font-weight: 700; }
    .hero-stat-value { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
    .hero-stat-meta { color: var(--muted); font-size: 12px; line-height: 1.5; }
    .grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.95fr); gap: 18px; }
    .stack { display: flex; flex-direction: column; gap: 18px; }
    .panel {
      border: 1px solid var(--border); border-radius: 20px; background: var(--panel);
      padding: 18px; box-shadow: 0 20px 45px rgba(0, 0, 0, 0.24);
    }
    .panel-title { margin: 0 0 12px 0; color: var(--accent); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
    .panel-kicker { color: var(--muted); font-size: 13px; line-height: 1.45; }
    .status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .triple-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
    .stat-card, .option-card, .list-card {
      border: 1px solid var(--border); border-radius: 16px; background: var(--card); padding: 14px;
    }
    .stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent); margin-bottom: 8px; font-weight: 700; }
    .stat-value { font-size: 16px; font-weight: 700; margin-bottom: 5px; }
    .stat-meta { color: var(--muted); font-size: 12px; line-height: 1.5; }
    .list { display: flex; flex-direction: column; gap: 10px; }
    .section-list { display: flex; flex-direction: column; gap: 16px; }
    .section-list h3 { margin: 0 0 8px 0; font-size: 13px; color: var(--accent-2); }
    .option-card h4, .list-card h4 { margin: 0 0 6px 0; font-size: 14px; }
    .list-card p, .option-card p, .option-card li { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
    .item-top { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 8px; }
    .status-pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
    .status-runtime_confirmed { background: rgba(60, 118, 84, 0.28); color: var(--ok); }
    .status-degraded_fallback, .status-target_form_not_reached { background: rgba(120, 89, 31, 0.28); color: var(--warn); }
    .status-blocked_missing_credentials, .status-disabled { background: rgba(115, 46, 46, 0.28); color: var(--danger); }
    .chip-row, .action-row { display: flex; flex-wrap: wrap; gap: 10px; }
    .chip {
      display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px;
      background: rgba(64, 83, 117, 0.66); color: var(--accent-2); font-size: 12px; font-weight: 600;
    }
    .btn {
      appearance: none; border: 1px solid rgba(138, 163, 214, 0.45); background: rgba(35, 49, 75, 0.95);
      color: white; border-radius: 12px; padding: 10px 12px; font-size: 13px; font-weight: 700; cursor: pointer;
    }
    .btn:hover { background: rgba(52, 72, 109, 1); }
    .btn.secondary { background: rgba(31, 38, 50, 0.95); }
    .btn.full { width: 100%; text-align: left; }
    .note { font-size: 12px; color: var(--muted); line-height: 1.55; }
    .error { color: var(--danger); font-size: 13px; }
    .confirmation { color: var(--ok); font-size: 13px; font-weight: 600; }
    .bullets { margin: 8px 0 0 18px; padding: 0; }
    .bullets li { margin-bottom: 6px; }
    @media (max-width: 1080px) {
      .grid { grid-template-columns: 1fr; }
      .hero { flex-direction: column; }
      .hero-stat { width: 100%; }
      .status-grid, .triple-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <div class="hero-title">AIchain Control Panel</div>
        <h1 class="hero-main">Session routing, access, limits and local models.</h1>
        <p class="hero-sub">Global catalog remains the baseline truth. This panel shows your runtime reality: current route, premium access, local models, fallback paths and session controls.</p>
      </div>
      <div class="hero-stat">
        <div class="hero-stat-label">Current Session</div>
        <div class="hero-stat-value" id="hero-current">Loading…</div>
        <div class="hero-stat-meta" id="hero-meta">Fetching AIchain runtime state.</div>
      </div>
    </section>
    <div class="grid">
      <div class="stack">
        <section class="panel">
          <h2 class="panel-title">Session Control</h2>
          <div class="panel-kicker">Switch between automatic routing, manual model lock and routing preference modes.</div>
          <div class="status-grid" id="session-grid" style="margin-top: 14px;"></div>
          <div class="action-row" style="margin-top: 14px;">
            <button class="btn" data-action="set-auto">Auto routing</button>
            <button class="btn secondary" data-action="pref-max">Prefer max intelligence</button>
            <button class="btn secondary" data-action="pref-cheap">Prefer cheapest</button>
            <button class="btn secondary" data-action="pref-local">Prefer local</button>
          </div>
          <div class="note" style="margin-top: 12px;">Changes are session-scoped and persisted for the active AIchain session.</div>
          <div id="confirmation" class="confirmation" style="margin-top: 10px;"></div>
          <div id="error" class="error" style="margin-top: 10px;"></div>
        </section>
        <section class="panel">
          <h2 class="panel-title">Why This Model</h2>
          <div class="panel-kicker">Short explanation of the current route, including fallback and active session preference.</div>
          <div id="why-route" style="margin-top: 14px;"></div>
        </section>
        <section class="panel">
          <h2 class="panel-title">Recommended Now</h2>
          <div class="panel-kicker">Best currently available candidates after global ranking, access rules, limits and local runtime checks.</div>
          <div class="list" id="recommended-list" style="margin-top: 14px;"></div>
        </section>
        <section class="panel">
          <h2 class="panel-title">Provider Access & Limits</h2>
          <div class="panel-kicker">Official support, factual runtime state, billing basis, quota visibility and fallback path.</div>
          <div class="list" id="access-list" style="margin-top: 14px;"></div>
        </section>
      </div>
      <div class="stack">
        <section class="panel">
          <h2 class="panel-title">Savings & Limits</h2>
          <div class="panel-kicker">Qualitative savings view and limit visibility for the currently selected route.</div>
          <div id="savings-grid" class="triple-grid" style="margin-top: 14px;"></div>
        </section>
        <section class="panel">
          <h2 class="panel-title">Model Picker</h2>
          <div class="panel-kicker">Manually lock a verified model or inspect why a model is available right now.</div>
          <div id="model-groups" class="section-list" style="margin-top: 14px;"></div>
        </section>
        <section class="panel">
          <h2 class="panel-title">Local Models</h2>
          <div class="panel-kicker">Machine-specific runtime truth. This is not part of the public catalog.</div>
          <div id="local-model-card" style="margin-top: 14px;"></div>
        </section>
      </div>
    </div>
  </div>
  <script>
    const cfg = __AICHAIN_CONFIG__;
    const state = {
      sessionId: new URLSearchParams(window.location.search).get("session_id") || localStorage.getItem("aichain.openclaw.sessionId") || cfg.defaultSessionId,
      last: null,
      loading: false,
    };
    localStorage.setItem("aichain.openclaw.sessionId", state.sessionId);

    function readablePreference(pref) {
      switch ((pref || "balanced").toLowerCase()) {
        case "max_intelligence": return "Max intelligence";
        case "min_cost": return "Cheapest";
        case "prefer_local": return "Prefer local";
        default: return "Balanced";
      }
    }
    function statusClass(mode) { return `status-${(mode || "disabled").replace(/[^a-z0-9_]+/gi, "_")}`; }
    function titleForGroup(group) {
      switch (group) {
        case 'manual': return 'Manual lock';
        case 'premium_access': return 'Premium access';
        case 'workspace': return 'Workspace / enterprise';
        case 'local': return 'Local models';
        case 'api_access': return 'API access';
        default: return 'Other options';
      }
    }
    async function api(path, options = {}) {
      const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
      const response = await fetch(`${cfg.apiBase}${path}`, Object.assign({ headers, credentials: "omit", cache: "no-store" }, options));
      const text = await response.text();
      let body = {};
      try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      return body;
    }
    let refreshTimer = null;
    function scheduleRefresh() {
      if (refreshTimer) window.clearTimeout(refreshTimer);
      const session = state.last?.session || {};
      const isRunning = (session.request_status || 'idle') === 'running';
      const delay = state.loading ? 500 : (isRunning ? 800 : 1200);
      refreshTimer = window.setTimeout(refresh, delay);
    }
    async function refresh() {
      state.loading = true;
      render();
      try { state.last = await api(`/control-state?session_id=${encodeURIComponent(state.sessionId)}`); }
      catch (err) { state.last = state.last || {}; state.last.error = err.message || String(err); }
      finally { state.loading = false; render(); scheduleRefresh(); }
    }
    async function applyControl(control) {
      state.loading = true;
      render();
      try {
        state.last = await api('/control', { method: 'POST', body: JSON.stringify({ session_id: state.sessionId, persist_for_session: true, ...control }) });
      } catch (err) {
        state.last = state.last || {};
        state.last.error = err.message || String(err);
      } finally {
        state.loading = false;
        render();
      }
    }
    function renderSession(session, recommended, confirmation, error) {
      const running = (session.request_status || 'idle') === 'running';
      const currentLabel = running
        ? (session.request_label || 'Thinking…')
        : (recommended?.label || recommended?.model || session.locked_model || 'Auto routing');
      document.getElementById('session-grid').innerHTML = `
        <div class="stat-card">
          <div class="stat-label">Routing Mode</div>
          <div class="stat-value">${(session.routing_mode || 'auto').toUpperCase()}</div>
          <div class="stat-meta">Preference: ${readablePreference(session.routing_preference)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Current Route</div>
          <div class="stat-value">${running ? (session.request_label || 'Thinking…') : (recommended?.model || session.locked_model || 'Not resolved yet')}</div>
          <div class="stat-meta">${running ? 'Request in progress' : `${recommended?.provider || session.locked_provider || 'provider pending'}${recommended?.access_method ? ` · via ${recommended.access_method}` : ''}`}</div>
        </div>`;
      document.getElementById('hero-current').textContent = currentLabel;
      document.getElementById('hero-meta').textContent = running
        ? `Session ${state.sessionId} · request in progress`
        : `Session ${state.sessionId} · ${readablePreference(session.routing_preference)}${recommended?.provider ? ` · ${recommended.provider}` : ''}`;
      document.getElementById('confirmation').textContent = confirmation || '';
      document.getElementById('error').textContent = error || '';
    }
    function renderWhy(why, recommended) {
      const root = document.getElementById('why-route');
      const bullets = why?.bullets?.length ? `<ul class="bullets">${why.bullets.map(item => `<li>${item}</li>`).join('')}</ul>` : '';
      root.innerHTML = `
        <div class="list-card">
          <div class="item-top">
            <div>
              <h4>${why?.title || 'Why this model?'}</h4>
              <p>${why?.summary || 'Current route selected from catalog ranking and runtime availability.'}</p>
            </div>
            <span class="status-pill ${statusClass(recommended?.status || 'runtime_confirmed')}">${recommended?.status || 'runtime_confirmed'}</span>
          </div>
          ${bullets}
        </div>`;
    }
    function renderRecommended(models) {
      const root = document.getElementById('recommended-list');
      if (!models?.length) { root.innerHTML = `<div class="note">No runtime-confirmed route candidates yet.</div>`; return; }
      root.innerHTML = models.map((model) => `
        <div class="list-card">
          <div class="item-top">
            <div><h4>${model.label}</h4><p>${model.model} · ${model.provider} · ${model.access_method || 'access metadata pending'}</p></div>
            <span class="status-pill ${statusClass(model.status || 'runtime_confirmed')}">${model.status || 'runtime_confirmed'}</span>
          </div>
          <p>${model.effective_cost_label || 'Cost not estimated'}${model.badges?.length ? ` · ${model.badges.join(', ')}` : ''}</p>
        </div>`).join('');
    }
    function renderSavings(savings, access) {
      const root = document.getElementById('savings-grid');
      root.innerHTML = `
        <div class="stat-card">
          <div class="stat-label">Savings Signal</div>
          <div class="stat-value">${savings?.headline || 'No savings summary yet'}</div>
          <div class="stat-meta">${savings?.detail || ''}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Cost Mode</div>
          <div class="stat-value">${savings?.cost_mode_label || savings?.cost_mode || 'catalog-ranked route'}</div>
          <div class="stat-meta">Quota visibility: ${savings?.quota_visibility_label || savings?.quota_visibility || access?.quota_visibility || 'provider-specific or not machine-readable yet'}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Fallback</div>
          <div class="stat-value">${savings?.fallback_label || access?.fallback_path || 'Automatic fallback to the next ranked runtime-confirmed route'}</div>
          <div class="stat-meta">Status: ${savings?.status_label || access?.status || 'route metadata pending'}</div>
        </div>`;
    }
    function renderModelPicker(models) {
      const root = document.getElementById('model-groups');
      if (!models?.length) { root.innerHTML = `<div class="note">No model options exposed by the current runtime state.</div>`; return; }
      const groups = {};
      for (const model of models) {
        const group = model.group || 'api_access';
        groups[group] = groups[group] || [];
        groups[group].push(model);
      }
      const ordered = ['manual', 'premium_access', 'api_access', 'local', 'workspace'];
      root.innerHTML = ordered.filter(key => groups[key]?.length).map((group) => `
        <section>
          <h3>${titleForGroup(group)}</h3>
          <div class="list">${groups[group].map((model) => `
            <div class="option-card">
              <div class="item-top">
                <div><h4>${model.label}</h4><p>${model.model}</p></div>
                <span class="status-pill ${statusClass(model.status || 'runtime_confirmed')}">${model.status || 'runtime_confirmed'}</span>
              </div>
              <p>${model.provider} · ${model.access_method || 'access metadata pending'}${model.effective_cost_label ? ` · ${model.effective_cost_label}` : ''}</p>
              <div class="chip-row" style="margin-top: 8px;">${(model.badges || []).map(tag => `<span class="chip">${tag}</span>`).join('')}</div>
              <div class="action-row" style="margin-top: 12px;"><button class="btn full" data-action="lock" data-model="${model.model}" data-provider="${model.provider}">Lock to ${model.label}</button></div>
            </div>`).join('')}</div>
        </section>`).join('');
      root.querySelectorAll('[data-action="lock"]').forEach((button) => {
        button.addEventListener('click', () => applyControl({ mode: 'manual', model: button.dataset.model || '', provider: button.dataset.provider || '' }));
      });
    }
    function renderAccess(access) {
      const root = document.getElementById('access-list');
      const items = Object.entries(access || {}).sort((a, b) => a[0].localeCompare(b[0]));
      if (!items.length) { root.innerHTML = `<div class="note">No provider access state captured yet.</div>`; return; }
      root.innerHTML = items.map(([provider, item]) => `
        <div class="list-card">
          <div class="item-top">
            <div><h4>${provider}</h4><p>${item.method || 'disabled'} · ${item.billing_basis || 'billing details not exported yet'}</p></div>
            <span class="status-pill ${statusClass(item.status || 'disabled')}">${item.status || 'disabled'}</span>
          </div>
          <p>Official: ${item.official_support ? 'yes' : 'no'} · Quota: ${item.quota_visibility || 'provider-specific or not machine-readable yet'}${item.limit_type ? ` · ${item.limit_type}` : ''}</p>
          <p>Fallback: ${item.fallback_path || 'AIchain will fall back to the next ranked usable route.'}${item.reason ? ` · ${item.reason}` : ''}</p>
        </div>`).join('');
    }
    function renderLocal(localProfiles) {
      const root = document.getElementById('local-model-card');
      const active = localProfiles?.active_profile;
      if (!active) { root.innerHTML = `<div class="note">No active local profile loaded for this machine.</div>`; return; }
      root.innerHTML = `
        <div class="list-card">
          <div class="item-top">
            <div><h4>${active.model}</h4><p>${active.capacity_status || 'capacity unknown'} · safe timeout ${active.safe_timeout_ms || 'n/a'} ms</p></div>
            <span class="status-pill ${statusClass(active.runtime_confirmed ? 'runtime_confirmed' : 'target_form_not_reached')}">${active.runtime_confirmed ? 'runtime_confirmed' : 'target_form_not_reached'}</span>
          </div>
          <p>Suitability: coding ${active.prompt_type_suitability?.coding ?? 'n/a'} · reasoning ${active.prompt_type_suitability?.reasoning ?? 'n/a'} · structured ${active.prompt_type_suitability?.structured_output ?? 'n/a'}</p>
          <p>Success rate: ${active.success_rate ?? 'n/a'} · tokens/s ${active.average_tokens_per_second ?? 'n/a'} · TTFT ${active.average_ttft_ms ?? 'n/a'} ms</p>
        </div>`;
    }
    function bindActions() {
      document.querySelector('[data-action="set-auto"]')?.addEventListener('click', () => applyControl({ mode: 'auto' }));
      document.querySelector('[data-action="pref-max"]')?.addEventListener('click', () => applyControl({ mode: 'auto', routing_preference: 'max_intelligence' }));
      document.querySelector('[data-action="pref-cheap"]')?.addEventListener('click', () => applyControl({ mode: 'auto', routing_preference: 'min_cost' }));
      document.querySelector('[data-action="pref-local"]')?.addEventListener('click', () => applyControl({ mode: 'auto', routing_preference: 'prefer_local' }));
    }
    function render() {
      const data = state.last || {};
      const session = data.session || {};
      renderSession(session, data.recommended_current, data.last_confirmation, data.error);
      renderWhy(data.why_this_route || {}, data.recommended_current || {});
      renderRecommended(data.model_options || []);
      renderSavings(data.savings_summary || {}, (data.provider_access || {})[(data.recommended_current || {}).provider || ''] || {});
      renderModelPicker(data.model_options || []);
      renderAccess(data.provider_access || {});
      renderLocal(data.local_profiles || {});
      bindActions();
    }
    refresh();
  </script>
</body>
</html>
"""


def build_companion_panel_html(*, api_base: str, token: str, default_session_id: str) -> str:
    cfg = json.dumps({
        "apiBase": api_base.rstrip("/"),
        "defaultSessionId": default_session_id,
    })
    return _HTML_TEMPLATE.replace("__AICHAIN_CONFIG__", cfg)
