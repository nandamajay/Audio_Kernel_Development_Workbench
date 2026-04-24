window.AKDWFolderBrowser = (function () {
  let modal = null;
  let entriesBox = null;
  let rootsBox = null;
  let pathInput = null;
  let statusEl = null;
  let selectBtn = null;
  let currentPath = '/app/kernel';
  let onSelect = function () {};
  let initialized = false;

  function dirname(path) {
    if (!path || path === '/') return '/';
    const parts = path.split('/').filter(Boolean);
    if (parts.length <= 1) return '/';
    return '/' + parts.slice(0, -1).join('/');
  }

  function close() {
    if (!modal) return;
    modal.hidden = true;
  }

  async function browse(path) {
    currentPath = path || currentPath || '/app/kernel';
    pathInput.value = currentPath;
    entriesBox.innerHTML = '<div class="small-muted">Loading...</div>';

    const res = await fetch('/api/fs/browse?path=' + encodeURIComponent(currentPath));
    const data = await res.json();

    if (!data.ok) {
      entriesBox.innerHTML = '';
      statusEl.textContent = data.error || 'Unable to browse this path.';
      return;
    }

    currentPath = data.path || currentPath;
    pathInput.value = currentPath;
    statusEl.textContent = 'Current path: ' + currentPath;
    entriesBox.innerHTML = '';

    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'folder-entry';
    upBtn.textContent = '⬆ ..';
    upBtn.onclick = function () { browse(dirname(currentPath)); };
    entriesBox.appendChild(upBtn);

    (data.entries || []).forEach(function (entry) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'folder-entry';
      row.textContent = (entry.type === 'dir' ? '📁 ' : '📄 ') + entry.name;
      row.onclick = function () {
        if (entry.type === 'dir') {
          browse(entry.path);
        }
      };
      entriesBox.appendChild(row);
    });
  }

  async function loadRoots() {
    if (!rootsBox) return;
    rootsBox.innerHTML = '';
    try {
      const res = await fetch('/api/fs/roots');
      const data = await res.json();
      (data.roots || []).forEach(function (item) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-secondary';
        btn.textContent = '📁 ' + (item.label || item.path);
        btn.addEventListener('click', function () {
          browse(item.path);
        });
        rootsBox.appendChild(btn);
      });
    } catch (err) {
      rootsBox.innerHTML = '';
    }
  }

  function ensureInit() {
    if (initialized) return;
    modal = document.getElementById('folderBrowserModal');
    if (!modal) return;
    entriesBox = document.getElementById('folderBrowserEntries');
    rootsBox = document.getElementById('folderBrowserRoots');
    pathInput = document.getElementById('folderBrowserPath');
    statusEl = document.getElementById('folderBrowserStatus');
    selectBtn = document.getElementById('folderBrowserSelect');

    modal.querySelectorAll('[data-folder-close]').forEach(function (btn) {
      btn.addEventListener('click', close);
    });
    document.getElementById('folderBrowserGo').addEventListener('click', function () {
      browse(pathInput.value.trim());
    });
    pathInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        browse(pathInput.value.trim());
      }
    });
    selectBtn.addEventListener('click', function () {
      onSelect(currentPath);
      close();
    });
    initialized = true;
  }

  function open(opts) {
    ensureInit();
    if (!modal) return;
    const config = opts || {};
    currentPath = config.startPath || currentPath || '/app/kernel';
    onSelect = typeof config.onSelect === 'function' ? config.onSelect : function () {};
    modal.hidden = false;
    loadRoots();
    browse(currentPath);
  }

  return { open: open };
})();
