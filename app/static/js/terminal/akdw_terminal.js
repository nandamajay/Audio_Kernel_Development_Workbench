/**
 * AKDW Terminal Core
 * Manages xterm.js Terminal instances mapped to SSH sessions via SocketIO.
 */

const AKDW_Terminal = (() => {
  const terminals = {};
  const pendingConnectTimers = {};
  let socket = null;
  let activeSessionId = null;

  const MIN_FONT_SIZE = 10;
  const MAX_FONT_SIZE = 24;
  const FONT_STORAGE_KEY = 'akdw_terminal_font_size';
  let currentFontSize = 13;

  const MOBATERM_THEME = {
    background: '#0D1117',
    foreground: '#E6EDF3',
    cursor: '#58A6FF',
    cursorAccent: '#0D1117',
    black: '#484F58',
    red: '#FF7B72',
    green: '#3FB950',
    yellow: '#D29922',
    blue: '#58A6FF',
    magenta: '#BC8CFF',
    cyan: '#39C5CF',
    white: '#B1BAC4',
    brightBlack: '#6E7681',
    brightRed: '#FFA198',
    brightGreen: '#56D364',
    brightYellow: '#E3B341',
    brightBlue: '#79C0FF',
    brightMagenta: '#D2A8FF',
    brightCyan: '#56D4DD',
    brightWhite: '#F0F6FC',
    selectionBackground: '#264F78'
  };

  function isDrawerInputActive() {
    const active = document.activeElement;
    if (!active) return window.AKDW_INPUT_FOCUS === 'drawer';
    if (window.AKDW_INPUT_FOCUS === 'drawer') return true;
    if (active.id === 'drawerInput') return true;
    return Boolean(active.closest && active.closest('#qgenieDrawer'));
  }

  function initSocket() {
    if (socket) return socket;

    socket = io({
      transports: ['polling', 'websocket'],
      upgrade: true,
      reconnection: true,
      reconnectionAttempts: 6,
      timeout: 10000
    });

    socket.on('connect', () => {
      // no-op: connection is used by emitWithSocketReady when needed
    });

    socket.on('disconnect', (reason) => {
      showConnectError('Socket disconnected: ' + (reason || 'unknown'));
    });

    socket.on('connect_error', (err) => {
      showConnectError('Socket.IO connection failed: ' + ((err && err.message) || 'transport error'));
    });

    socket.on('terminal_output', ({ session_id, data }) => {
      const t = terminals[session_id];
      if (!t) return;
      t.term.write(data || '');
      updateStatusFromOutput(session_id, data || '');
    });

    socket.on('terminal_connected', ({ session_id, hostname, username, message }) => {
      clearPendingConnect(session_id);
      hideWelcomeScreen();
      updateStatusBar(session_id, hostname, username);
      if (window.AKDW_Sessions) {
        AKDW_Sessions.onConnected(session_id, hostname);
      }

      const status = document.getElementById('connect-status');
      if (status) {
        status.textContent = message || 'Connected to ' + username + '@' + hostname;
        status.className = 'connect-status info';
        status.classList.remove('hidden');
      }
      closeConnectModal();
    });

    socket.on('terminal_closed', ({ session_id, message }) => {
      clearPendingConnect(session_id);
      const t = terminals[session_id];
      if (t) {
        t.term.writeln('\r\n\x1b[33m[Session closed: ' + (message || 'Disconnected') + ']\x1b[0m\r\n');
      }
      if (window.AKDW_Sessions) {
        AKDW_Sessions.onClosed(session_id);
      }
    });

    socket.on('terminal_error', ({ session_id, message }) => {
      clearPendingConnect(session_id);
      const errorMessage = message || 'Unknown error';
      const t = terminals[session_id];
      if (t) {
        t.term.writeln('\r\n\x1b[31mError: ' + errorMessage + '\x1b[0m\r\n');
      }
      showConnectError(errorMessage);
      if (window.AKDW_Sessions) {
        AKDW_Sessions.onError(session_id, errorMessage);
      }

      const modal = document.getElementById('connectModal');
      if (modal && modal.classList.contains('hidden')) {
        modal.classList.remove('hidden');
      }
    });

    return socket;
  }

  function emitWithSocketReady(eventName, payload) {
    initSocket();
    if (socket.connected) {
      socket.emit(eventName, payload);
      return;
    }

    const onConnect = () => {
      socket.off('connect_error', onConnectError);
      socket.emit(eventName, payload);
    };
    const onConnectError = (err) => {
      socket.off('connect', onConnect);
      showConnectError('Socket.IO connection failed: ' + ((err && err.message) || 'transport error'));
    };

    socket.once('connect', onConnect);
    socket.once('connect_error', onConnectError);
    try {
      socket.connect();
    } catch (_err) {
      // no-op
    }
  }

  function createTerminal(sessionId, options = {}) {
    initSocket();
    if (terminals[sessionId]) return terminals[sessionId];

    const term = new Terminal({
      theme: MOBATERM_THEME,
      fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
      fontSize: currentFontSize,
      lineHeight: 1.2,
      cursorBlink: true,
      cursorStyle: 'block',
      scrollback: 5000,
      bellStyle: 'visual',
      allowTransparency: true
    });

    const FitCtor = (window.FitAddon && window.FitAddon.FitAddon) ? window.FitAddon.FitAddon : null;
    const fitAddon = FitCtor ? new FitCtor() : null;
    if (fitAddon) term.loadAddon(fitAddon);

    const el = document.createElement('div');
    el.id = 'term-' + sessionId;
    el.className = 'xterm-instance hidden';
    el.style.width = '100%';
    el.style.height = '100%';

    const terminalArea = document.getElementById('terminalArea');
    if (terminalArea) terminalArea.appendChild(el);

    term.open(el);
    if (fitAddon) fitAddon.fit();

    term.onData((data) => {
      if (isDrawerInputActive()) return;
      socket.emit('terminal_input', { session_id: sessionId, data });
    });

    term.onResize(({ cols, rows }) => {
      socket.emit('terminal_resize', { session_id: sessionId, cols, rows });
    });

    term.onFocus(() => {
      if (window.AKDW_INPUT_FOCUS === 'drawer') {
        window.AKDW_INPUT_FOCUS = null;
      }
    });

    emitWithSocketReady('terminal_join', { session_id: sessionId });

    terminals[sessionId] = { term, fitAddon, el };

    if (options.reattach) {
      term.writeln('\x1b[36m[Reattached to active session ' + sessionId + ']\x1b[0m');
      term.writeln('Press Enter if prompt is not visible.');
    }

    return terminals[sessionId];
  }

  function connectSession(sessionId, connectionData) {
    createTerminal(sessionId);
    showSession(sessionId);

    clearPendingConnect(sessionId);
    pendingConnectTimers[sessionId] = window.setTimeout(() => {
      const t = terminals[sessionId];
      if (t) {
        t.term.writeln('\r\n\x1b[33mConnection timeout waiting for SSH response.\x1b[0m\r\n');
      }
      showConnectError('Connection timeout. Check credentials, host reachability, and Socket.IO transport.');
      if (window.AKDW_Sessions) AKDW_Sessions.onError(sessionId, 'Connection timeout');
    }, 15000);

    emitWithSocketReady('terminal_connect', {
      session_id: sessionId,
      hostname: connectionData.hostname,
      port: connectionData.port,
      username: connectionData.username,
      password: connectionData.password,
      key_path: connectionData.key_path || null
    });
  }

  function attachSession(sessionId, meta = {}) {
    createTerminal(sessionId, { reattach: true });
    if (meta.hostname) {
      updateStatusBar(sessionId, meta.hostname, meta.username || '');
    }
  }

  function showSession(sessionId) {
    Object.keys(terminals).forEach((sid) => {
      terminals[sid].el.classList.add('hidden');
    });

    const target = terminals[sessionId];
    if (!target) return;

    target.el.classList.remove('hidden');
    if (target.fitAddon) target.fitAddon.fit();
    target.term.focus();
    activeSessionId = sessionId;
    hideWelcomeScreen();
    updateSessionCount();
  }

  function closeSession(sessionId) {
    const t = terminals[sessionId];
    if (t) {
      try {
        t.term.dispose();
      } catch (_err) {
        // no-op
      }
      t.el.remove();
      delete terminals[sessionId];
    }

    clearPendingConnect(sessionId);

    if (socket) {
      socket.emit('terminal_disconnect_session', { session_id: sessionId });
    }

    const remaining = Object.keys(terminals);
    if (remaining.length > 0) {
      showSession(remaining[remaining.length - 1]);
    } else {
      showWelcomeScreen();
      activeSessionId = null;
      const host = document.getElementById('statusHost');
      const path = document.getElementById('statusPath');
      if (host) host.textContent = 'Not connected';
      if (path) path.textContent = '~';
    }
    updateSessionCount();
  }

  function resizeAll() {
    Object.keys(terminals).forEach((sid) => {
      const t = terminals[sid];
      if (!t || !t.fitAddon) return;
      try {
        t.fitAddon.fit();
      } catch (_err) {
        // no-op
      }
    });
  }

  function showWelcomeScreen() {
    const el = document.getElementById('terminalWelcome');
    if (el) el.classList.remove('hidden');
  }

  function hideWelcomeScreen() {
    const el = document.getElementById('terminalWelcome');
    if (el) el.classList.add('hidden');
  }

  function showConnectError(message) {
    const statusEl = document.getElementById('connect-status');
    if (!statusEl) return;
    statusEl.textContent = 'Error: ' + message;
    statusEl.className = 'connect-status error';
    statusEl.classList.remove('hidden');
  }

  function updateStatusBar(sessionId, hostname) {
    const hostEl = document.getElementById('statusHost');
    if (hostEl) {
      hostEl.textContent = 'Connected: ' + (hostname || 'host');
      hostEl.className = 'status-item status-host connected';
    }
    activeSessionId = sessionId;
    updateSessionCount();
  }

  function updateSessionCount() {
    const count = Object.keys(terminals).length;
    const countEl = document.getElementById('statusSessions');
    if (countEl) {
      countEl.textContent = String(count) + ' session' + (count === 1 ? '' : 's');
    }
  }

  function updateStatusFromOutput(sessionId, data) {
    if (sessionId !== activeSessionId || !data) return;

    const pathMatch = data.match(/:[ ]*(~[^\n\r$#>]*|\/[^\n\r$#>]*)[$#>]/);
    if (pathMatch) {
      const path = document.getElementById('statusPath');
      if (path) path.textContent = pathMatch[1].trim();
    }

    const gitMatch = data.match(/\(([^)]+)\)\s*[$#>]/);
    if (gitMatch) {
      const branch = document.getElementById('statusBranch');
      if (branch) branch.textContent = 'branch: ' + gitMatch[1].trim();
    }
  }

  function clearPendingConnect(sessionId) {
    const timer = pendingConnectTimers[sessionId];
    if (timer) {
      window.clearTimeout(timer);
      delete pendingConnectTimers[sessionId];
    }
  }

  function hasSession(sessionId) {
    return Boolean(terminals[sessionId]);
  }

  function generateSessionId() {
    let candidate = '';
    do {
      candidate = 'sess-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6);
    } while (hasSession(candidate));
    return candidate;
  }

  function readStoredFontSize() {
    const raw = safeStorageGet(FONT_STORAGE_KEY);
    const parsed = parseInt(raw || '13', 10);
    if (Number.isNaN(parsed)) return 13;
    return Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, parsed));
  }

  function setFontSize(size) {
    if (!Number.isFinite(size)) size = 13;
    const next = Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, size));
    currentFontSize = next;
    safeStorageSet(FONT_STORAGE_KEY, String(next));

    Object.keys(terminals).forEach((sid) => {
      const t = terminals[sid];
      if (!t) return;
      t.term.options.fontSize = next;
      if (t.fitAddon) {
        try {
          t.fitAddon.fit();
        } catch (_err) {
          // no-op
        }
      }
    });

    const label = document.getElementById('fontSizeValue');
    if (label) label.textContent = String(next);
  }

  function adjustFontSize(delta) {
    setFontSize(currentFontSize + delta);
  }

  function safeStorageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_err) {
      return null;
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_err) {
      // no-op (private mode / policy lock)
    }
  }

  window.addEventListener('resize', resizeAll);

  return {
    connectSession,
    attachSession,
    showSession,
    closeSession,
    resizeAll,
    hasSession,
    generateSessionId,
    setFontSize,
    adjustFontSize,
    getActive: () => activeSessionId,
    getTerminals: () => terminals,
    readStoredFontSize
  };
})();
window.AKDW_Terminal = AKDW_Terminal;

