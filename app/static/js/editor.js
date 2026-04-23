window.AKDWEditor = (function () {
  let socket = null;
  let editor = null;
  let sessionId = null;
  let activeModel = null;
  let activePath = null;
  let attachedFiles = [];

  function languageFromPath(path) {
    const name = (path || '').toLowerCase();
    if (name.endsWith('.c') || name.endsWith('.h')) return 'c';
    if (name.endsWith('.dts') || name.endsWith('.dtsi')) return 'plaintext';
    if (name.endsWith('/kconfig') || name.endsWith('kconfig')) return 'plaintext';
    if (name.endsWith('/makefile') || name.endsWith('makefile')) return 'makefile';
    return 'plaintext';
  }

  function setStatus(text) {
    const el = document.getElementById('status-last-action');
    if (el) el.textContent = 'Last action: ' + text;
  }

  async function listPath(path) {
    const res = await fetch('/api/fs/browse?path=' + encodeURIComponent(path));
    return res.json();
  }

  async function loadTree(path) {
    const data = await listPath(path);
    const list = document.getElementById('fileTree');
    list.innerHTML = '';

    if (!data.ok) {
      list.innerHTML = '<div class="small-muted">' + (data.error || 'Unable to load tree') + '</div>';
      return;
    }

    activePath = data.path;
    localStorage.setItem('akdw_editor_path', activePath);
    document.getElementById('pathInput').value = activePath;

    data.entries.forEach(function (item) {
      const row = document.createElement('button');
      row.className = 'tree-row';
      row.textContent = (item.type === 'dir' ? '📁 ' : '📄 ') + item.name;
      row.onclick = function () {
        if (item.type === 'dir') {
          loadTree(item.path);
          return;
        }
        openFile(item.path);
      };
      list.appendChild(row);
    });
  }

  async function openFile(path) {
    const res = await fetch('/api/fs/read?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || 'Open failed');
      return;
    }
    const model = monaco.editor.createModel(data.content || '', languageFromPath(path));
    editor.setModel(model);
    document.getElementById('currentFile').textContent = path;
    setStatus('Opened ' + path);
  }

  async function saveCurrentFile() {
    const path = document.getElementById('currentFile').textContent;
    if (!path || path === '(none)') {
      setStatus('No file selected');
      return;
    }
    const res = await fetch('/api/fs/write', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ path: path, content: editor.getValue() }),
    });
    const data = await res.json();
    setStatus(data.ok ? 'Saved ' + path : (data.error || 'Save failed'));
  }

  function renderAttachments() {
    const box = document.getElementById('attachPills');
    box.innerHTML = '';
    attachedFiles.forEach(function (file, idx) {
      const pill = document.createElement('span');
      pill.className = 'file-pill';
      pill.textContent = '📎 ' + file.filename;
      const close = document.createElement('button');
      close.textContent = '×';
      close.onclick = function () {
        attachedFiles.splice(idx, 1);
        renderAttachments();
      };
      pill.appendChild(close);
      box.appendChild(pill);
    });
  }

  function addChatBubble(role, content) {
    const list = document.getElementById('chatMessages');
    const row = document.createElement('div');
    row.className = role === 'user' ? 'chat-row user' : 'chat-row assistant';
    row.textContent = content;
    list.appendChild(row);
    list.scrollTop = list.scrollHeight;
  }

  function stepTitle(stepType) {
    const labels = {
      thinking: '🟣 THINKING',
      tool_call: '🔵 TOOL CALL',
      tool_result: '🟢 TOOL RESULT',
      response: '⬜ RESPONSE',
    };
    return labels[stepType] || '⬜ RESPONSE';
  }

  function addStepCard(step) {
    const list = document.getElementById('stepCards');
    const card = document.createElement('details');
    card.className = 'step-card ' + (step.type || 'response');
    card.open = step.type !== 'thinking';

    const summary = document.createElement('summary');
    summary.textContent = stepTitle(step.type || 'response') + '  [' + (step.timestamp || '') + ']';

    const body = document.createElement('div');
    body.className = 'step-body';

    if (step.type === 'tool_call') {
      const pre = document.createElement('pre');
      const args = step.tool_args && Object.keys(step.tool_args).length ? JSON.stringify(step.tool_args, null, 2) : '{}';
      pre.textContent = 'Tool: ' + (step.tool_name || 'unknown') + '\nArgs: ' + args;
      body.appendChild(pre);
    } else if (step.type === 'tool_result') {
      const text = step.content || '';
      const lines = text.split('\n');
      const pre = document.createElement('pre');
      pre.textContent = lines.slice(0, 3).join('\n');
      body.appendChild(pre);
      if (lines.length > 3) {
        const btn = document.createElement('button');
        btn.className = 'btn-secondary';
        btn.textContent = 'Show more';
        btn.onclick = function () {
          const expanded = btn.dataset.expanded === '1';
          pre.textContent = expanded ? lines.slice(0, 3).join('\n') : text;
          btn.textContent = expanded ? 'Show more' : 'Show less';
          btn.dataset.expanded = expanded ? '0' : '1';
        };
        body.appendChild(btn);
      }
    } else if (step.type === 'response' && window.marked) {
      body.innerHTML = window.marked.parse(step.content || '');
    } else {
      const pre = document.createElement('pre');
      pre.style.fontStyle = step.type === 'thinking' ? 'italic' : 'normal';
      pre.textContent = step.content || '';
      body.appendChild(pre);
    }

    card.appendChild(summary);
    card.appendChild(body);
    list.appendChild(card);
    list.scrollTop = list.scrollHeight;
  }

  function sendQuery(messageOverride, selectedCode) {
    const input = document.getElementById('chatInput');
    const message = (messageOverride || input.value || '').trim();
    if (!message && attachedFiles.length === 0) return;

    addChatBubble('user', message || '(attachments)');
    input.value = '';

    socket.emit('editor_query', {
      session_id: sessionId,
      model: activeModel,
      message: message,
      attachments: attachedFiles,
      selected_code: selectedCode || '',
      filename: document.getElementById('currentFile').textContent,
    });
    attachedFiles = [];
    renderAttachments();
  }

  function initMonaco() {
    require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs' } });
    require(['vs/editor/editor.main'], function () {
      editor = monaco.editor.create(document.getElementById('editorPane'), {
        value: '',
        language: 'c',
        theme: 'vs-dark',
        automaticLayout: true,
        minimap: { enabled: true },
      });

      editor.addAction({
        id: 'ask-qgenie',
        label: 'Ask QGenie',
        contextMenuGroupId: 'navigation',
        contextMenuOrder: 1.1,
        run: function () {
          const selection = editor.getSelection();
          const selectedCode = editor.getModel().getValueInRange(selection).trim();
          if (!selectedCode) return;
          sendQuery('Please review this selected code.', selectedCode);
        },
      });
    });
  }

  async function uploadFile(file) {
    const form = new FormData();
    form.append('file', file);
    form.append('target_dir', activePath || '/app/workspace');
    const res = await fetch('/editor/api/fs/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.ok) {
      setStatus(data.error || 'Upload failed');
      return;
    }

    const lower = (data.filename || '').toLowerCase();
    if (['.c', '.h', '.dts', '.dtsi'].some(function (ext) { return lower.endsWith(ext); })) {
      const model = monaco.editor.createModel(data.content || '', languageFromPath(data.filename));
      editor.setModel(model);
      document.getElementById('currentFile').textContent = data.path;
    } else {
      attachedFiles.push({ filename: data.filename, content: data.content });
      renderAttachments();
    }
    setStatus('Uploaded ' + data.filename);
  }

  function wireDnD() {
    const zone = document.getElementById('dropZone');
    ['dragenter', 'dragover'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) { e.preventDefault(); zone.classList.add('dragging'); });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) { e.preventDefault(); zone.classList.remove('dragging'); });
    });
    zone.addEventListener('drop', function (e) {
      const files = Array.from(e.dataTransfer.files || []);
      files.forEach(uploadFile);
    });

    const picker = document.getElementById('fileInput');
    picker.addEventListener('change', function () {
      Array.from(picker.files || []).forEach(uploadFile);
      picker.value = '';
    });
  }

  function init(opts) {
    sessionId = localStorage.getItem('akdw_editor_session') || opts.sessionId;
    localStorage.setItem('akdw_editor_session', sessionId);

    activeModel = localStorage.getItem('akdw_editor_model') || opts.defaultModel;
    document.getElementById('modelSelect').value = activeModel;

    socket = io();
    socket.emit('join_agent_session', { session_id: sessionId });

    window.AKDWTerminal.init({
      containerId: 'terminalPane',
      socket: socket,
      sessionId: sessionId,
    });

    socket.on('agent_step', function (msg) {
      if (!msg || msg.session_id !== sessionId) return;
      addStepCard(msg);
      if (msg.type === 'response') addChatBubble('assistant', msg.content || '');
    });

    socket.on('file_diff', function (msg) {
      if (!msg || msg.session_id !== sessionId) return;
      document.getElementById('diffPanel').style.display = 'block';
      window.AKDWDiff.showDiff(msg);
      document.getElementById('diffFilename').textContent = msg.filename || '';
    });

    document.getElementById('sendBtn').addEventListener('click', function () { sendQuery(); });
    document.getElementById('chatInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendQuery();
      }
    });

    document.getElementById('saveFileBtn').addEventListener('click', saveCurrentFile);
    document.getElementById('applyPathBtn').addEventListener('click', function () {
      loadTree(document.getElementById('pathInput').value);
    });
    document.getElementById('browsePathBtn').addEventListener('click', function () {
      if (!window.AKDWFolderBrowser) {
        loadTree(document.getElementById('pathInput').value);
        return;
      }
      window.AKDWFolderBrowser.open({
        startPath: document.getElementById('pathInput').value || '/app/kernel',
        onSelect: function (selectedPath) {
          document.getElementById('pathInput').value = selectedPath;
          loadTree(selectedPath);
        },
      });
    });

    document.getElementById('modelSelect').addEventListener('change', function (e) {
      activeModel = e.target.value;
      localStorage.setItem('akdw_editor_model', activeModel);
    });

    document.getElementById('acceptDiffBtn').addEventListener('click', async function () {
      const path = window.AKDWDiff.getActiveFile() || document.getElementById('currentFile').textContent;
      const content = window.AKDWDiff.getModified();
      if (!path || path === '(none)') return;
      await fetch('/api/fs/write', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ path: path, content: content }),
      });
      setStatus('Accepted diff into ' + path);
      document.getElementById('diffPanel').style.display = 'none';
      openFile(path);
    });

    document.getElementById('rejectDiffBtn').addEventListener('click', function () {
      document.getElementById('diffPanel').style.display = 'none';
    });

    document.getElementById('saveDiffBtn').addEventListener('click', async function () {
      const path = window.AKDWDiff.getActiveFile() || document.getElementById('currentFile').textContent;
      if (!path || path === '(none)') return;
      const content = window.AKDWDiff.getModified();
      await fetch('/api/fs/write', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ path: path, content: content }),
      });
      setStatus('Saved proposed diff to file');
    });

    initMonaco();
    window.AKDWDiff.init('diffEditor');
    wireDnD();

    const savedPath = localStorage.getItem('akdw_editor_path') || opts.defaultPath;
    loadTree(savedPath);

    document.getElementById('active-session').textContent = sessionId;
  }

  return { init: init };
})();
