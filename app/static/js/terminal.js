// REUSED FROM (PATTERN): Q-Build-Manager terminal + socket streaming style.
window.AKDWTerminal = (function () {
  function init(opts) {
    const container = document.getElementById(opts.containerId);
    if (!container || !window.Terminal) return null;

    const term = new Terminal({
      convertEol: true,
      fontFamily: 'JetBrains Mono, monospace',
      theme: { background: '#0d1117', foreground: '#e6edf3' },
      fontSize: 12,
    });

    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();

    const socket = opts.socket;
    const sessionId = opts.sessionId;
    const getSessionId = typeof opts.getSessionId === 'function'
      ? opts.getSessionId
      : function () { return sessionId; };

    window.addEventListener('resize', function () {
      try { fitAddon.fit(); } catch (_) {}
    });

    if (socket) {
      socket.on('terminal_output', function (msg) {
        if (msg && msg.session_id && msg.session_id !== getSessionId()) return;
        term.write((msg && msg.data) || '');
      });
    }

    return {
      term: term,
      write: function (text) { term.write(text || ''); },
      clear: function () { term.clear(); },
    };
  }

  return { init: init };
})();
