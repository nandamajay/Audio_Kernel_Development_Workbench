class ResizablePanel {
  constructor(containerSelector, handles) {
    this.container = document.querySelector(containerSelector);
    this.handles = handles || [];
    this.storageKey = "akdw-layout-" + window.location.pathname.replace(/\//g, "_");
    if (!this.container) return;
    this.init(this.handles);
    this.loadLayout();
  }

  init(handles) {
    handles.forEach((h) => {
      const handle = document.getElementById(h.handleId);
      if (!handle) return;
      let dragging = false;
      let startX = 0;
      let startW = 0;

      handle.addEventListener("mousedown", (e) => {
        dragging = true;
        startX = e.clientX;
        const left = document.getElementById(h.leftPanelId);
        if (!left) return;
        startW = left.getBoundingClientRect().width;
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
      });

      document.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        const left = document.getElementById(h.leftPanelId);
        const right = document.getElementById(h.rightPanelId);
        if (!left || !right || !this.container) return;
        const dx = e.clientX - startX;
        const containerW = this.container.getBoundingClientRect().width;
        const newW = Math.max(150, Math.min(startW + dx, containerW - 150));
        left.style.width = newW + "px";
        left.style.flex = "none";
        this.saveLayout();
      });

      document.addEventListener("mouseup", () => {
        dragging = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      });

      handle.addEventListener("dblclick", () => {
        const left = document.getElementById(h.leftPanelId);
        if (!left) return;
        left.style.width = "";
        left.style.flex = "";
        this.clearLayout();
      });
    });
  }

  saveLayout() {
    if (!this.container) return;
    const panelWidths = {};
    this.handles.forEach((h) => {
      const left = document.getElementById(h.leftPanelId);
      if (left && left.style.width) {
        panelWidths[h.leftPanelId] = left.style.width;
      }
    });
    localStorage.setItem(this.storageKey, JSON.stringify(panelWidths));
  }

  loadLayout() {
    const raw = localStorage.getItem(this.storageKey);
    if (!raw) return;
    try {
      const data = JSON.parse(raw);
      Object.keys(data || {}).forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.width = data[id];
        el.style.flex = "none";
      });
    } catch (_) {
      // no-op
    }
  }

  clearLayout() {
    localStorage.removeItem(this.storageKey);
  }
}

window.ResizablePanel = ResizablePanel;
