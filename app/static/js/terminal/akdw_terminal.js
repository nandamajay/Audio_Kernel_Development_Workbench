/**
 * AKDW Terminal Core
 * Manages xterm.js Terminal instances mapped to SSH sessions via SocketIO.
 */

const AKDW_Terminal = (() => {
  const terminals = {};
  let socket = null;
  let activeSessionId = null;

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

  function initSocket() {
    if (socket) return socket;
    socket = io({ transports: ['websocket'], reconnection: true });

    socket.on('terminal_output', ({ session_id, data }) => {
      const t = terminals[session_id];
      if (!t) return;
      t.term.write(data || '');
      updateStatusFromOutput(session_id, data || '');
    });

    socket.on('terminal_connected', ({ session_id, hostname, username, message }) => {
      hideWelcomeScreen();
      updateStatusBar(session_id, hostname, username);
      if (window.AKDW_Sessions) {
        AKDW_Sessions.onConnected(session_id, hostname);
      }
      const status = document.getElementById('connect-status');
      if (status) {
        status.textContent = message || `Connected to ${username}@${hostname}`;
        status.className = 'connect-status info';
        status.classList.remove('hidden');
      }
    });

    socket.on('terminal_closed', ({ session_id, message }) => {
      const t = terminals[session_id];
      if (t) {
        t.term.writeln('\r\n\x1b[33m[Session closed: ' + (message || 'Disconnected') + ']\x1b[0m\r\n');
      }
      if (window.AKDW_Sessions) {
        AKDW_Sessions.onClosed(session_id);
      }
    });

    socket.on('terminal_error', ({ session_id, message }) => {
      const t = terminals[session_id];
      if (t) {
        t.term.writeln('\r\n\x1b[31mError: ' + (message || 'Unknown error') + '\x1b[0m\r\n');
      } else {
        showConnectError(message || 'Unknown error');
      }
      if (window.AKDW_Sessions) {
        AKDW_Sessions.onError(session_id, message || 'Unknown error');
      }
    });

    return socket;
  }

  function createTerminal(sessionId) {
    initSocket();

    const term = new Terminal({
      theme: MOBATERM_THEME,
      fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", monospace',
      fontSize: 13,
      lineHeight: 1.2,
      cursorBlink: true,
      cursorStyle: 'block',
      scrollback: 5000,
      bellStyle: 'visual',
      allowTransparency: true
    });

    const FitCtor = (window.FitAddon && window.FitAddon.FitAddon) ? window.FitAddon.FitAddon : null;
    const fitAddon = FitCtor ? new FitCtor() : null;
    if (fitAddon) {
      term.loadAddon(fitAddon);
    }

    const el = document.createElement('div');
    el.id = 'term-' + sessionId;
    el.className = 'xterm-instance hidden';
    el.style.width = '100%';
    el.style.height = '100%';
    document.getElementById('terminalArea').appendChild(el);

    term.open(el);
    if (fitAddon) fitAddon.fit();

    term.onData((data) => {
      socket.emit('terminal_input', { session_id: sessionId, data });
    });

    term.onResize(({ cols, rows }) => {
      socket.emit('terminal_resize', { session_id: sessionId, cols, rows });
    });

    socket.emit('terminal_join', { session_id: sessionId });

    terminals[sessionId] = { term, fitAddon, el };
    return terminals[sessionId];
  }

  function connectSession(sessionId, connectionData) {
    createTerminal(sessionId);
    showSession(sessionId);
    socket.emit('terminal_connect', {
      session_id: sessionId,
      hostname: connectionData.hostname,
      port: connectionData.port,
      username: connectionData.username,
      password: connectionData.password,
      key_path: connectionData.key_path || null
    });
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
      } catch (err) {
        // no-op
      }
      t.el.remove();
      delete terminals[sessionId];
    }

    if (socket) {
      socket.emit('terminal_disconnect_session', { session_id: sessionId });
    }

    const remaining = Object.keys(terminals);
    if (remaining.length > 0) {
      showSession(remaining[remaining.length - 1]);
    } else {
      showWelcomeScreen();
      activeSessionId = null;
      document.getElementById('statusHost').textContent = 'Not connected';
      document.getElementById('statusPath').textContent = '~';
    }
    updateSessionCount();
  }

  function resizeAll() {
    Object.keys(terminals).forEach((sid) => {
      const t = terminals[sid];
      if (!t || !t.fitAddon) return;
      try {
        t.fitAddon.fit();
      } catch (err) {
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
      hostEl.textContent = 'Connected: ' + hostname;
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
      document.getElementById('statusPath').textContent = pathMatch[1].trim();
    }

    const gitMatch = data.match(/\(([^)]+)\)\s*[$#>]/);
    if (gitMatch) {
      document.getElementById('statusBranch').textContent = 'branch: ' + gitMatch[1].trim();
    }
  }

  window.addEventListener('resize', resizeAll);

  return {
    connectSession,
    showSession,
    closeSession,
    resizeAll,
    getActive: () => activeSessionId,
    getTerminals: () => terminals
  };
})();

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

  const sessionId = 'sess-' + Math.random().toString(36).slice(2, 10);
  if (window.AKDW_Sessions) {
    AKDW_Sessions.addTab(sessionId, hostname);
    AKDW_Sessions.saveCurrentHost(hostname, port, username, hostname);
  }

  closeConnectModal();
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

document.addEventListener('DOMContentLoaded', () => {
  if (window.AKDW_Sessions) {
    AKDW_Sessions.loadSavedHosts();
  }
});
