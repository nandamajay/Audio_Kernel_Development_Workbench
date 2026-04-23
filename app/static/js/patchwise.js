window.AKDWPatchwise = (function () {
  const pathKey = 'akdw_patchwise_path';
  const modelKey = 'akdw_patchwise_model';
  const sessionKey = 'akdw_patchwise_session';

  let files = [];
  let sessionId = localStorage.getItem(sessionKey) || ('pw-' + Math.random().toString(36).slice(2, 10));
  localStorage.setItem(sessionKey, sessionId);

  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function sevClass(sev) {
    const value = (sev || 'INFO').toLowerCase();
    if (value === 'critical') return 'sev-critical';
    if (value === 'warning') return 'sev-warning';
    if (value === 'suggestion') return 'sev-suggestion';
    return 'sev-info';
  }

  function escapeHtml(value) {
    return (value || '').replace(/[&<>"]/, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] || c;
    });
  }

  function renderFiles() {
    const box = document.getElementById('patchFileList');
    box.innerHTML = '';

    if (!files.length) {
      box.innerHTML = '<div class="small-muted">No files selected.</div>';
      return;
    }

    files.forEach(function (f, idx) {
      const row = document.createElement('div');
      row.className = 'tree-row';
      row.innerHTML = '<strong>' + f.name + '</strong> <span class="small-muted">(' + fmtSize(f.size) + ')</span>';

      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'btn-secondary';
      rm.style.float = 'right';
      rm.textContent = '✕';
      rm.addEventListener('click', function () {
        files.splice(idx, 1);
        renderFiles();
      });
      row.appendChild(rm);

      row.addEventListener('click', function () {
        if (typeof f.content === 'string') {
          document.getElementById('patchContent').value = f.content;
        }
      });

      box.appendChild(row);
    });
  }

  function handleFilesDropped(fileList) {
    const allowed = ['.c', '.h', '.dts', '.dtsi', '.patch', '.log', '.txt', '.diff'];
    Array.from(fileList || []).forEach(function (file) {
      const lower = file.name.toLowerCase();
      if (!allowed.some(function (ext) { return lower.endsWith(ext); })) {
        return;
      }
      const reader = new FileReader();
      reader.onload = function () {
        files.push({ name: file.name, size: file.size, content: reader.result || '' });
        renderFiles();
      };
      reader.readAsText(file);
    });
  }

  async function refreshPath() {
    const input = document.getElementById('patchPathInput');
    const listing = document.getElementById('patchPathListing');
    const path = input.value.trim() || '/app/kernel';
    localStorage.setItem(pathKey, path);

    const resp = await fetch('/api/fs/browse?path=' + encodeURIComponent(path));
    const data = await resp.json();
    if (!data.ok) {
      listing.textContent = data.error || 'Path not allowed';
      return;
    }
    input.value = data.path;
    listing.textContent = 'Active path: ' + data.path + ' (' + (data.entries || []).length + ' entries)';
  }

  function evidenceTemplate(item) {
    const findingId = item.id || ('f-' + Math.random().toString(36).slice(2, 8));
    return [
      '<div class="evidence-box" data-finding="' + findingId + '">',
      '  <div class="pw-actions">',
      '    <button type="button" class="btn-secondary ev-shot-btn">📷 Attach Screenshot</button>',
      '    <button type="button" class="btn-secondary ev-lkml-btn">🔗 LKML Link</button>',
      '  </div>',
      '  <input type="file" class="ev-shot-input" accept="image/png,image/jpeg" style="display:none">',
      '  <div class="ev-lkml-form" style="display:none;">',
      '    <input class="input ev-lkml-url" placeholder="Paste lore.kernel.org or LKML URL...">',
      '    <button type="button" class="btn-secondary ev-lkml-save">Save Link</button>',
      '  </div>',
      '  <div class="ev-items"></div>',
      '</div>'
    ].join('');
  }

  function renderFindings(findings, summary) {
    const box = document.getElementById('reviewFindings');
    const sum = document.getElementById('reviewSummary');
    box.innerHTML = '';
    sum.textContent = summary
      ? ('Summary - Critical: ' + (summary.critical || 0) + ', Warning: ' + (summary.warning || 0) + ', Suggestion: ' + (summary.suggestion || 0))
      : '';

    if (!Array.isArray(findings) || !findings.length) {
      box.innerHTML = '<div class="small-muted">No findings generated.</div>';
      return;
    }

    findings.forEach(function (item, index) {
      const fid = item.id || ('f-' + (index + 1));
      const card = document.createElement('div');
      card.className = 'finding-card';
      card.dataset.findingId = fid;
      card.innerHTML = [
        '<span class="severity-badge ' + sevClass(item.severity) + '">' + escapeHtml(item.severity || 'INFO') + '</span>',
        '<span class="loc-chip">' + escapeHtml((item.file || 'unknown') + ':' + (item.line || 0)) + '</span>',
        '<p>' + escapeHtml(item.description || '') + '</p>',
        '<details><summary>Suggested fix</summary><pre>' + escapeHtml(item.suggested_fix || '') + '</pre></details>',
        evidenceTemplate({ id: fid }),
      ].join('');
      box.appendChild(card);
    });

    wireEvidenceActions();
    loadEvidence();
  }

  async function attachScreenshot(findingId, base64Content) {
    const res = await fetch('/api/evidence/attach_screenshot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, finding_id: findingId, image_base64: base64Content }),
    });
    return res.json();
  }

  async function saveLkml(findingId, data) {
    const payload = {
      session_id: sessionId,
      finding_id: findingId,
      url: data.url,
      title: data.title,
      author: data.author,
      date: data.date,
    };
    const res = await fetch('/api/evidence/save_lkml', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.json();
  }

  function wireEvidenceActions() {
    document.querySelectorAll('.evidence-box').forEach(function (box) {
      const findingId = box.dataset.finding;
      const shotBtn = box.querySelector('.ev-shot-btn');
      const shotInput = box.querySelector('.ev-shot-input');
      const lkmlBtn = box.querySelector('.ev-lkml-btn');
      const lkmlForm = box.querySelector('.ev-lkml-form');
      const lkmlUrl = box.querySelector('.ev-lkml-url');
      const lkmlSave = box.querySelector('.ev-lkml-save');

      shotBtn.addEventListener('click', function () {
        shotInput.click();
      });

      shotInput.addEventListener('change', function () {
        const file = shotInput.files && shotInput.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async function () {
          const raw = String(reader.result || '');
          const base64Content = raw.includes(',') ? raw.split(',')[1] : raw;
          await attachScreenshot(findingId, base64Content);
          loadEvidence();
        };
        reader.readAsDataURL(file);
      });

      lkmlBtn.addEventListener('click', function () {
        lkmlForm.style.display = lkmlForm.style.display === 'none' ? 'grid' : 'none';
      });

      lkmlSave.addEventListener('click', async function () {
        const url = lkmlUrl.value.trim();
        if (!url) return;
        const previewRes = await fetch('/api/evidence/lkml_preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url }),
        });
        const preview = await previewRes.json();
        await saveLkml(findingId, preview);
        lkmlUrl.value = '';
        lkmlForm.style.display = 'none';
        loadEvidence();
      });
    });
  }

  async function loadEvidence() {
    const res = await fetch('/api/evidence/list/' + encodeURIComponent(sessionId));
    const data = await res.json();
    const records = data.records || [];

    document.querySelectorAll('.evidence-box').forEach(function (box) {
      const findingId = box.dataset.finding;
      const itemBox = box.querySelector('.ev-items');
      itemBox.innerHTML = '';

      records.filter(function (r) { return r.finding_id === findingId; }).forEach(function (record) {
        const wrap = document.createElement('div');
        if (record.evidence_type === 'screenshot') {
          wrap.innerHTML = '<img class="thumb" src="data:image/png;base64,' + record.content + '">';
        } else {
          const meta = record.metadata || {};
          wrap.className = 'lkml-chip';
          wrap.innerHTML = '🔗 <a href="' + (record.content || '#') + '" target="_blank">'
            + escapeHtml(meta.title || record.content || '') + '</a> '
            + escapeHtml((meta.author || 'Unknown') + ' · ' + (meta.date || ''));
        }

        const rm = document.createElement('button');
        rm.type = 'button';
        rm.className = 'btn-secondary';
        rm.textContent = '✕ Remove';
        rm.style.marginLeft = '8px';
        rm.addEventListener('click', async function () {
          await fetch('/api/evidence/' + record.id, { method: 'DELETE' });
          loadEvidence();
        });
        wrap.appendChild(rm);

        itemBox.appendChild(wrap);
      });
    });
  }

  async function runReview() {
    const patchContent = document.getElementById('patchContent').value;
    const contextUrl = document.getElementById('contextUrl').value.trim();
    const model = document.getElementById('patchModel').value;

    const res = await fetch('/api/patchwise/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        patch_content: patchContent,
        context_url: contextUrl,
        model: model,
      }),
    });
    const data = await res.json();
    if (!data.ok && !data.findings) {
      document.getElementById('reviewSummary').textContent = data.error || 'Review failed';
      return;
    }
    renderFindings(data.findings || [], data.summary || {});
  }

  async function runCheckpatch() {
    const patchContent = document.getElementById('patchContent').value;
    const res = await fetch('/api/patchwise/run_checkpatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, patch_content: patchContent }),
    });
    const data = await res.json();
    document.getElementById('checkpatchOutput').textContent =
      'checkpatch warnings: ' + (data.warnings_count || 0) + ', errors: ' + (data.errors_count || 0);
  }

  async function askReviewer() {
    const question = document.getElementById('pwAskInput').value.trim();
    if (!question) return;
    const patch = document.getElementById('patchContent').value;

    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId + '-chat',
        page: 'patchwise',
        message: 'Patch context:\n' + patch + '\n\nQuestion:\n' + question,
      }),
    });
    const data = await res.json();
    document.getElementById('pwAskOutput').textContent = data.response || data.content || data.message || 'No response';
  }

  function init() {
    const input = document.getElementById('patchPathInput');
    const model = document.getElementById('patchModel');
    input.value = localStorage.getItem(pathKey) || '/app/kernel';
    model.value = localStorage.getItem(modelKey) || model.value;

    document.getElementById('patchApplyBtn').addEventListener('click', refreshPath);
    model.addEventListener('change', function () {
      localStorage.setItem(modelKey, model.value);
    });

    document.getElementById('patchBrowseBtn').addEventListener('click', function () {
      if (!window.AKDWFolderBrowser) return;
      window.AKDWFolderBrowser.open({
        startPath: input.value.trim() || '/app/kernel',
        onSelect: function (selectedPath) {
          input.value = selectedPath;
          refreshPath();
        },
      });
    });

    document.getElementById('browse-btn').addEventListener('click', function () {
      document.getElementById('pw-file-input').click();
    });

    document.getElementById('pw-file-input').addEventListener('change', function (e) {
      const picked = Array.from(e.target.files || []);
      handleFilesDropped(picked);
      e.target.value = '';
    });

    const zone = document.getElementById('patchDropZone');
    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('dragging');
    });
    zone.addEventListener('dragleave', function (e) {
      e.preventDefault();
      zone.classList.remove('dragging');
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dragging');
      const dropped = Array.from(e.dataTransfer.files || []);
      handleFilesDropped(dropped);
    });

    document.getElementById('runReviewBtn').addEventListener('click', runReview);
    document.getElementById('runCheckpatchBtn').addEventListener('click', runCheckpatch);
    document.getElementById('exportBtn').addEventListener('click', function () {
      window.open('/api/patchwise/export/' + encodeURIComponent(sessionId), '_blank');
    });
    document.getElementById('pwAskBtn').addEventListener('click', askReviewer);

    refreshPath();
    renderFiles();
  }

  return {
    init: init,
    handleFilesDropped: handleFilesDropped,
  };
})();

window.addEventListener('DOMContentLoaded', function () {
  if (window.AKDWPatchwise) {
    window.AKDWPatchwise.init();
  }
});
