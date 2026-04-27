/**
 * AKDW Session/Tab Manager
 * Manages tab bar and saved hosts sidebar.
 */

const AKDW_Sessions = (() => {
  let tabCounter = 0;
  const tabs = {};

  function addTab(sessionId, hostname) {
    tabCounter += 1;
    const tabNumber = tabCounter;
    const label = tabNumber + '. ' + hostname;

    const tabEl = document.createElement('div');
    tabEl.className = 'terminal-tab connecting';
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

    document.getElementById('tabsList').appendChild(tabEl);
    tabs[sessionId] = {
      tabEl,
      hostname,
      number: tabNumber,
      status: 'connecting'
    };

    activateTab(sessionId);
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
      document.getElementById('tabsList').innerHTML =
        '<div class="no-sessions-hint">Click <strong>+ New Session</strong> or a saved host to connect</div>';
    }
  }

  function onConnected(sessionId) {
    const tab = tabs[sessionId];
    if (!tab) return;
    tab.status = 'connected';
    tab.tabEl.classList.remove('connecting', 'error', 'disconnected');
    tab.tabEl.classList.add('connected');
    const dot = tab.tabEl.querySelector('.tab-status-dot');
    if (dot) dot.textContent = 'o';
  }

  function onClosed(sessionId) {
    const tab = tabs[sessionId];
    if (!tab) return;
    tab.status = 'closed';
    tab.tabEl.classList.remove('connected', 'connecting', 'error');
    tab.tabEl.classList.add('disconnected');
    const dot = tab.tabEl.querySelector('.tab-status-dot');
    if (dot) dot.textContent = '-';
  }

  function onError(sessionId) {
    const tab = tabs[sessionId];
    if (!tab) return;
    tab.status = 'error';
    tab.tabEl.classList.remove('connected', 'connecting', 'disconnected');
    tab.tabEl.classList.add('error');
    const dot = tab.tabEl.querySelector('.tab-status-dot');
    if (dot) dot.textContent = '!';
  }

  async function loadSavedHosts() {
    try {
      const resp = await fetch('/api/terminal/hosts');
      const data = await resp.json();
      renderHostsList(data.hosts || []);
    } catch (err) {
      renderHostsList([]);
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
    } catch (err) {
      // no-op
    }
  }

  return {
    addTab,
    activateTab,
    closeTab,
    onConnected,
    onClosed,
    onError,
    loadSavedHosts,
    saveCurrentHost
  };
})();

async function deleteHost(hostId, event) {
  if (event) event.stopPropagation();
  await fetch('/api/terminal/hosts/' + encodeURIComponent(hostId), { method: 'DELETE' });
  AKDW_Sessions.loadSavedHosts();
}
