(function () {
  const state = {
    mode: "link",
    status: "IDLE",
    sourceCode: "",
    metadata: {},
    filename: "",
    patch: "",
  };

  let diffEditor = null;
  let diffModels = { original: null, modified: null };
  let statusTimer = null;
  let statusPhaseIndex = 0;
  const phases = [
    "Analyzing downstream patterns...",
    "Removing QCOM-internal APIs...",
    "Applying upstream conventions...",
    "Generating patch format...",
  ];

  const modeLinkBtn = document.getElementById("modeLink");
  const modeFileBtn = document.getElementById("modeFile");
  const linkSection = document.getElementById("linkModeSection");
  const fileSection = document.getElementById("fileModeSection");
  const fetchBtn = document.getElementById("fetchBtn");
  const convertBtn = document.getElementById("convertBtn");
  const convertStatus = document.getElementById("convertStatus");
  const grid = document.getElementById("converterGrid");
  const resultsPanel = document.getElementById("resultsPanel");

  function setStatus(next) {
    state.status = next;
    if (next === "FETCHING") {
      setBanner("Fetching source...");
    } else if (next === "FETCHED") {
      setBanner("Source fetched. Ready to convert.");
    } else if (next === "CONVERTING") {
      setBanner(phases[0]);
    } else if (next === "DONE") {
      setBanner("Conversion complete.");
    } else if (next === "ERROR") {
      setBanner("Conversion failed.");
    } else {
      setBanner("");
    }
    updateConvertButton();
  }

  function setBanner(text) {
    if (!convertStatus) return;
    convertStatus.textContent = text || "";
  }

  function setMode(mode) {
    state.mode = mode;
    const isLink = mode === "link";
    modeLinkBtn.classList.toggle("active", isLink);
    modeFileBtn.classList.toggle("active", !isLink);
    linkSection.style.display = isLink ? "grid" : "none";
    fileSection.style.display = isLink ? "none" : "grid";
    updateConvertButton();
  }

  function updateConvertButton() {
    const ready = !!state.sourceCode.trim();
    convertBtn.disabled = !ready || state.status === "FETCHING" || state.status === "CONVERTING";
    convertBtn.classList.toggle("ready", ready);
  }

  function metadataCard(active) {
    const card = document.getElementById("metadataCard");
    if (!card) return;
    card.classList.toggle("active", !!active);
  }

  function gerritCard(active) {
    const card = document.getElementById("gerritAuthCard");
    if (!card) return;
    card.classList.toggle("active", !!active);
  }

  function populateMetadata(meta) {
    document.getElementById("metaFilename").textContent = meta.filename || "-";
    document.getElementById("metaCl").textContent = meta.cl_number || "-";
    document.getElementById("metaSubject").textContent = meta.subject || "-";
    document.getElementById("metaAuthor").textContent = meta.author || "-";
    document.getElementById("metaPath").textContent = meta.file_path || "-";
    document.getElementById("metaRepo").textContent = meta.repo || "-";
    document.getElementById("metaSource").textContent = state.sourceCode || "";
  }

  async function fetchSource() {
    const url = document.getElementById("sourceLink").value.trim();
    if (!url) return;
    setStatus("FETCHING");
    const payload = {
      url: url,
      gerrit_username: document.getElementById("gerritUser").value,
      gerrit_password: document.getElementById("gerritPass").value,
    };
    try {
      const res = await fetch("/api/converter/fetch-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.success) {
        setStatus("ERROR");
        setBanner(data.error || "Fetch failed");
        return;
      }
      state.sourceCode = data.source_code || "";
      state.metadata = data.metadata || {};
      state.filename = data.filename || "driver.c";
      state.metadata.filename = state.filename;
      metadataCard(true);
      populateMetadata(state.metadata);
      gerritCard(data.link_type === "gerrit");
      setStatus("FETCHED");
    } catch (err) {
      setStatus("ERROR");
      setBanner("Fetch failed");
    }
  }

  async function testGerritAuth() {
    const payload = {
      gerrit_username: document.getElementById("gerritUser").value,
      gerrit_password: document.getElementById("gerritPass").value,
    };
    const status = document.getElementById("gerritAuthStatus");
    status.textContent = "Testing...";
    try {
      const res = await fetch("/api/converter/gerrit-auth-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        status.textContent = "✅ Auth OK — Connected as " + (data.username || "user");
      } else {
        status.textContent = "❌ " + (data.error || "Auth failed");
      }
    } catch (err) {
      status.textContent = "❌ Auth failed";
    }
  }

  function startConvertSpinner() {
    statusPhaseIndex = 0;
    setBanner(phases[statusPhaseIndex]);
    statusTimer = setInterval(function () {
      statusPhaseIndex = (statusPhaseIndex + 1) % phases.length;
      setBanner(phases[statusPhaseIndex]);
    }, 1400);
  }

  function stopConvertSpinner() {
    if (statusTimer) clearInterval(statusTimer);
    statusTimer = null;
  }

  function collectConversionType() {
    const choices = document.querySelectorAll("input[name='convType']");
    for (const choice of choices) {
      if (choice.checked) return choice.value;
    }
    return "full_upstream";
  }

  async function convertDriver() {
    if (!state.sourceCode.trim()) return;
    setStatus("CONVERTING");
    startConvertSpinner();

    const payload = {
      source_code: state.sourceCode,
      filename: state.filename || "driver.c",
      metadata: state.metadata || {},
      requirements: document.getElementById("requirements").value,
      conversion_type: collectConversionType(),
      target_kernel: document.getElementById("targetKernel").value,
      model: document.getElementById("modelSelect").value,
    };

    try {
      const res = await fetch("/api/converter/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      stopConvertSpinner();
      if (!data.success) {
        setStatus("ERROR");
        setBanner(data.error || "Conversion failed");
        return;
      }
      state.patch = data.patch || "";
      renderDiff(state.patch);
      renderSummary(data.summary || "", data.files_modified || []);
      showResults();
      setStatus("DONE");
    } catch (err) {
      stopConvertSpinner();
      setStatus("ERROR");
      setBanner("Conversion failed");
    }
  }

  function showResults() {
    if (!grid || !resultsPanel) return;
    resultsPanel.classList.add("active");
    grid.classList.add("has-results");
  }

  function renderSummary(summary, files) {
    const panel = document.getElementById("summaryPanel");
    if (!panel) return;
    const fileList = (files || []).map(function (file) {
      return "• " + file;
    }).join("\n");
    panel.textContent = summary || (fileList ? ("Files modified:\n" + fileList) : "No summary provided.");
  }

  function renderDiff(text) {
    const container = document.getElementById("diffViewer");
    if (!container) return;
    if (!window.require) {
      container.textContent = text || "";
      return;
    }

    window.require.config({ paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs" } });
    window.require(["vs/editor/editor.main"], function () {
      if (!diffEditor) {
        diffEditor = monaco.editor.createDiffEditor(container, {
          readOnly: true,
          theme: "vs-dark",
          automaticLayout: true,
        });
      }
      if (diffModels.original) diffModels.original.dispose();
      if (diffModels.modified) diffModels.modified.dispose();
      diffModels.original = monaco.editor.createModel("", "diff");
      diffModels.modified = monaco.editor.createModel(text || "", "diff");
      diffEditor.setModel({ original: diffModels.original, modified: diffModels.modified });
    });
  }

  async function renderJobHistory() {
    const box = document.getElementById("jobHistory");
    if (!box) return;
    box.innerHTML = "";
    try {
      const res = await fetch("/api/converter/jobs");
      const data = await res.json();
      if (!Array.isArray(data) || !data.length) {
        box.innerHTML = '<div class="small-muted">No conversion jobs yet.</div>';
        return;
      }
      data.slice(0, 12).forEach(function (job) {
        const card = document.createElement("div");
        card.className = "job-card";
        card.innerHTML =
          "<strong>" + (job.filename || "driver") + "</strong><br>" +
          "CL: " + (job.cl_number || "-") + " · " + (job.status || "-") + "<br>" +
          "Req: " + (job.requirements || "").slice(0, 80);
        box.appendChild(card);
      });
    } catch (err) {
      box.innerHTML = '<div class="small-muted">Failed to load history.</div>';
    }
  }

  function sendToPatchWorkshop() {
    if (!state.patch) return;
    sessionStorage.setItem("workshop_pending_patch", state.patch);
    window.location.href = "/patchwise/";
  }

  async function copyPatch() {
    if (!state.patch) return;
    try {
      await navigator.clipboard.writeText(state.patch);
      setBanner("Patch copied to clipboard.");
    } catch (err) {
      setBanner("Unable to copy patch.");
    }
  }

  function downloadPatch() {
    if (!state.patch) return;
    const blob = new Blob([state.patch], { type: "text/plain" });
    const link = document.createElement("a");
    const ts = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
    link.href = URL.createObjectURL(blob);
    link.download = "converted_driver_" + ts + ".patch";
    document.body.appendChild(link);
    link.click();
    URL.revokeObjectURL(link.href);
    link.remove();
  }

  function bindTabs() {
    const tabs = document.querySelectorAll(".tab-btn");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(btn => btn.classList.remove("active"));
        tab.classList.add("active");
        const target = tab.getAttribute("data-tab");
        document.getElementById("patchTab").style.display = target === "patch" ? "block" : "none";
        document.getElementById("summaryTab").style.display = target === "summary" ? "block" : "none";
        document.getElementById("historyTab").style.display = target === "history" ? "block" : "none";
        if (target === "history") renderJobHistory();
      });
    });
  }

  function bindFileUpload() {
    const drop = document.getElementById("uploadDrop");
    const input = document.getElementById("fileInput");
    if (!drop || !input) return;

    drop.addEventListener("click", function () {
      input.click();
    });
    input.addEventListener("change", function (e) {
      const file = (e.target.files || [])[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = function () {
        state.sourceCode = reader.result || "";
        state.filename = file.name;
        state.metadata = { filename: file.name, file_path: file.name };
        metadataCard(true);
        populateMetadata(state.metadata);
        setStatus("FETCHED");
      };
      reader.readAsText(file);
    });

    drop.addEventListener("dragover", function (e) {
      e.preventDefault();
      drop.classList.add("dragging");
    });
    drop.addEventListener("dragleave", function () {
      drop.classList.remove("dragging");
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      drop.classList.remove("dragging");
      const file = (e.dataTransfer.files || [])[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = function () {
        state.sourceCode = reader.result || "";
        state.filename = file.name;
        state.metadata = { filename: file.name, file_path: file.name };
        metadataCard(true);
        populateMetadata(state.metadata);
        setStatus("FETCHED");
      };
      reader.readAsText(file);
    });
  }

  function bindMetadataToggle() {
    const toggle = document.getElementById("toggleSource");
    const source = document.getElementById("metaSource");
    if (!toggle || !source) return;
    toggle.addEventListener("click", function () {
      source.classList.toggle("active");
      toggle.textContent = source.classList.contains("active") ? "Hide Source ▲" : "View Source ▼";
    });
  }

  modeLinkBtn.addEventListener("click", function () { setMode("link"); });
  modeFileBtn.addEventListener("click", function () { setMode("file"); });
  fetchBtn.addEventListener("click", fetchSource);
  convertBtn.addEventListener("click", convertDriver);
  document.getElementById("gerritAuthTest").addEventListener("click", testGerritAuth);
  document.getElementById("copyPatchBtn").addEventListener("click", copyPatch);
  document.getElementById("downloadPatchBtn").addEventListener("click", downloadPatch);
  document.getElementById("sendWorkshopBtn").addEventListener("click", sendToPatchWorkshop);

  bindTabs();
  bindFileUpload();
  bindMetadataToggle();
  setMode("link");
  setStatus("IDLE");
})();
