/**
 * AKDW QGenie Drawer
 * Collapsible right-side assistant panel.
 */

const AKDW_QGenie = (() => {
  let drawerOpen = false;

  function toggleQGenieDrawer() {
    const drawer = document.getElementById('qgenieDrawer');
    const toggleBtn = document.getElementById('qgenieToggleBtn');
    if (!drawer || !toggleBtn) return;

    drawerOpen = !drawerOpen;
    if (drawerOpen) {
      drawer.classList.remove('collapsed');
      toggleBtn.classList.add('hidden');
    } else {
      drawer.classList.add('collapsed');
      toggleBtn.classList.remove('hidden');
    }
    AKDW_Terminal.resizeAll();
  }

  function handleDrawerKey(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendDrawerMessage();
    }
  }

  async function sendDrawerMessage() {
    const input = document.getElementById('drawerInput');
    const text = (input.value || '').trim();
    if (!text) return;

    input.value = '';
    addMessage('user', text);
    const thinkingId = addMessage('thinking', 'Thinking...');

    try {
      const response = await fetch('/api/agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          context: 'terminal_ide',
          page: 'editor',
          session_id: AKDW_Terminal.getActive() || undefined
        })
      });

      const data = await response.json();
      removeMessage(thinkingId);
      addMessage('assistant', data.response || data.message || 'No response');
    } catch (err) {
      removeMessage(thinkingId);
      addMessage('error', 'QGenie error: ' + err.message);
    }
  }

  function addMessage(role, content) {
    const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    const container = document.getElementById('drawerMessages');
    if (!container) return id;

    const msgEl = document.createElement('div');
    msgEl.id = id;
    msgEl.className = 'drawer-msg drawer-msg-' + role;

    if (role === 'assistant') {
      msgEl.innerHTML =
        '<div class="msg-content">' + renderMarkdown(content) + '</div>' +
        '<button class="msg-copy" onclick="copyToClipboard(this)" data-text="' +
        encodeURIComponent(content) +
        '" title="Copy response">Copy</button>';
    } else {
      msgEl.innerHTML = '<div class="msg-content">' + escapeHtml(content) + '</div>';
    }

    container.appendChild(msgEl);
    container.scrollTop = container.scrollHeight;
    return id;
  }

  function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function renderMarkdown(text) {
    const fenced = /```(\w+)?\n([\s\S]*?)```/g;
    let html = escapeHtml(text || '');

    html = html.replace(fenced, (_m, lang, code) => {
      const safeCode = escapeHtml((code || '').trim());
      const rawCode = encodeURIComponent((code || '').trim());
      return (
        '<div class="code-block">' +
          '<div class="code-lang">' + escapeHtml(lang || '') + '</div>' +
          '<pre><code>' + safeCode + '</code></pre>' +
          '<button class="code-copy" onclick="copyToClipboard(this)" data-text="' + rawCode + '">Copy</button>' +
        '</div>'
      );
    });

    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/\n/g, '<br>');

    return html;
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
    toggleQGenieDrawer,
    handleDrawerKey,
    sendDrawerMessage
  };
})();

function toggleQGenieDrawer() { AKDW_QGenie.toggleQGenieDrawer(); }
function handleDrawerKey(event) { AKDW_QGenie.handleDrawerKey(event); }
function sendDrawerMessage() { AKDW_QGenie.sendDrawerMessage(); }

function copyToClipboard(btn) {
  const text = decodeURIComponent((btn && btn.dataset && btn.dataset.text) || '');
  if (!navigator.clipboard) return;
  navigator.clipboard.writeText(text).then(() => {
    const original = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => {
      btn.textContent = original;
    }, 1200);
  });
}
