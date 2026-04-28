const UpstreamCharts = {
  charts: {},
  series: [],
  filteredSeries: [],
  sortField: "vN_posted",
  sortDir: "desc",

  async fetchAndRender(mode = "live") {
    this.setLoading("Fetching " + mode + " data...");
    try {
      const res = await fetch(`/api/upstream/stats?mode=${encodeURIComponent(mode)}`, { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      this.renderAll(data);
      this.setLoading("Loaded " + (data.series || []).length + " series.");
    } catch (err) {
      this.setLoading("Fetch failed: " + err.message);
      this.renderAll({ series: [], summary: this.emptySummary(), fetch_mode: mode, last_updated: null, author: "" });
    }
  },

  async uploadAndRender(file) {
    this.setLoading("Uploading and parsing offline mbox...");
    try {
      const fd = new FormData();
      fd.append("mbox_file", file);
      const res = await fetch("/api/upstream/upload-mbox", {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error("Upload failed: " + (text || res.status));
      }
      const data = await res.json();
      this.renderAll(data);
      this.setLoading("Offline data loaded.");
    } catch (err) {
      this.setLoading("Upload failed: " + err.message);
    }
  },

  renderAll(data) {
    const summary = data.summary || this.emptySummary();
    this.series = Array.isArray(data.series) ? data.series.slice() : [];
    this.filteredSeries = this.series.slice();

    this.updateKPIs(summary);
    this.renderStatusDonut(summary);
    this.renderMonthlyTimeline(summary.monthly_submissions || []);
    this.renderVersionDistribution(summary.version_distribution || []);
    this.renderDaysHistogram(summary.days_to_merge_histogram || [], summary.avg_days_to_merge || 0);
    this.renderTopSeriesLines(this.series);
    this.renderTable(this.series);
    this.updateFetchModeBadge(data.fetch_mode || "live", data.last_updated || null, data.author || "");
  },

  destroyChart(key) {
    if (this.charts[key]) {
      this.charts[key].destroy();
      delete this.charts[key];
    }
  },

  updateKPIs(summary) {
    this.setText("kpi-total-series", summary.total_series || 0);
    this.setText("kpi-merged", summary.merged || 0);
    this.setText("kpi-reviewed", summary.reviewed_not_merged || 0);
    this.setText("kpi-pending", summary.pending || 0);
    this.setText("kpi-avg-merge", summary.avg_days_to_merge || 0);
    this.setText("kpi-avg-apply", summary.avg_days_to_apply || 0);
    this.setText("kpi-total-patches", summary.total_patches || 0);
  },

  renderStatusDonut(summary) {
    const canvas = document.getElementById("chart-status-donut");
    if (!canvas || typeof Chart === "undefined") return;
    this.destroyChart("status");

    const merged = Number(summary.merged || 0);
    const reviewed = Number(summary.reviewed_not_merged || 0);
    const pending = Number(summary.pending || 0);
    const total = Number(summary.total_series || 0);

    this.charts.status = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: ["MERGED", "REVIEWED_NOT_MERGED", "PENDING"],
        datasets: [{
          data: [merged, reviewed, pending],
          backgroundColor: ["#22c55e", "#f59e0b", "#6366f1"],
          borderColor: ["#14532d", "#7c2d12", "#312e81"],
          borderWidth: 1,
        }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#c9d1d9" } },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const v = Number(ctx.raw || 0);
                const pct = total ? ((v * 100) / total).toFixed(1) : "0.0";
                return `${ctx.label}: ${v} (${pct}%)`;
              },
            },
          },
        },
      },
      plugins: [{
        id: "centerLabel",
        afterDraw(chart) {
          const c = chart.ctx;
          const meta = chart.getDatasetMeta(0);
          if (!meta || !meta.data || !meta.data.length) return;
          const x = meta.data[0].x;
          const y = meta.data[0].y;
          c.save();
          c.fillStyle = "#e6edf3";
          c.font = "600 16px sans-serif";
          c.textAlign = "center";
          c.fillText(String(total), x, y);
          c.restore();
        },
      }],
    });
  },

  renderMonthlyTimeline(monthly) {
    const canvas = document.getElementById("chart-monthly-timeline");
    if (!canvas || typeof Chart === "undefined") return;
    this.destroyChart("monthly");

    const rows = Array.isArray(monthly) ? monthly : [];
    const labels = rows.map((r) => r.month || "-");
    const submitted = rows.map((r) => Number(r.count || 0));
    const merged = rows.map((r) => Number(r.merged || 0));

    this.charts.monthly = new Chart(canvas, {
      data: {
        labels,
        datasets: [
          { type: "bar", label: "Submitted", data: submitted, backgroundColor: "#6366f1", borderColor: "#4f46e5", borderWidth: 1 },
          { type: "line", label: "Merged", data: merged, borderColor: "#22c55e", backgroundColor: "#22c55e", tension: 0.2, yAxisID: "y" },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
          y: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
        },
        plugins: { legend: { labels: { color: "#c9d1d9" } } },
      },
    });
  },

  renderVersionDistribution(vdist) {
    const canvas = document.getElementById("chart-version-distribution");
    if (!canvas || typeof Chart === "undefined") return;
    this.destroyChart("versions");

    const map = new Map();
    (vdist || []).forEach((row) => map.set(Number(row.revisions), Number(row.count || 0)));
    const labels = ["1 revision", "2 revisions", "3 revisions", "4+ revisions"];
    const values = [map.get(1) || 0, map.get(2) || 0, map.get(3) || 0, map.get(4) || 0];

    this.charts.versions = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: ["#22c55e", "#eab308", "#f97316", "#ef4444"] }],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
          y: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
        },
        plugins: { legend: { display: false } },
      },
    });
  },

  renderDaysHistogram(hist, avgDays) {
    const canvas = document.getElementById("chart-days-histogram");
    if (!canvas || typeof Chart === "undefined") return;
    this.destroyChart("days");

    const labels = (hist || []).map((r) => r.bucket || "-");
    const values = (hist || []).map((r) => Number(r.count || 0));

    this.charts.days = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: "#0ea5e9", borderColor: "#0369a1", borderWidth: 1 }],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
          y: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              footer: () => `Avg: ${avgDays} days`,
            },
          },
        },
      },
    });
  },

  renderTopSeriesLines(series) {
    const canvas = document.getElementById("chart-top-series-lines");
    if (!canvas || typeof Chart === "undefined") return;
    this.destroyChart("toplines");

    const rows = (series || [])
      .slice()
      .sort((a, b) => Number(b.added_lines || 0) - Number(a.added_lines || 0))
      .slice(0, 10);

    const labels = rows.map((r) => {
      const t = String(r.title || "");
      return t.length > 40 ? t.slice(0, 40) + "..." : t;
    });
    const values = rows.map((r) => Number(r.added_lines || 0));

    this.charts.toplines = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: "#8b5cf6", borderColor: "#5b21b6", borderWidth: 1 }],
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
          y: { ticks: { color: "#c9d1d9" }, grid: { color: "rgba(255,255,255,0.08)" } },
        },
        plugins: { legend: { display: false } },
      },
    });
  },

  renderTable(series) {
    this.filteredSeries = (series || []).slice();
    this.applySort();
    this.paintTable();
    this.bindSortHeaders();
  },

  filterTable(query, status) {
    const q = String(query || "").toLowerCase().trim();
    const st = String(status || "").trim();
    this.filteredSeries = this.series.filter((row) => {
      const title = String(row.title || "").toLowerCase();
      const matchesQ = !q || title.includes(q);
      const matchesS = !st || String(row.status || "") === st;
      return matchesQ && matchesS;
    });
    this.applySort();
    this.paintTable();
  },

  applySort() {
    const key = this.sortField;
    const dir = this.sortDir === "asc" ? 1 : -1;
    this.filteredSeries.sort((a, b) => {
      const av = a[key] == null ? "" : a[key];
      const bv = b[key] == null ? "" : b[key];
      const na = Number(av);
      const nb = Number(bv);
      if (!Number.isNaN(na) && !Number.isNaN(nb) && String(av) !== "" && String(bv) !== "") {
        return (na - nb) * dir;
      }
      return String(av).localeCompare(String(bv)) * dir;
    });
  },

  paintTable() {
    const tbody = document.getElementById("upstream-table-body");
    if (!tbody) return;
    if (!this.filteredSeries.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="no-data">No data yet.</td></tr>';
      return;
    }

    tbody.innerHTML = this.filteredSeries.map((row, idx) => {
      const status = String(row.status || "PENDING");
      const badgeClass = status === "MERGED" ? "status-merged" : (status === "REVIEWED_NOT_MERGED" ? "status-reviewed" : "status-pending");
      const title = String(row.title || "");
      const safeTitle = this.escapeHtml(title);
      const lore = this.escapeHtml(String(row.lore_url || "#"));
      const reviewers = Array.isArray(row.reviewers) ? row.reviewers.join(", ") : "";
      const commits = Array.isArray(row.commit_shas) ? row.commit_shas.slice(0, 3).join(", ") : "";
      return [
        "<tr>",
        `<td>${idx + 1}</td>`,
        `<td><a href="${lore}" target="_blank" rel="noopener noreferrer" class="truncate-title" title="${safeTitle}">${safeTitle}</a></td>`,
        `<td><span class="status-badge ${badgeClass}">${status}</span></td>`,
        `<td>${this.escapeHtml((row.versions || []).join(" → ") || "v1")}</td>`,
        `<td>${this.escapeHtml(row.v1_posted || "-")}</td>`,
        `<td>${this.escapeHtml(row.vN_posted || "-")}</td>`,
        `<td>${row.days_to_merge == null ? "-" : row.days_to_merge}</td>`,
        `<td>${this.escapeHtml(reviewers || "-")}</td>`,
        `<td>${Number(row.added_lines || 0)}</td>`,
        `<td>${this.escapeHtml(commits || "-")}</td>`,
        "</tr>",
      ].join("");
    }).join("");
  },

  bindSortHeaders() {
    document.querySelectorAll("#upstream-patch-table th[data-sort]").forEach((th) => {
      if (th.dataset.bound === "1") return;
      th.dataset.bound = "1";
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (!key) return;
        if (this.sortField === key) {
          this.sortDir = this.sortDir === "asc" ? "desc" : "asc";
        } else {
          this.sortField = key;
          this.sortDir = "asc";
        }
        this.applySort();
        this.paintTable();
      });
    });
  },

  updateFetchModeBadge(fetchMode, lastUpdated, author) {
    const badge = document.getElementById("fetch-mode-badge");
    const last = document.getElementById("last-updated");
    const auth = document.getElementById("author-email");

    if (badge) {
      badge.textContent = fetchMode === "offline" ? "📦 Offline" : "🟢 Live";
    }
    if (last) {
      last.textContent = lastUpdated ? new Date(lastUpdated).toLocaleString() : "-";
    }
    if (auth && author) {
      auth.textContent = author;
    }
  },

  setLoading(text) {
    this.setText("loading-indicator", text || "Idle");
  },

  setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
  },

  escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  },

  emptySummary() {
    return {
      total_series: 0,
      merged: 0,
      reviewed_not_merged: 0,
      pending: 0,
      total_patches: 0,
      total_lines_added: 0,
      avg_days_to_merge: 0,
      avg_days_to_apply: 0,
      avg_maintainer_delay: 0,
      breakdown: {
        MERGED: { series: 0, patches: 0, lines: 0 },
        REVIEWED_NOT_MERGED: { series: 0, patches: 0, lines: 0 },
        PENDING: { series: 0, patches: 0, lines: 0 },
      },
      monthly_submissions: [],
      version_distribution: [],
      days_to_merge_histogram: [],
    };
  },
};

