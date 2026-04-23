window.AKDWCharts = (function () {
  function renderPatchHealthChart(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') {
      return;
    }

    new Chart(canvas, {
      type: 'line',
      data: {
        labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
        datasets: [
          {
            label: 'Checkpatch Issues',
            data: [4, 3, 6, 2, 1],
            borderColor: '#1f6feb',
            backgroundColor: 'rgba(31, 111, 235, 0.2)',
            tension: 0.3,
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#e6edf3' } },
        },
        scales: {
          x: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } },
        },
      },
    });
  }

  return {
    renderPatchHealthChart: renderPatchHealthChart,
  };
})();