function openConnectModal(prefillHostname) {
  if (prefillHostname) {
    document.getElementById('ssh-hostname').value = prefillHostname;
  }
  document.getElementById('connectModal').classList.remove('hidden');
  setTimeout(() => {
    const p = document.getElementById('ssh-password');
    if (p) p.focus();
  }, 100);
}

function closeConnectModal() {
  const modal = document.getElementById('connectModal');
  const status = document.getElementById('connect-status');
  if (modal) modal.classList.add('hidden');
  if (status) status.classList.add('hidden');
}

function doConnect() {
  const hostname = (document.getElementById('ssh-hostname').value || '').trim();
  const port = parseInt(document.getElementById('ssh-port').value, 10) || 22;
  const username = (document.getElementById('ssh-username').value || '').trim();
  const password = document.getElementById('ssh-password').value || '';
  const keyPath = (document.getElementById('ssh-keypath').value || '').trim();

  if (!hostname || !username) {
    const s = document.getElementById('connect-status');
    s.textContent = 'Hostname and username are required';
    s.className = 'connect-status error';
    s.classList.remove('hidden');
    return;
  }

  const status = document.getElementById('connect-status');
  status.textContent = 'Connecting to ' + username + '@' + hostname + '...';
  status.className = 'connect-status info';
  status.classList.remove('hidden');

  const sessionId = AKDW_Terminal.generateSessionId();
  if (window.AKDW_Sessions) {
    AKDW_Sessions.addTab(sessionId, hostname);
    AKDW_Sessions.saveCurrentHost(hostname, port, username, hostname);
  }

  AKDW_Terminal.connectSession(sessionId, {
    hostname,
    port,
    username,
    password,
    key_path: keyPath || null
  });
}

