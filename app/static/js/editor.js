window.AKDWEditor = (function () {
  let socket = null;
  let editor = null;
  let sessionId = null;
  let terminalSessionId = null;
  let activeModel = null;
  let activePath = null;
  let attachedFiles = [];
  let sessionPrimed = false;
  let editorMode = "editor";

  window.monacoReady = false;
  window.monacoEditor = null;

  function languageFromPath(path) {
    const lower = (path || "").toLowerCase();
    const ext = lower.includes(".") ? lower.split(".").pop() : "";
    const langMap = {
      c: "c",
      h: "c",
      patch: "diff",
      diff: "diff",
      dts: "dts",
      dtsi: "dts",
      txt: "plaintext",
      py: "python",
    };
    return langMap[ext] || "plaintext";
  }

  function setStatus(text) {
    const el = document.getElementById("status-last-action");
    if (el) el.textContent = "Last action: " + text;
  }

  function setSessionLabel(value) {
    const editorLabel = document.getElementById("editorSessionLabel");
    if (editorLabel) editorLabel.textContent = value || "unknown";
    const topbarLabel = document.getElementById("active-session");
    if (topbarLabel) topbarLabel.textContent = value || "unknown";
  }

  function setSubtitle(mode) {
    const subtitle = document.getElementById("pageSubtitle");
    if (!subtitle) return;
    subtitle.textContent =
      mode === "agent"
        ? "Code Editor — 🤖 Agent Mode"
        : "Code Editor — ✏️ Editor Mode";
  }

  function applyModeUi(mode) {
    const pill = document.getElementById("modePill");
    const dot = document.getElementById("modeDot");
    const dotPanel = document.getElementById("modeDotPanel");
    const slider = document.getElementById("pillSlider");
    const btnEditor = document.getElementById("btnEditorMode");
    const btnAgent = document.getElementById("btnAgentMode");
    const editorPanel = document.getElementById("editorCenterPanel");
    const agentPanel = document.getElementById("editorRightPanel");
    const rightHandle = document.getElementById("editorHandleRight");

    if (!btnEditor || !btnAgent || !slider) return;

    editorMode = mode === "agent" ? "agent" : "editor";
    localStorage.setItem("akdw_editor_mode", editorMode);

    btnEditor.classList.toggle("active", editorMode === "editor");
    btnAgent.classList.toggle("active", editorMode === "agent");
    if (pill) {
      pill.classList.toggle("agent-active", editorMode === "agent");
    }

    const activeBtn = editorMode === "editor" ? btnEditor : btnAgent;
    slider.style.left = activeBtn.offsetLeft + "px";
    slider.style.width = activeBtn.offsetWidth + "px";

    const dotCls = "mode-dot " + editorMode;
    if (dot) dot.className = dotCls;
    if (dotPanel) dotPanel.className = dotCls + " editor-panel-dot";

    setSubtitle(editorMode);

    if (editorPanel && agentPanel) {
      editorPanel.style.display = editorMode === "editor" ? "block" : "none";
      agentPanel.style.display = editorMode === "agent" ? "grid" : "none";
      if (rightHandle) {
        rightHandle.style.display = editorMode === "agent" ? "block" : "none";
      }
    }
  }

  async function ensureTerminalSession() {
    if (terminalSessionId) return terminalSessionId;
    const res = await fetch("/api/terminal/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cwd: activePath || "/app/kernel" }),
    });
    const data = await res.json();
    terminalSessionId = data.session_id || ("term-" + Date.now());
    return terminalSessionId;
  }

  function linkifyText(text) {
    return String(text || "").replace(
      /(https?:\/\/[^\s<>"']+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer" class="chat-link">$1 ↗</a>'
    );
  }

  function renderMarkdown(text) {
    const linked = linkifyText(text || "");
    if (!window.marked) return linked;
    const renderer = new window.marked.Renderer();
    renderer.link = function (href, title, txt) {
      const tip = title || href;
      return (
        '<a href="' +
        href +
        '" target="_blank" rel="noopener noreferrer" class="chat-link" title="' +
        tip +
        '">' +
        (txt || href) +
        " ↗</a>"
      );
    };
    window.marked.setOptions({ renderer: renderer, breaks: true });
    return window.marked.parse(linked);
  }

  function copyResponse(btn) {
    const root = btn.closest(".chat-row.assistant");
    const content = root ? root.querySelector(".msg-content") : null;
    const text = content ? content.innerText : "";
    navigator.clipboard.writeText(text).then(function () {
      btn.textContent = "✅";
      setTimeout(function () {
        btn.textContent = "📋";
      }, 1500);
    });
  }

  function prefillShellCommand(cmd) {
    const input = document.getElementById("shellCmdInput");
    if (!input) return;
    input.value = cmd;
    const status = document.getElementById("shellStatus");
    if (status) status.textContent = "Command prefilled from assistant response.";
  }

  function attachRunButtons(container) {
    const allowed = /^(git|ls|cat|grep|find|checkpatch\.pl|make|diff|patch)\b/m;
    container.querySelectorAll("pre > code").forEach(function (code) {
      const pre = code.parentElement;
      if (!pre || pre.previousElementSibling && pre.previousElementSibling.classList.contains("run-this-btn")) {
        return;
      }
      const raw = (code.textContent || "").trim();
      const firstLine = raw.split("\n")[0].trim();
      if (!allowed.test(firstLine)) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-secondary run-this-btn";
      btn.textContent = "▶ Run this";
      btn.style.marginBottom = "6px";
      btn.addEventListener("click", function () {
        prefillShellCommand(firstLine);
      });
      pre.parentNode.insertBefore(btn, pre);
    });
  }

  function addChatBubble(role, content) {
    const list = document.getElementById("chatMessages");
    const row = document.createElement("div");
    row.className = role === "user" ? "chat-row user" : "chat-row assistant";
    if (role === "assistant") {
      row.innerHTML =
        '<button class="copy-btn" type="button" title="Copy response">📋</button>' +
        '<div class="msg-content">' +
        renderMarkdown(content || "") +
        "</div>";
      const copyBtn = row.querySelector(".copy-btn");
      if (copyBtn) {
        copyBtn.addEventListener("click", function () {
          copyResponse(copyBtn);
        });
      }
      attachRunButtons(row);
    } else {
      row.textContent = content || "";
    }
    list.appendChild(row);
    list.scrollTop = list.scrollHeight;
  }

  function stepTitle(stepType) {
    const labels = {
      thinking: "🟣 THINKING",
      tool_call: "🔵 TOOL CALL",
      tool_result: "🟢 TOOL RESULT",
      response: "⬜ RESPONSE",
    };
    return labels[stepType] || "⬜ RESPONSE";
  }

  function addStepCard(step) {
    const list = document.getElementById("stepCards");
    const card = document.createElement("details");
    card.className = "step-card " + (step.type || "response");
    card.open = step.type !== "thinking";

    const summary = document.createElement("summary");
    summary.textContent = stepTitle(step.type || "response") + " [" + (step.timestamp || "") + "]";

    const body = document.createElement("div");
    body.className = "step-body";
    if (step.type === "response") {
      body.innerHTML = renderMarkdown(step.content || "");
    } else {
      const pre = document.createElement("pre");
      pre.textContent = step.content || "";
      body.appendChild(pre);
    }

    card.appendChild(summary);
    card.appendChild(body);
    list.appendChild(card);
    list.scrollTop = list.scrollHeight;
  }

  async function listPath(path) {
    const res = await fetch("/api/fs/browse?path=" + encodeURIComponent(path));
    return res.json();
  }

  async function loadTree(path) {
    const data = await listPath(path);
    const list = document.getElementById("fileTree");
    list.innerHTML = "";

    if (!data.ok) {
      list.innerHTML = '<div class="small-muted">' + (data.error || "Unable to load tree") + "</div>";
      return;
    }

    activePath = data.path;
    localStorage.setItem("akdw_editor_path", activePath);
    document.getElementById("pathInput").value = activePath;

    data.entries.forEach(function (item) {
      const row = document.createElement("button");
      row.className = "tree-row";
      row.textContent = (item.type === "dir" ? "📁 " : "📄 ") + item.name;
      row.onclick = function () {
        if (item.type === "dir") {
          loadTree(item.path);
          return;
        }
        openFile(item.path);
      };
      list.appendChild(row);
    });
  }

  function openFileWithContent(filename, content, retryCount) {
    const retries = retryCount || 0;
    if (!window.monacoReady || !window.monacoEditor || !editor) {
      if (retries < 20) {
        setTimeout(function () {
          openFileWithContent(filename, content, retries + 1);
        }, 300);
      }
      return;
    }
    const lang = languageFromPath(filename);
    const model = monaco.editor.createModel(content || "", lang);
    window.monacoEditor.setModel(model);
    window.monacoEditor.layout();
    window.monacoEditor.revealLine(1);
    document.getElementById("currentFile").textContent = filename;
    setStatus("Opened " + filename);
  }

  async function openFile(path) {
    const res = await fetch("/api/fs/read?path=" + encodeURIComponent(path));
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Open failed");
      return;
    }
    openFileWithContent(path, data.content || "", 0);
  }

  async function saveCurrentFile() {
    const path = document.getElementById("currentFile").textContent;
    if (!path || path === "(none)") {
      setStatus("No file selected");
      return;
    }
    const res = await fetch("/api/fs/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path, content: editor.getValue() }),
    });
    const data = await res.json();
    setStatus(data.ok ? "Saved " + path : data.error || "Save failed");
  }

  function renderAttachments() {
    const box = document.getElementById("attachPills");
    box.innerHTML = "";
    attachedFiles.forEach(function (file, idx) {
      const pill = document.createElement("span");
      pill.className = "file-pill";
      pill.textContent = "📎 " + file.filename;
      const close = document.createElement("button");
      close.textContent = "×";
      close.onclick = function () {
        attachedFiles.splice(idx, 1);
        renderAttachments();
      };
      pill.appendChild(close);
      box.appendChild(pill);
    });
  }

  async function ensureEditorSession() {
    if (sessionId) {
      setSessionLabel(sessionId);
      if (window.AKDWSession && typeof window.AKDWSession.setSession === "function") {
        window.AKDWSession.setSession(sessionId);
      }
      if (socket) {
        socket.emit("join_agent_session", { session_id: sessionId });
      }
      return sessionId;
    }
    const persisted = localStorage.getItem("akdw_editor_session");
    if (persisted) {
      sessionId = persisted;
      setSessionLabel(sessionId);
      if (window.AKDWSession && typeof window.AKDWSession.setSession === "function") {
        window.AKDWSession.setSession(sessionId);
      }
      return sessionId;
    }
    const res = await fetch("/api/agent/new_session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: "editor" }),
    });
    const data = await res.json();
    sessionId = data.session_id;
    localStorage.setItem("akdw_editor_session", sessionId);
    setSessionLabel(sessionId);
    if (window.AKDWSession && typeof window.AKDWSession.setSession === "function") {
      window.AKDWSession.setSession(sessionId);
    }
    if (socket) {
      socket.emit("join_agent_session", { session_id: sessionId });
    }
    return sessionId;
  }

  async function primeEditorContext() {
    if (sessionPrimed) return;
    await ensureEditorSession();
    const currentFile = document.getElementById("currentFile").textContent || "(none)";
    await fetch("/api/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        page: "editor",
        model: activeModel,
        message: "System: The user is editing " + currentFile + ". Assist with kernel driver code questions.",
      }),
    });
    sessionPrimed = true;
  }

  async function sendQuery(messageOverride, selectedCode) {
    const input = document.getElementById("chatInput");
    const message = (messageOverride || input.value || "").trim();
    if (!message && attachedFiles.length === 0) return;

    await ensureEditorSession();
    await primeEditorContext();

    const filename = document.getElementById("currentFile").textContent || "(none)";
    let outbound = message;
    if (selectedCode && selectedCode.trim()) {
      outbound += "\n\nSelected code from " + filename + ":\n" + selectedCode;
    }

    addChatBubble("user", message || "(attachments)");
    input.value = "";
    addStepCard({ type: "thinking", content: "Analysing request...", timestamp: new Date().toLocaleTimeString() });

    let data = {};
    if (editorMode === "agent") {
      await ensureTerminalSession();
      const res = await fetch("/api/terminal/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: terminalSessionId,
          prompt: outbound,
          cwd: activePath || "/app/kernel",
          file_context: selectedCode || editor.getValue().slice(0, 6000),
          filename: filename,
          model: activeModel,
        }),
      });
      data = await res.json();
    } else {
      const res = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          page: "editor",
          model: activeModel,
          message: outbound,
          attachments: attachedFiles,
          selected_code: selectedCode || "",
          filename: filename,
        }),
      });
      data = await res.json();
    }
    const answer = (data.response || data.content || data.message || "").trim() || "⚠️ No response received. Please retry.";
    addChatBubble("assistant", answer);
    addStepCard({ type: "response", content: answer, timestamp: new Date().toLocaleTimeString() });
    attachedFiles = [];
    renderAttachments();
  }

  function initMonaco() {
    window.monacoReady = false;
    require.config({ paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs" } });
    require(["vs/editor/editor.main"], function () {
      editor = monaco.editor.create(document.getElementById("editorPane"), {
        value: "",
        language: "c",
        theme: "vs-dark",
        automaticLayout: true,
        minimap: { enabled: true },
      });
      window.monacoEditor = editor;
      window.monacoReady = true;

      editor.addAction({
        id: "ask-qgenie",
        label: "Ask QGenie",
        contextMenuGroupId: "navigation",
        contextMenuOrder: 1.1,
        run: function () {
          const selection = editor.getSelection();
          const selectedCode = editor.getModel().getValueInRange(selection).trim();
          if (!selectedCode) return;
          sendQuery("Please review this selected code.", selectedCode);
        },
      });
    });
  }

  async function uploadFile(file) {
    const form = new FormData();
    form.append("file", file);
    form.append("target_dir", activePath || "/app/workspace");
    const res = await fetch("/editor/api/fs/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || "Upload failed");
      return;
    }

    const lower = (data.filename || "").toLowerCase();
    if ([".c", ".h", ".dts", ".dtsi", ".patch", ".diff", ".txt", ".py"].some(function (ext) { return lower.endsWith(ext); })) {
      openFileWithContent(data.path || data.filename, data.content || "", 0);
    } else {
      attachedFiles.push({ filename: data.filename, content: data.content });
      renderAttachments();
    }
    setStatus("Uploaded " + data.filename);
  }

  function wireDnD() {
    const zone = document.getElementById("dropZone");
    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        zone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        zone.classList.remove("dragging");
      });
    });
    zone.addEventListener("drop", function (e) {
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      files.forEach(uploadFile);
    });

    const picker = document.getElementById("fileInput");
    picker.addEventListener("change", function () {
      Array.from(picker.files || []).forEach(uploadFile);
      picker.value = "";
    });
  }

  async function runShellCommand() {
    const input = document.getElementById("shellCmdInput");
    const output = document.getElementById("shellOutput");
    const runBtn = document.getElementById("shellRunBtn");
    const status = document.getElementById("shellStatus");
    const cmd = (input.value || "").trim();
    if (!cmd) return;

    runBtn.disabled = true;
    runBtn.innerHTML = '<span class="send-spinner"></span>';
    status.textContent = "Running...";
    const cwd = activePath || document.getElementById("pathInput").value || "/app/kernel";
    const res = await fetch("/api/editor/shell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cmd: cmd, cwd: cwd }),
    });
    const data = await res.json();
    const header = "$ " + cmd + " (cwd: " + cwd + ")\n";
    const body = (data.output || data.error || "").trim();
    output.textContent = header + body + "\n";
    output.scrollTop = output.scrollHeight;
    status.textContent = data.ok ? "✅ Completed (" + data.returncode + ")" : "❌ " + (data.error || "Failed");
    runBtn.disabled = false;
    runBtn.textContent = "Run";
  }

  function initShellPanel() {
    const toggle = document.getElementById("shellPanelToggle");
    const body = document.getElementById("shellPanelBody");
    const runBtn = document.getElementById("shellRunBtn");
    const cmdInput = document.getElementById("shellCmdInput");
    if (!toggle || !body || !runBtn || !cmdInput) return;

    toggle.addEventListener("click", function () {
      const hidden = body.style.display === "none";
      body.style.display = hidden ? "grid" : "none";
      toggle.textContent = hidden ? "▼" : "▶";
    });
    runBtn.addEventListener("click", runShellCommand);
    cmdInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        runShellCommand();
      }
    });
  }

  function initSocketHandlers() {
    socket = io();
    const terminalSocket = io("/terminal");
    window.AKDWTerminal.init({
      containerId: "terminalPane",
      socket: terminalSocket,
      sessionId: terminalSessionId || sessionId || "editor-live",
      getSessionId: function () {
        return terminalSessionId || sessionId || "editor-live";
      },
    });

    socket.on("agent_step", function (msg) {
      if (!msg || !sessionId || msg.session_id !== sessionId) return;
      addStepCard(msg);
    });

    socket.on("file_diff", function (msg) {
      if (!msg || !sessionId || msg.session_id !== sessionId) return;
      document.getElementById("diffPanel").style.display = "block";
      window.AKDWDiff.showDiff(msg);
      document.getElementById("diffFilename").textContent = msg.filename || "";
    });
  }

  async function init(opts) {
    sessionId = localStorage.getItem("akdw_editor_session") || null;
    activeModel = localStorage.getItem("akdw_editor_model") || opts.defaultModel;
    document.getElementById("modelSelect").value = activeModel;
    setSessionLabel("initializing...");

    initSocketHandlers();
    initMonaco();
    window.AKDWDiff.init("diffEditor");
    wireDnD();
    initShellPanel();

    document.getElementById("sendBtn").addEventListener("click", function () { sendQuery(); });
    document.getElementById("chatInput").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendQuery();
      }
    });

    document.getElementById("saveFileBtn").addEventListener("click", saveCurrentFile);
    document.getElementById("applyPathBtn").addEventListener("click", function () {
      loadTree(document.getElementById("pathInput").value);
    });
    document.getElementById("browsePathBtn").addEventListener("click", function () {
      if (!window.AKDWFolderBrowser) {
        loadTree(document.getElementById("pathInput").value);
        return;
      }
      window.AKDWFolderBrowser.open({
        startPath: document.getElementById("pathInput").value || "/app/kernel",
        onSelect: function (selectedPath) {
          document.getElementById("pathInput").value = selectedPath;
          loadTree(selectedPath);
        },
      });
    });

    document.getElementById("modelSelect").addEventListener("change", function (e) {
      activeModel = e.target.value;
      localStorage.setItem("akdw_editor_model", activeModel);
    });

    document.getElementById("acceptDiffBtn").addEventListener("click", async function () {
      const path = window.AKDWDiff.getActiveFile() || document.getElementById("currentFile").textContent;
      const content = window.AKDWDiff.getModified();
      if (!path || path === "(none)") return;
      await fetch("/api/fs/write", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path, content: content }),
      });
      setStatus("Accepted diff into " + path);
      document.getElementById("diffPanel").style.display = "none";
      openFile(path);
    });

    document.getElementById("rejectDiffBtn").addEventListener("click", function () {
      document.getElementById("diffPanel").style.display = "none";
    });

    document.getElementById("saveDiffBtn").addEventListener("click", async function () {
      const path = window.AKDWDiff.getActiveFile() || document.getElementById("currentFile").textContent;
      if (!path || path === "(none)") return;
      const content = window.AKDWDiff.getModified();
      await fetch("/api/fs/write", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path, content: content }),
      });
      setStatus("Saved proposed diff to file");
    });

    const savedPath = localStorage.getItem("akdw_editor_path") || opts.defaultPath;
    loadTree(savedPath);
    await ensureEditorSession();
    await ensureTerminalSession();
    sessionPrimed = false;
    await primeEditorContext();

    const btnEditor = document.getElementById("btnEditorMode");
    const btnAgent = document.getElementById("btnAgentMode");
    if (btnEditor && btnAgent) {
      btnEditor.addEventListener("click", function () {
        applyModeUi("editor");
        setStatus("Switched to EDITOR mode");
      });
      btnAgent.addEventListener("click", function () {
        applyModeUi("agent");
        setStatus("Switched to AGENT mode");
      });
      const savedMode = localStorage.getItem("akdw_editor_mode") || "editor";
      applyModeUi(savedMode);
      window.addEventListener("resize", function () {
        applyModeUi(editorMode);
      });
    }
  }

  return { init: init };
})();
