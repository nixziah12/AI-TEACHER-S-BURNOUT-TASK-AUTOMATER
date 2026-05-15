function buildChart(canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;
    new Chart(canvas, config);
}

window.renderBarChart = function renderBarChart(canvasId, data, label) {
    buildChart(canvasId, {
        type: 'bar',
        data: {
            labels: data.map(item => item.keyword),
            datasets: [{
                label,
                data: data.map(item => item.score),
                backgroundColor: ['#6D4CFF', '#4D9FFF', '#F5A524', '#A06BFF', '#7B8794'],
                borderRadius: 12,
            }],
        },
        options: {
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, max: 100 } },
        },
    });
};

window.renderPieChart = function renderPieChart(canvasId, data) {
    buildChart(canvasId, {
        type: 'pie',
        data: {
            labels: Object.keys(data),
            datasets: [{
                data: Object.values(data),
                backgroundColor: ['#6D4CFF', '#4D9FFF', '#F5A524', '#EF4444'],
                borderWidth: 0,
            }],
        },
        options: {
            plugins: { legend: { position: 'bottom' } },
        },
    });
};

window.renderTopicChart = function renderTopicChart(canvasId, labels, values) {
    buildChart(canvasId, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data: values,
                backgroundColor: '#4D6BFE',
                borderRadius: 12,
            }],
        },
        options: {
            indexAxis: 'y',
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true, max: 100 } },
        },
    });
};

window.renderTrendChart = function renderTrendChart(canvasId, points) {
    buildChart(canvasId, {
        type: 'line',
        data: {
            labels: points.map(item => item.date),
            datasets: [{
                label: 'Average score',
                data: points.map(item => item.average),
                borderColor: '#5A35D6',
                backgroundColor: 'rgba(90, 53, 214, 0.16)',
                fill: true,
                tension: 0.35,
                pointRadius: 4,
            }],
        },
        options: {
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, max: 100 } },
        },
    });
};

