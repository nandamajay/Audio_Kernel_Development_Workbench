window.AKDWPatchwise = (function () {
  const pathKey = "akdw_patchwise_path";
  const modelKey = "akdw_patchwise_model";
  const sessionKey = "akdw_patchwise_session";
  const allowedExt = [".c", ".h", ".dts", ".dtsi", ".patch", ".log", ".txt", ".diff"];

  let files = [];
  let hasResults = false;
  let reviewStarted = false;
  let exportedReport = false;
  let reviewProgressTimer = null;
  let sessionId = localStorage.getItem(sessionKey) || ("pw-" + Math.random().toString(36).slice(2, 10));
  localStorage.setItem(sessionKey, sessionId);
  let activeTargetPath = "";
  let autofixSelectedFixIds = [];
  let lastAutofixPreview = null;
  let lastAutofixBackupPath = "";
  let lastTraceId = "";

  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function sevClass(sev) {
    const value = (sev || "INFO").toLowerCase();
    if (value === "critical" || value === "error") return "sev-critical";
    if (value === "warning") return "sev-warning";
    if (value === "suggestion") return "sev-suggestion";
    return "sev-info";
  }

  function escapeHtml(value) {
    return (value || "").replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] || c;
    });
  }

  function linkifyText(text) {
    return (text || "").replace(
      /(https?:\/\/[^\s<>"]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer" class="chat-link">$1 ↗</a>'
    );
  }

  function asMarkdown(text) {
    const safe = linkifyText(text || "");
    if (!window.marked) return safe;
    const renderer = new window.marked.Renderer();
    renderer.link = function (href, title, textValue) {
      const label = textValue || href;
      const tip = title || href;
      return (
        '<a href="' + href + '" target="_blank" rel="noopener noreferrer" class="chat-link" title="' +
        escapeHtml(tip) + '">' + label + ' ↗</a>'
      );
    };
    window.marked.setOptions({ renderer: renderer, breaks: true });
    return window.marked.parse(safe);
  }

  function setStepState(uploadLoaded, reviewInProgress, resultsReady, reportExported) {
    const s1 = document.getElementById("stepUpload");
    const s2 = document.getElementById("stepReview");
    const s3 = document.getElementById("stepResults");
    const s4 = document.getElementById("stepExport");
    s1.className = "step-item";
    s2.className = "step-item";
    s3.className = "step-item";
    s4.className = "step-item";

    if (!uploadLoaded) {
      s1.classList.add("active");
      return;
    }
    s1.classList.add("completed");
    if (!reviewInProgress) {
      s2.classList.add("active");
      return;
    }
    s2.classList.add("completed");
    if (!resultsReady) {
      s3.classList.add("active");
      return;
    }
    s3.classList.add("completed");
    if (!reportExported) {
      s4.classList.add("active");
      return;
    }
    s4.classList.add("completed");
  }

  function hasPatchContent() {
    const content = document.getElementById("patchContent").value.trim();
    return content.length > 0 || files.length > 0;
  }

  function supportedPathFile(name) {
    const lower = String(name || "").toLowerCase();
    return [".patch", ".diff", ".c", ".h", ".log", ".txt"].some(function (ext) {
      return lower.endsWith(ext);
    });
  }

  function setActionState() {
    const ready = hasPatchContent();
    const reviewBtn = document.getElementById("runReviewBtn");
    const pipelineBtn = document.getElementById("runPipelineBtn");
    const checkBtn = document.getElementById("runCheckpatchBtn");
    const autofixBtn = document.getElementById("previewAutofixBtn");
    const exportBtn = document.getElementById("exportBtn");
    const applyAutofixBtn = document.getElementById("applyAutofixBtn");
    const rollbackAutofixBtn = document.getElementById("rollbackAutofixBtn");

    [reviewBtn, pipelineBtn, checkBtn, autofixBtn, exportBtn].forEach(function (btn) {
      if (!btn) return;
      btn.disabled = !ready || (btn.id === "exportBtn" && !hasResults);
      btn.classList.toggle("btn-disabled", btn.disabled);
      btn.title = btn.disabled ? "Load a patch file first" : "";
    });

    reviewBtn.classList.toggle("ready", ready && !hasResults);
    if (applyAutofixBtn) {
      const canApply = ready && !!(lastAutofixPreview && lastAutofixPreview.has_changes);
      applyAutofixBtn.disabled = !canApply;
      applyAutofixBtn.classList.toggle("btn-disabled", !canApply);
    }
    if (rollbackAutofixBtn) {
      const canRollback = !!(activeTargetPath && lastAutofixBackupPath);
      rollbackAutofixBtn.disabled = !canRollback;
      rollbackAutofixBtn.classList.toggle("btn-disabled", !canRollback);
    }
    if (hasResults) {
      exportBtn.style.borderColor = "rgba(16, 185, 129, 0.45)";
      exportBtn.style.boxShadow = "0 0 12px rgba(16, 185, 129, 0.35)";
    } else {
      exportBtn.style.borderColor = "";
      exportBtn.style.boxShadow = "";
    }
  }

  function addAssistantBubble(role, text) {
    const box = document.getElementById("pwAskMessages");
    const row = document.createElement("div");
    row.className = "assistant-bubble " + (role === "user" ? "user" : "ai");
    if (role === "user") {
      row.textContent = text;
    } else {
      row.innerHTML = '<button class="copy-btn" type="button" title="Copy response">📋</button>' +
        '<div class="msg-content">' + asMarkdown(text) + "</div>";
      const copyBtn = row.querySelector(".copy-btn");
      if (copyBtn) {
        copyBtn.addEventListener("click", function () {
          const msg = row.querySelector(".msg-content");
          navigator.clipboard.writeText(msg ? msg.innerText : "").then(function () {
            copyBtn.textContent = "✅";
            setTimeout(function () { copyBtn.textContent = "📋"; }, 1500);
          });
        });
      }
    }
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  function renderFiles() {
    const list = document.getElementById("patchFileList");
    const onboarding = document.getElementById("onboardingPrompt");
    const hint = document.getElementById("contextHint");
    const readyBanner = document.getElementById("fileReadyBanner");
    const nextTip = document.getElementById("fileNextTip");
    list.innerHTML = "";

    if (!files.length) {
      activeTargetPath = "";
      autofixSelectedFixIds = [];
      lastAutofixPreview = null;
      lastAutofixBackupPath = "";
      onboarding.style.display = "grid";
      hint.style.display = "none";
      readyBanner.style.display = "none";
      nextTip.style.display = "none";
      list.innerHTML = '<div class="small-muted">No files selected.</div>';
      setStepState(false, reviewStarted, hasResults, exportedReport);
      setActionState();
      return;
    }

    onboarding.style.display = "none";
    hint.style.display = "block";
    readyBanner.style.display = "block";
    nextTip.style.display = "block";
    readyBanner.textContent = "✅ " + files[0].name + " loaded (" + fmtSize(files[0].size) + ") — ready to review";
    setStepState(true, reviewStarted, hasResults, exportedReport);

    files.forEach(function (f, idx) {
      const chip = document.createElement("div");
      chip.className = "file-chip";
      chip.innerHTML = "📄 " + escapeHtml(f.name) + " <span class=\"small-muted\">(" + fmtSize(f.size) + ")</span>";

      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "btn-secondary";
      rm.style.padding = "2px 8px";
      rm.textContent = "✕";
      rm.addEventListener("click", function () {
        files.splice(idx, 1);
        if (files.length === 0) {
          document.getElementById("patchContent").value = "";
          hasResults = false;
          reviewStarted = false;
          exportedReport = false;
          document.getElementById("reviewFindings").innerHTML = "";
          document.getElementById("reviewPlaceholder").style.display = "grid";
        }
        renderFiles();
      });
      chip.appendChild(rm);
      chip.addEventListener("click", function () {
        document.getElementById("patchContent").value = f.content || "";
        activeTargetPath = f.path || "";
        setActionState();
      });
      list.appendChild(chip);
    });

    if (files[0] && !document.getElementById("patchContent").value.trim()) {
      document.getElementById("patchContent").value = files[0].content || "";
      activeTargetPath = files[0].path || "";
    }
    setActionState();
  }

  function setReviewRunning(isRunning) {
    const reviewBtn = document.getElementById("runReviewBtn");
    const pipelineBtn = document.getElementById("runPipelineBtn");
    const hintBtn = document.getElementById("hintRunReviewBtn");
    const hintPipelineBtn = document.getElementById("hintRunPipelineBtn");
    const placeholderBtn = document.getElementById("placeholderRunBtn");
    const checkBtn = document.getElementById("runCheckpatchBtn");
    const autofixBtn = document.getElementById("previewAutofixBtn");

    if (isRunning) {
      reviewBtn.disabled = true;
      pipelineBtn.disabled = true;
      checkBtn.disabled = true;
      autofixBtn.disabled = true;
      hintBtn.disabled = true;
      hintPipelineBtn.disabled = true;
      placeholderBtn.disabled = true;
      reviewBtn.classList.add("btn-disabled");
      reviewBtn.innerHTML = '<span class="btn-spinner"></span>Reviewing...';
      return;
    }

    reviewBtn.innerHTML = "▶ Run AI Review";
    pipelineBtn.disabled = false;
    hintBtn.disabled = false;
    hintPipelineBtn.disabled = false;
    placeholderBtn.disabled = false;
    setActionState();
  }

  function startReviewProgress() {
    const progressEl = document.getElementById("reviewProgress");
    const fillEl = document.getElementById("reviewProgressFill");
    const pctEl = document.getElementById("reviewProgressPct");
    let pct = 8;

    progressEl.style.display = "block";
    fillEl.style.width = pct + "%";
    pctEl.textContent = pct + "%";

    clearInterval(reviewProgressTimer);
    reviewProgressTimer = setInterval(function () {
      pct = Math.min(90, pct + Math.floor(Math.random() * 9) + 3);
      fillEl.style.width = pct + "%";
      pctEl.textContent = pct + "%";
      if (pct >= 90) {
        clearInterval(reviewProgressTimer);
        reviewProgressTimer = null;
      }
    }, 280);
  }

  function stopReviewProgress(success) {
    const progressEl = document.getElementById("reviewProgress");
    const fillEl = document.getElementById("reviewProgressFill");
    const pctEl = document.getElementById("reviewProgressPct");
    clearInterval(reviewProgressTimer);
    reviewProgressTimer = null;

    if (success) {
      fillEl.style.width = "100%";
      pctEl.textContent = "100%";
    }
    setTimeout(function () {
      progressEl.style.display = "none";
    }, success ? 420 : 0);
  }

  function handleFilesDropped(fileList) {
    Array.from(fileList || []).forEach(function (file) {
      const lower = file.name.toLowerCase();
      if (!allowedExt.some(function (ext) { return lower.endsWith(ext); })) return;
      const reader = new FileReader();
      reader.onload = function () {
        files.push({ name: file.name, size: file.size, content: reader.result || "", path: "" });
        activeTargetPath = "";
        autofixSelectedFixIds = [];
        lastAutofixPreview = null;
        lastAutofixBackupPath = "";
        renderFiles();
      };
      reader.readAsText(file);
    });
  }

  async function refreshPath() {
    const input = document.getElementById("patchPathInput");
    const listing = document.getElementById("patchPathListing");
    const listEl = document.getElementById("patchFileList");
    const onboarding = document.getElementById("onboardingPrompt");
    const path = input.value.trim() || "/app/kernel";
    localStorage.setItem(pathKey, path);
    const resp = await fetch("/api/fs/browse?path=" + encodeURIComponent(path));
    const data = await resp.json();
    if (!data.ok) {
      listing.textContent = data.error || "Path not allowed";
      return;
    }
    input.value = data.path;
    const entries = (data.entries || []).filter(function (item) {
      return item.type === "file" && supportedPathFile(item.name);
    });
    listing.textContent = "Active path: " + data.path + " (" + entries.length + " candidate files)";

    if (!entries.length) {
      listEl.innerHTML = '<div class="small-muted">No patch/source files found in this path.</div>';
      return;
    }

    onboarding.style.display = "none";
    listEl.innerHTML = "";
    entries.forEach(function (entry) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "tree-row";
      row.textContent = "📄 " + entry.name;
      row.addEventListener("click", async function () {
        const readRes = await fetch("/api/fs/read?path=" + encodeURIComponent(entry.path));
        const readData = await readRes.json();
        if (!readData.ok) {
          document.getElementById("reviewSummary").textContent = readData.error || "Failed to load file";
          return;
        }
        files = [{ name: entry.name, size: (readData.content || "").length, content: readData.content || "", path: entry.path }];
        activeTargetPath = entry.path || "";
        autofixSelectedFixIds = [];
        lastAutofixPreview = null;
        lastAutofixBackupPath = "";
        hasResults = false;
        reviewStarted = false;
        exportedReport = false;
        document.getElementById("patchContent").value = readData.content || "";
        renderFiles();
        setStepState(true, false, false, false);
        setActionState();
      });
      listEl.appendChild(row);
    });

    if (entries.length === 1) {
      listEl.querySelector("button")?.click();
    }
  }

  function evidenceTemplate(findingId, subject) {
    return [
      '<div class="evidence-box" data-finding="' + findingId + '">',
      '  <div class="patch-actions">',
      '    <button type="button" class="btn-secondary ev-shot-btn">📷 Attach Screenshot</button>',
      '    <button type="button" class="btn-secondary ev-lkml-btn">🔗 LKML Link</button>',
      '    <button type="button" class="btn-secondary ev-lkml-find">🔎 Find on LKML</button>',
      "  </div>",
      '  <input type="file" class="ev-shot-input" accept="image/png,image/jpeg" style="display:none">',
      '  <div class="ev-lkml-form" style="display:none;">',
      '    <input class="input ev-lkml-url" placeholder="Paste lore.kernel.org or LKML URL...">',
      '    <button type="button" class="btn-secondary ev-lkml-save">Save Link</button>',
      "  </div>",
      '  <div class="ev-items" data-subject="' + escapeHtml(subject || "") + '"></div>',
      "</div>",
    ].join("");
  }

  function renderMaintainers(list) {
    const box = document.getElementById("reviewSummary");
    if (!Array.isArray(list) || list.length === 0) return;
    const cards = list.map(function (m) {
      const name = escapeHtml(m.name || "Unknown");
      const email = escapeHtml(m.email || "");
      const role = escapeHtml(m.role || "maintainer");
      return '<a class="lkml-chip" href="mailto:' + email + '">' + name + " &lt;" + email + "&gt; · " + role + "</a>";
    }).join(" ");
    box.innerHTML += '<div style="margin-top:8px;"><strong>Maintainers</strong><div style="margin-top:6px;">' + cards + "</div></div>";
  }

  async function loadSessionList() {
    const root = document.getElementById("patchSessions");
    if (!root) return;
    const res = await fetch("/api/patchwise/sessions");
    const data = await res.json();
    const rows = data.sessions || [];
    root.innerHTML = "";
    if (!rows.length) {
      root.innerHTML = '<div class="small-muted">No sessions yet.</div>';
      return;
    }
    rows.slice(0, 10).forEach(function (item) {
      const entry = document.createElement("button");
      entry.type = "button";
      entry.className = "session-entry";
      const label = (item.patch_filename || item.session_id || "").slice(0, 26);
      const status = item.status || "pending";
      entry.innerHTML = '<span>' + escapeHtml(label) + '</span><span class="status-badge ' + escapeHtml(status) + '">' +
        escapeHtml(status) + "</span>";
      entry.addEventListener("click", async function () {
        const detailRes = await fetch("/api/patchwise/session/" + encodeURIComponent(item.session_id));
        const detail = await detailRes.json();
        if (!detail.ok || !detail.session) return;
        sessionId = item.session_id;
        localStorage.setItem(sessionKey, sessionId);
        document.getElementById("patchContent").value = "";
        activeTargetPath = "";
        autofixSelectedFixIds = [];
        lastAutofixPreview = null;
        lastAutofixBackupPath = "";
        renderFindings(detail.session.findings || [], detail.session.summary || {});
        document.getElementById("reviewPlaceholder").style.display = "none";
        hasResults = (detail.session.findings || []).length > 0;
        reviewStarted = hasResults;
        exportedReport = (item.status || "") === "exported";
        setStepState(true, reviewStarted, hasResults, exportedReport);
        setActionState();
        loadTraceList();
      });
      root.appendChild(entry);
    });
  }

  function renderFindings(findings, summary) {
    const box = document.getElementById("reviewFindings");
    const sum = document.getElementById("reviewSummary");
    const placeholder = document.getElementById("reviewPlaceholder");
    box.innerHTML = "";
    placeholder.style.display = "none";

    sum.textContent = summary
      ? "Summary - Critical: " + (summary.critical || 0) + ", Warning: " + (summary.warning || 0) +
        ", Suggestion: " + (summary.suggestion || 0) + ", Info: " + (summary.info || 0)
      : "";

    if (!Array.isArray(findings) || !findings.length) {
      box.innerHTML = '<div class="small-muted">No findings generated.</div>';
      return;
    }

    findings.forEach(function (item, index) {
      const fid = item.id || ("f-" + (index + 1));
      const subject = item.description || "";
      const card = document.createElement("div");
      card.className = "finding-card results-enter";
      card.style.animationDelay = Math.min(index * 60, 360) + "ms";
      card.dataset.findingId = fid;
      card.innerHTML = [
        '<span class="severity-badge ' + sevClass(item.severity) + '">' + escapeHtml(item.severity || "INFO") + "</span>",
        '<span class="loc-chip">' + escapeHtml((item.file || "unknown") + ":" + (item.line || 0)) + "</span>",
        "<p>" + escapeHtml(item.description || "") + "</p>",
        "<details><summary>Suggested fix</summary><pre>" + escapeHtml(item.suggested_fix || "") + "</pre></details>",
        evidenceTemplate(fid, subject),
      ].join("");
      box.appendChild(card);
    });

    wireEvidenceActions();
    loadEvidence();
  }

  async function attachScreenshot(findingId, base64Content) {
    const res = await fetch("/api/evidence/attach_screenshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
    const res = await fetch("/api/evidence/save_lkml", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return res.json();
  }

  function wireEvidenceActions() {
    document.querySelectorAll(".evidence-box").forEach(function (box) {
      const findingId = box.dataset.finding;
      const shotBtn = box.querySelector(".ev-shot-btn");
      const shotInput = box.querySelector(".ev-shot-input");
      const lkmlBtn = box.querySelector(".ev-lkml-btn");
      const lkmlFind = box.querySelector(".ev-lkml-find");
      const lkmlForm = box.querySelector(".ev-lkml-form");
      const lkmlUrl = box.querySelector(".ev-lkml-url");
      const lkmlSave = box.querySelector(".ev-lkml-save");
      const subject = box.querySelector(".ev-items").dataset.subject || "";

      shotBtn.addEventListener("click", function () {
        shotInput.click();
      });

      shotInput.addEventListener("change", function () {
        const file = shotInput.files && shotInput.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async function () {
          const raw = String(reader.result || "");
          const base64Content = raw.includes(",") ? raw.split(",")[1] : raw;
          await attachScreenshot(findingId, base64Content);
          loadEvidence();
        };
        reader.readAsDataURL(file);
      });

      lkmlBtn.addEventListener("click", function () {
        lkmlForm.style.display = lkmlForm.style.display === "none" ? "grid" : "none";
      });

      lkmlFind.addEventListener("click", function () {
        const q = encodeURIComponent(subject || "linux kernel patch");
        window.open("https://lore.kernel.org/all/?q=" + q, "_blank", "noopener");
      });

      lkmlSave.addEventListener("click", async function () {
        const url = lkmlUrl.value.trim();
        if (!url) return;
        const previewRes = await fetch("/api/evidence/lkml_preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: url }),
        });
        const preview = await previewRes.json();
        await saveLkml(findingId, preview);
        lkmlUrl.value = "";
        lkmlForm.style.display = "none";
        loadEvidence();
      });
    });
  }

  async function loadEvidence() {
    const res = await fetch("/api/evidence/list/" + encodeURIComponent(sessionId));
    const data = await res.json();
    const records = data.records || [];

    document.querySelectorAll(".evidence-box").forEach(function (box) {
      const findingId = box.dataset.finding;
      const itemBox = box.querySelector(".ev-items");
      itemBox.innerHTML = "";

      records.filter(function (r) { return r.finding_id === findingId; }).forEach(function (record) {
        const wrap = document.createElement("div");
        if (record.evidence_type === "screenshot") {
          wrap.innerHTML = '<img class="thumb" src="data:image/png;base64,' + record.content + '">';
        } else {
          const meta = record.metadata || {};
          wrap.className = "lkml-chip";
          wrap.innerHTML = '🔗 <a href="' + (record.content || "#") + '" target="_blank">'
            + escapeHtml(meta.title || record.content || "") + "</a> "
            + escapeHtml((meta.author || "Unknown") + " · " + (meta.date || ""));
        }
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "btn-secondary";
        rm.textContent = "✕";
        rm.style.padding = "2px 8px";
        rm.addEventListener("click", async function () {
          await fetch("/api/evidence/" + record.id, { method: "DELETE" });
          loadEvidence();
        });
        wrap.appendChild(rm);
        itemBox.appendChild(wrap);
      });
    });
  }

  async function fetchMaintainersFromPatch(content) {
    const matches = (content || "").match(/\+\+\+\s+b\/([^\n]+)/g) || [];
    const paths = matches.map(function (line) {
      return line.replace("+++ b/", "").trim();
    }).filter(Boolean);
    if (paths.length === 0) return [];
    try {
      const res = await fetch("/api/patchwise/get_maintainers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_paths: paths }),
      });
      const data = await res.json();
      return data.maintainers || [];
    } catch (err) {
      return [];
    }
  }

  function fmtMs(ms) {
    const value = Number(ms || 0);
    if (value < 1000) return value + "ms";
    return (value / 1000).toFixed(2) + "s";
  }

  function stepClass(status) {
    const lower = String(status || "").toLowerCase();
    if (lower === "pass") return "pass";
    if (lower === "fail") return "fail";
    return "skip";
  }

  function renderPipelineResult(data) {
    const summaryBox = document.getElementById("pipelineSummary");
    const stepsBox = document.getElementById("pipelineSteps");
    const summary = data.summary || {};
    const steps = data.steps || [];
    summaryBox.style.display = "block";
    summaryBox.innerHTML = [
      "<strong>Pipeline</strong>",
      '<div class="small-muted">Trace: ' + escapeHtml(data.trace_id || "") + "</div>",
      '<div class="small-muted">Overall: ' + escapeHtml(summary.overall || "UNKNOWN") +
      " · Passed: " + (summary.passed || 0) +
      " · Failed: " + (summary.failed || 0) +
      " · Skipped: " + (summary.skipped || 0) +
      " · Duration: " + fmtMs(data.duration_ms || 0) + "</div>",
    ].join("");

    stepsBox.innerHTML = "";
    if (!steps.length) {
      stepsBox.innerHTML = '<div class="small-muted">No pipeline steps returned.</div>';
      return;
    }

    steps.forEach(function (step) {
      const card = document.createElement("div");
      card.className = "pipeline-step " + stepClass(step.status);
      const output = (step.output_preview || "").trim();
      card.innerHTML = [
        '<div class="pipeline-topline"><strong>' + escapeHtml(step.name || step.id || "Step") + "</strong>" +
        '<span>' + escapeHtml(step.status || "UNKNOWN") + " · exit=" + escapeHtml(String(step.exit_code)) +
        " · " + fmtMs(step.duration_ms || 0) + "</span></div>",
        '<div class="small-muted">Warnings: ' + (step.warnings_count || 0) + " · Errors: " + (step.errors_count || 0) + "</div>",
        output ? '<pre class="pipeline-output">' + escapeHtml(output.slice(0, 1400)) + "</pre>" : "",
      ].join("");
      stepsBox.appendChild(card);
    });
  }

  async function loadTraceList(optionalTraceId) {
    const traceList = document.getElementById("traceList");
    const traceSummary = document.getElementById("traceSummary");
    const params = new URLSearchParams();
    params.set("session_id", sessionId);
    params.set("limit", "40");
    if (optionalTraceId) params.set("trace_id", optionalTraceId);
    try {
      const res = await fetch("/api/patchwise/traces?" + params.toString());
      const data = await res.json();
      const rows = data.rows || [];
      if (!rows.length) {
        traceSummary.textContent = "No traces yet for this session.";
        traceList.innerHTML = "";
        return;
      }
      traceSummary.textContent = "Showing " + rows.length + " latest trace entries.";
      traceList.innerHTML = "";
      rows.forEach(function (row) {
        const item = document.createElement("div");
        item.className = "trace-item";
        const err = row.error_message ? (" · " + row.error_message) : "";
        item.innerHTML = [
          "<div><strong>" + escapeHtml(row.stage || "") + "</strong></div>",
          "<div>" + escapeHtml((row.status || "").toUpperCase()) + " · exit=" + escapeHtml(String(row.exit_code)) +
            " · " + fmtMs(row.duration_ms || 0) + "</div>",
          "<div>trace=" + escapeHtml(row.trace_id || "") + " · in=" + (row.token_input || 0) +
            " · out=" + (row.token_output || 0) + escapeHtml(err) + "</div>",
        ].join("");
        traceList.appendChild(item);
      });
    } catch (err) {
      traceSummary.textContent = "Trace load failed.";
    }
  }

  function renderAutofixPreview(data) {
    const summary = document.getElementById("autofixSummary");
    const fixesBox = document.getElementById("autofixFixes");
    const diffBox = document.getElementById("autofixDiff");
    const available = data.available_fixes || [];

    if (!autofixSelectedFixIds.length) {
      autofixSelectedFixIds = (data.selected_fix_ids || []).slice();
    }

    summary.textContent = data.has_changes
      ? ("Changes ready · " + (data.changed_line_count || 0) + " lines impacted")
      : "No autofix changes suggested for current selection.";

    fixesBox.innerHTML = "";
    available.forEach(function (fix) {
      const row = document.createElement("label");
      row.innerHTML = '<input type="checkbox" value="' + escapeHtml(fix.id || "") + '"' +
        (autofixSelectedFixIds.indexOf(fix.id) >= 0 ? " checked" : "") + "> " +
        escapeHtml(fix.title || fix.id || "Fix") +
        '<span class="small-muted"> — ' + escapeHtml(fix.description || "") + "</span>";
      const cb = row.querySelector("input");
      cb.addEventListener("change", function () {
        if (cb.checked && autofixSelectedFixIds.indexOf(fix.id) === -1) autofixSelectedFixIds.push(fix.id);
        if (!cb.checked) autofixSelectedFixIds = autofixSelectedFixIds.filter(function (id) { return id !== fix.id; });
        previewAutofix();
      });
      fixesBox.appendChild(row);
    });

    if (data.applied_fixes && data.applied_fixes.length) {
      const applied = document.createElement("div");
      applied.className = "small-muted";
      applied.textContent = "Applied in preview: " + data.applied_fixes.map(function (f) { return f.title || f.id; }).join(", ");
      fixesBox.appendChild(applied);
    }

    diffBox.textContent = (data.diff || "").trim() || "No diff generated.";
  }

  async function runPipeline() {
    const patchContent = document.getElementById("patchContent").value;
    if (!patchContent.trim()) return;

    reviewStarted = true;
    exportedReport = false;
    setStepState(true, true, false, false);
    setReviewRunning(true);
    startReviewProgress();
    const summaryBox = document.getElementById("pipelineSummary");
    summaryBox.style.display = "block";
    summaryBox.textContent = "Running deterministic pipeline...";

    try {
      const res = await fetch("/api/patchwise/pipeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          patch_content: patchContent,
          target_path: activeTargetPath,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        summaryBox.textContent = data.error || "Pipeline failed";
        stopReviewProgress(false);
        return;
      }

      lastTraceId = data.trace_id || "";
      renderPipelineResult(data);
      hasResults = true;
      setStepState(true, true, true, exportedReport);
      setActionState();
      const findings = data.findings || [];
      document.getElementById("checkpatchOutput").textContent =
        "Pipeline findings: " + findings.length + (findings.length ? ("\n" + findings.map(function (f) { return "[" + f.severity + "] " + f.message; }).join("\n")) : "");
      await loadTraceList(lastTraceId);
      stopReviewProgress(true);
    } catch (err) {
      summaryBox.textContent = "Pipeline failed";
      stopReviewProgress(false);
    } finally {
      setReviewRunning(false);
    }
  }

  async function previewAutofix() {
    const patchContent = document.getElementById("patchContent").value;
    if (!patchContent.trim()) return;
    const summary = document.getElementById("autofixSummary");
    summary.textContent = "Generating autofix preview...";
    try {
      const res = await fetch("/api/patchwise/autofix/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          patch_content: patchContent,
          target_path: activeTargetPath,
          accepted_fix_ids: autofixSelectedFixIds.length ? autofixSelectedFixIds : undefined,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        summary.textContent = data.error || "Autofix preview failed";
        return;
      }
      lastTraceId = data.trace_id || "";
      lastAutofixPreview = data;
      renderAutofixPreview(data);
      setActionState();
      await loadTraceList(lastTraceId);
    } catch (err) {
      summary.textContent = "Autofix preview failed";
    }
  }

  async function applyAutofix() {
    const patchContent = document.getElementById("patchContent").value;
    if (!patchContent.trim()) return;
    const summary = document.getElementById("autofixSummary");
    summary.textContent = "Applying selected fixes...";
    try {
      const res = await fetch("/api/patchwise/autofix/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          patch_content: patchContent,
          target_path: activeTargetPath,
          accepted_fix_ids: autofixSelectedFixIds.length ? autofixSelectedFixIds : undefined,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        summary.textContent = data.error || "Autofix apply failed";
        return;
      }
      lastTraceId = data.trace_id || "";
      lastAutofixPreview = data;
      if (data.fixed_content) {
        document.getElementById("patchContent").value = data.fixed_content;
        if (files[0]) files[0].content = data.fixed_content;
      }
      lastAutofixBackupPath = data.backup_path || "";
      summary.textContent = data.persisted
        ? "Fixes applied and saved to file. Backup recorded."
        : "Fixes applied to editor content (not persisted to disk).";
      renderAutofixPreview(data);
      setActionState();
      await loadTraceList(lastTraceId);
    } catch (err) {
      summary.textContent = "Autofix apply failed";
    }
  }

  async function rollbackAutofix() {
    const targetPath = activeTargetPath;
    const summary = document.getElementById("autofixSummary");
    if (!targetPath) {
      summary.textContent = "Rollback requires a file loaded from an allowed path.";
      return;
    }
    summary.textContent = "Rolling back latest applied autofix...";
    try {
      const res = await fetch("/api/patchwise/autofix/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          target_path: targetPath,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        summary.textContent = data.error || "Rollback failed";
        return;
      }
      lastTraceId = data.trace_id || "";
      document.getElementById("patchContent").value = data.content || "";
      if (files[0]) files[0].content = data.content || "";
      summary.textContent = "Rollback complete. Restored from backup.";
      lastAutofixPreview = null;
      setActionState();
      await loadTraceList(lastTraceId);
    } catch (err) {
      summary.textContent = "Rollback failed";
    }
  }

  async function runReview() {
    const patchContent = document.getElementById("patchContent").value;
    const contextUrl = document.getElementById("contextUrl").value.trim();
    const model = document.getElementById("patchModel").value;
    if (!patchContent.trim()) return;

    reviewStarted = true;
    exportedReport = false;
    setStepState(true, true, false, false);
    setReviewRunning(true);
    startReviewProgress();

    try {
      const res = await fetch("/api/patchwise/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          patch_content: patchContent,
          context_url: contextUrl,
          model: model,
        }),
      });
      const data = await res.json();
      if (!data.ok && !data.findings) {
        document.getElementById("reviewSummary").textContent = data.error || "Review failed";
        stopReviewProgress(false);
        return;
      }

      lastTraceId = data.trace_id || "";
      hasResults = true;
      setStepState(true, true, true, false);
      setActionState();
      renderFindings(data.findings || [], data.summary || {});
      const maintainers = Array.isArray(data.maintainers) && data.maintainers.length
        ? data.maintainers
        : await fetchMaintainersFromPatch(patchContent);
      renderMaintainers(maintainers);
      loadSessionList();
      await loadTraceList(lastTraceId);
      stopReviewProgress(true);
    } catch (err) {
      document.getElementById("reviewSummary").textContent = "Review failed";
      stopReviewProgress(false);
    } finally {
      setReviewRunning(false);
    }
  }

  async function runCheckpatch() {
    const patchContent = document.getElementById("patchContent").value;
    if (!patchContent.trim()) return;
    const res = await fetch("/api/patchwise/run_checkpatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, patch_content: patchContent }),
    });
    const data = await res.json();
    const out = data.output || "";
    const summary = "checkpatch warnings: " + (data.warnings_count || 0) + ", errors: " + (data.errors_count || 0);
    document.getElementById("checkpatchOutput").textContent = summary + (out ? "\n" + out.slice(0, 4000) : "");
  }

  async function exportReport() {
    const res = await fetch("/api/patchwise/export/" + encodeURIComponent(sessionId));
    if (!res.ok) return;
    const blob = await res.blob();
    const link = document.createElement("a");
    const ts = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
    link.href = URL.createObjectURL(blob);
    link.download = "patch_review_" + ts + ".html";
    document.body.appendChild(link);
    link.click();
    URL.revokeObjectURL(link.href);
    link.remove();
    exportedReport = true;
    setStepState(true, true, true, true);
    loadSessionList();
  }

  async function askReviewer() {
    const question = document.getElementById("pwAskInput").value.trim();
    if (!question) return;
    const patch = document.getElementById("patchContent").value;
    const filename = files[0] ? files[0].name : "pasted_patch.diff";
    addAssistantBubble("user", question);
    document.getElementById("pwAskInput").value = "";

    const contextualMessage = [
      "Patch filename: " + filename,
      "Patch content:",
      patch,
      "",
      "Question:",
      question,
    ].join("\n");

    const res = await fetch("/api/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId + "-chat",
        page: "patchwise",
        message: contextualMessage,
      }),
    });
    const data = await res.json();
    addAssistantBubble("ai", data.response || data.content || data.message || "No response");
  }

  function init() {
    const input = document.getElementById("patchPathInput");
    const model = document.getElementById("patchModel");
    const zone = document.getElementById("patchDropZone");
    input.value = localStorage.getItem(pathKey) || "/app/kernel";
    model.value = localStorage.getItem(modelKey) || model.value;

    document.getElementById("patchApplyBtn").addEventListener("click", refreshPath);
    model.addEventListener("change", function () {
      localStorage.setItem(modelKey, model.value);
    });

    document.getElementById("patchBrowseBtn").addEventListener("click", function () {
      if (!window.AKDWFolderBrowser) return;
      window.AKDWFolderBrowser.open({
        startPath: input.value.trim() || "/app/kernel",
        onSelect: function (selectedPath) {
          input.value = selectedPath;
          refreshPath();
        },
      });
    });

    document.getElementById("browse-btn").addEventListener("click", function () {
      document.getElementById("pw-file-input").click();
    });
    document.getElementById("pw-file-input").addEventListener("change", function (e) {
      handleFilesDropped(Array.from(e.target.files || []));
      e.target.value = "";
    });

    zone.addEventListener("dragover", function (e) {
      e.preventDefault();
      zone.classList.add("dragging");
    });
    zone.addEventListener("dragleave", function (e) {
      e.preventDefault();
      zone.classList.remove("dragging");
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("dragging");
      handleFilesDropped(Array.from((e.dataTransfer && e.dataTransfer.files) || []));
    });

    document.getElementById("runReviewBtn").addEventListener("click", runReview);
    document.getElementById("runPipelineBtn").addEventListener("click", runPipeline);
    document.getElementById("runCheckpatchBtn").addEventListener("click", runCheckpatch);
    document.getElementById("previewAutofixBtn").addEventListener("click", previewAutofix);
    document.getElementById("applyAutofixBtn").addEventListener("click", applyAutofix);
    document.getElementById("rollbackAutofixBtn").addEventListener("click", rollbackAutofix);
    document.getElementById("exportBtn").addEventListener("click", exportReport);
    document.getElementById("placeholderRunBtn").addEventListener("click", runReview);
    document.getElementById("hintRunReviewBtn").addEventListener("click", runReview);
    document.getElementById("hintRunPipelineBtn").addEventListener("click", runPipeline);
    document.getElementById("hintCheckpatchBtn").addEventListener("click", runCheckpatch);
    document.getElementById("pwAskBtn").addEventListener("click", askReviewer);
    document.getElementById("patchContent").addEventListener("input", function () {
      if (this.value.trim()) {
        setStepState(true, reviewStarted, hasResults, exportedReport);
      }
      lastAutofixPreview = null;
      setActionState();
    });

    addAssistantBubble("ai", "Load a patch, then ask any question about style, risks, or upstream readiness.");
    renderFiles();
    refreshPath();
    setActionState();
    setStepState(false, false, false, false);
    loadSessionList();
    loadTraceList();
  }

  return { init: init };
})();

window.addEventListener("DOMContentLoaded", function () {
  if (window.AKDWPatchwise) window.AKDWPatchwise.init();
});
