window.AKDWDiff = (function () {
  let diffEditor = null;
  let originalModel = null;
  let modifiedModel = null;
  let activeFile = '';

  function languageFromFilename(filename) {
    const name = (filename || '').toLowerCase();
    if (name.endsWith('.c') || name.endsWith('.h')) return 'c';
    if (name.endsWith('.dts') || name.endsWith('.dtsi')) return 'plaintext';
    if (name.includes('makefile')) return 'makefile';
    return 'plaintext';
  }

  function init(containerId) {
    const el = document.getElementById(containerId);
    if (!el || !window.monaco) return null;

    diffEditor = monaco.editor.createDiffEditor(el, {
      theme: 'vs-dark',
      automaticLayout: true,
      readOnly: false,
      originalEditable: false,
      minimap: { enabled: false },
    });
    return diffEditor;
  }

  function showDiff(payload) {
    if (!diffEditor || !payload) return;
    activeFile = payload.filename || '';
    const lang = languageFromFilename(activeFile);
    if (originalModel) originalModel.dispose();
    if (modifiedModel) modifiedModel.dispose();
    originalModel = monaco.editor.createModel(payload.before || '', lang);
    modifiedModel = monaco.editor.createModel(payload.after || '', lang);
    diffEditor.setModel({ original: originalModel, modified: modifiedModel });
  }

  function getModified() {
    return modifiedModel ? modifiedModel.getValue() : '';
  }

  function getActiveFile() {
    return activeFile;
  }

  return {
    init: init,
    showDiff: showDiff,
    getModified: getModified,
    getActiveFile: getActiveFile,
  };
})();