function quickConnect(hostname) {
  openConnectModal(hostname);
}

function toggleHostsSidebar() {
  const sidebar = document.getElementById('hostsSidebar');
  const expandBtn = document.getElementById('sidebarExpandBtn');
  if (!sidebar || !expandBtn) return;
  sidebar.classList.toggle('collapsed');
  expandBtn.classList.toggle('hidden');
  AKDW_Terminal.resizeAll();
}

function refreshHostsList() {
  if (window.AKDW_Sessions && typeof AKDW_Sessions.loadSavedHosts === 'function') {
    AKDW_Sessions.loadSavedHosts();
  }
}

function increaseTerminalFont() {
  AKDW_Terminal.adjustFontSize(1);
}

function decreaseTerminalFont() {
  AKDW_Terminal.adjustFontSize(-1);
}

document.addEventListener('DOMContentLoaded', () => {
  AKDW_Terminal.setFontSize(AKDW_Terminal.readStoredFontSize());
  const tryInit = () => {
    const mgr = (typeof AKDW_Sessions !== 'undefined' && AKDW_Sessions) || window.AKDW_Sessions;
    if (mgr && typeof mgr.initialize === 'function') {
      mgr.initialize();
      return;
    }
    window.setTimeout(tryInit, 120);
  };
  tryInit();
});
