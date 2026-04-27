/**
 * AKDW Session/Tab Manager
 * Manages tab bar and saved hosts sidebar.
 */

const AKDW_Sessions = (() => {
  let tabCounter = 0;
  const tabs = {};
  let initialized = false;

  function addTab(sessionId, hostname, options = {}) {
    if (!sessionId) return null;
    if (tabs[sessionId]) {
      if (options.activate !== false) activateTab(sessionId);
      return tabs[sessionId];
    }

    tabCounter += 1;
    const tabNumber = tabCounter;
    const label = tabNumber + '. ' + (hostname || 'session');

    const tabEl = document.createElement('div');
    tabEl.className = 'terminal-tab';
    tabEl.dataset.sessionId = sessionId;
    tabEl.innerHTML = [
      '<span class="tab-status-dot">...</span>',
      '<span class="tab-label"></span>',
      '<button class="tab-close" title="Close session">x</button>'
    ].join('');

    tabEl.querySelector('.tab-label').textContent = label;
    tabEl.querySelector('.tab-close').addEventListener('click', (event) => {
      closeTab(sessionId, event);
    });
    tabEl.addEventListener('click', (event) => {
      if (event.target.classList.contains('tab-close')) return;
      activateTab(sessionId);
    });

    const hint = document.querySelector('.no-sessions-hint');
    if (hint) hint.remove();

    const tabsList = document.getElementById('tabsList');
    if (tabsList) tabsList.appendChild(tabEl);

    tabs[sessionId] = {
      tabEl,
      hostname: hostname || 'session',
      number: tabNumber,
      status: 'connecting'
    };

    setTabState(sessionId, options.status || 'connecting');

    if (options.activate !== false) {
      activateTab(sessionId);
    }

    return tabs[sessionId];
  }

  function hasTab(sessionId) {
    return Boolean(tabs[sessionId]);
  }

  function activateTab(sessionId) {
    Object.keys(tabs).forEach((sid) => {
      tabs[sid].tabEl.classList.remove('active');
    });

    const tab = tabs[sessionId];
    if (!tab) return;
    tab.tabEl.classList.add('active');
    AKDW_Terminal.showSession(sessionId);
  }

  function closeTab(sessionId, event) {
    if (event) event.stopPropagation();

    const tab = tabs[sessionId];
    if (tab) {
      tab.tabEl.remove();
      delete tabs[sessionId];
    }

    AKDW_Terminal.closeSession(sessionId);

    if (Object.keys(tabs).length === 0) {
      const tabsList = document.getElementById('tabsList');
      if (tabsList) {
        tabsList.innerHTML = '<div class="no-sessions-hint">Click <strong>+ New Session</strong> or a saved host to connect</div>';
      }
    }
  }

  function onConnected(sessionId) {
    setTabState(sessionId, 'connected');
  }

  function onClosed(sessionId) {
    setTabState(sessionId, 'disconnected');
  }

  function onError(sessionId) {
    setTabState(sessionId, 'error');
  }

  function setTabState(sessionId, state) {
    const tab = tabs[sessionId];
    if (!tab) return;

    tab.status = state;
    tab.tabEl.classList.remove('connected', 'connecting', 'disconnected', 'error');
    tab.tabEl.classList.add(state);

    const dot = tab.tabEl.querySelector('.tab-status-dot');
    if (!dot) return;
    if (state === 'connected') dot.textContent = 'o';
    else if (state === 'connecting') dot.textContent = '...';
    else if (state === 'disconnected') dot.textContent = '-';
    else if (state === 'error') dot.textContent = '!';
  }

  async function loadSavedHosts() {
    const container = document.getElementById('hostsList');
    if (container && container.innerHTML.indexOf('host-item') === -1) {
      container.innerHTML = '<div class="host-loading">Loading hosts...</div>';
    }

    try {
      const resp = await fetch('/api/terminal/hosts', { cache: 'no-store' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const contentType = (resp.headers.get('content-type') || '').toLowerCase();
      if (contentType.indexOf('application/json') === -1) {
        throw new Error('Non-JSON response (possible setup redirect/session timeout)');
      }
      const data = await resp.json();
      renderHostsList(Array.isArray(data.hosts) ? data.hosts : []);
    } catch (err) {
      renderHostsError(err && err.message ? err.message : 'Failed to load saved hosts');
    }
  }

  function renderHostsList(hosts) {
    const container = document.getElementById('hostsList');
    if (!container) return;

    if (!hosts.length) {
      container.innerHTML = '<div class="no-hosts-hint">No saved hosts yet.<br/>Click + New Session to add one.</div>';
      return;
    }

    container.innerHTML = '';
    hosts.forEach((h) => {
      const item = document.createElement('div');
      item.className = 'host-item';
      item.dataset.hostId = String(h.id);
      item.title = 'Connect to ' + h.hostname;
      item.addEventListener('click', () => quickConnect(h.hostname));

      const icon = document.createElement('span');
      icon.className = 'host-icon';
      icon.textContent = '>'; 

      const details = document.createElement('div');
      details.className = 'host-details';

      const label = document.createElement('span');
      label.className = 'host-label';
      label.textContent = h.label || h.hostname;

      const sub = document.createElement('span');
      sub.className = 'host-sub';
      sub.textContent = (h.username || '') + '@' + h.hostname + ':' + String(h.port || 22);

      details.appendChild(label);
      details.appendChild(sub);

      const del = document.createElement('button');
      del.className = 'host-delete';
      del.title = 'Remove host';
      del.textContent = 'x';
      del.addEventListener('click', (event) => deleteHost(h.id, event));

      item.appendChild(icon);
      item.appendChild(details);
      item.appendChild(del);
      container.appendChild(item);
    });
  }

  function renderHostsError(message) {
    const container = document.getElementById('hostsList');
    if (!container) return;
    container.innerHTML = [
      '<div class="hosts-error">',
      '<div class="hosts-error-title">Could not load hosts</div>',
      '<div class="hosts-error-msg">' + escapeHtml(message) + '</div>',
      '<button class="btn-secondary hosts-retry" onclick="refreshHostsList()">Retry</button>',
      '</div>'
    ].join('');
  }

  async function restoreActiveSessions() {
    try {
      const resp = await fetch('/api/terminal/sessions', { cache: 'no-store' });
      if (!resp.ok) return;
      const data = await resp.json();
      const sessions = Array.isArray(data.sessions) ? data.sessions : [];
      const active = sessions.filter((s) => Boolean(s.active));
      if (!active.length) return;

      active.forEach((s, idx) => {
        const sid = s.session_id;
        const host = s.hostname || 'session';
        addTab(sid, host, { status: 'connected', activate: idx === active.length - 1 });
        AKDW_Terminal.attachSession(sid, {
          hostname: s.hostname || '',
          username: s.username || ''
        });
      });
    } catch (_err) {
      // no-op
    }
  }

  async function deleteHost(hostId, event) {
    if (event) event.stopPropagation();
    await fetch('/api/terminal/hosts/' + encodeURIComponent(hostId), { method: 'DELETE' });
    loadSavedHosts();
  }

  async function saveCurrentHost(hostname, port, username, label) {
    try {
      await fetch('/api/terminal/hosts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hostname, port, username, label: label || hostname })
      });
      loadSavedHosts();
    } catch (_err) {
      // no-op
    }
  }

  function initialize() {
    if (initialized) return;
    initialized = true;

    loadSavedHosts();
    restoreActiveSessions();

    window.setInterval(() => {
      loadSavedHosts();
    }, 60000);

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        loadSavedHosts();
        restoreActiveSessions();
      }
    });
  }

  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  return {
    addTab,
    hasTab,
    activateTab,
    closeTab,
    onConnected,
    onClosed,
    onError,
    loadSavedHosts,
    restoreActiveSessions,
    saveCurrentHost,
    initialize
  };
})();
window.AKDW_Sessions = AKDW_Sessions;

async function deleteHost(hostId, event) {
  if (event) event.stopPropagation();
  await fetch('/api/terminal/hosts/' + encodeURIComponent(hostId), { method: 'DELETE' });
  AKDW_Sessions.loadSavedHosts();
}
