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

    const socket = opts.socket || io("/terminal");
    const sessionId = opts.sessionId;
    const getSessionId = typeof opts.getSessionId === 'function'
      ? opts.getSessionId
      : function () { return sessionId; };

    window.addEventListener('resize', function () {
      try { fitAddon.fit(); } catch (_) {}
      if (socket) {
        socket.emit("terminal:resize", {
          session_id: getSessionId(),
          cols: term.cols || 80,
          rows: term.rows || 24,
        });
      }
    });

    if (socket) {
      socket.emit("terminal:join", { session_id: getSessionId() });
      socket.on("terminal:output", function (msg) {
        if (msg && msg.session_id && msg.session_id !== getSessionId()) return;
        term.write((msg && msg.data) || "");
      });
      socket.on("agent:tool_call", function (msg) {
        if (msg && msg.session_id && msg.session_id !== getSessionId()) return;
        term.write("\r\n" + (msg.message || "") + "\r\n");
      });
      socket.on("agent:output", function (msg) {
        if (msg && msg.session_id && msg.session_id !== getSessionId()) return;
        term.write((msg.output || "") + "\r\n");
      });
      socket.on("agent:complete", function (msg) {
        if (msg && msg.session_id && msg.session_id !== getSessionId()) return;
        term.write("\r\n" + (msg.message || "") + "\r\n");
      });
    }

    term.onData(function (data) {
      if (!socket) return;
      socket.emit("terminal:input", { session_id: getSessionId(), data: data });
    });

    setTimeout(function () {
      try { fitAddon.fit(); } catch (_) {}
      if (socket) {
        socket.emit("terminal:resize", {
          session_id: getSessionId(),
          cols: term.cols || 80,
          rows: term.rows || 24,
        });
      }
    }, 100);

    return {
      term: term,
      write: function (text) { term.write(text || ''); },
      clear: function () { term.clear(); },
      socket: socket,
    };
  }

  return { init: init };
})();