window.UpstreamCharts = UpstreamCharts;

document.addEventListener("DOMContentLoaded", () => {
  UpstreamCharts.fetchAndRender("live");

  const liveBtn = document.getElementById("btn-live-fetch");
  if (liveBtn) {
    liveBtn.addEventListener("click", () => UpstreamCharts.fetchAndRender("live"));
  }

  const refreshBtn = document.getElementById("btn-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => UpstreamCharts.fetchAndRender("live"));
  }

  const uploadBtn = document.getElementById("btn-upload-mbox");
  if (uploadBtn) {
    uploadBtn.addEventListener("click", () => {
      const modal = document.getElementById("mbox-upload-modal");
      if (modal) modal.classList.add("open");
    });
  }

  const uploadConfirm = document.getElementById("btn-upload-confirm");
  if (uploadConfirm) {
    uploadConfirm.addEventListener("click", () => {
      const fileInput = document.getElementById("mbox-file-input");
      const modal = document.getElementById("mbox-upload-modal");
      if (!fileInput || !fileInput.files || !fileInput.files[0]) {
        UpstreamCharts.setLoading("Choose a .mbox or .mbox.gz file first.");
        return;
      }
      if (modal) modal.classList.remove("open");
      UpstreamCharts.uploadAndRender(fileInput.files[0]);
    });
  }

  const filterTitle = document.getElementById("filter-title");
  const filterStatus = document.getElementById("filter-status");
  if (filterTitle) {
    filterTitle.addEventListener("input", (e) => {
      const status = filterStatus ? filterStatus.value : "";
      UpstreamCharts.filterTable(e.target.value, status);
    });
  }
  if (filterStatus) {
    filterStatus.addEventListener("change", (e) => {
      const query = filterTitle ? filterTitle.value : "";
      UpstreamCharts.filterTable(query, e.target.value);
    });
  }
});
