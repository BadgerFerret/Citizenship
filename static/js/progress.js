let radarChart = null;
let timelineChart = null;

async function loadProgress(studentId) {
  const res = await fetch('/api/progress/' + studentId);
  const data = await res.json();

  // Summary stats
  document.getElementById('stat-total').textContent = data.summary.total_attempts;
  document.getElementById('stat-avg').textContent = data.summary.overall_avg_pct + '%';
  document.getElementById('stat-streak').textContent = data.summary.streak_days + ' 🔥';

  // Radar chart — topic mastery
  const topicOrder = [
    'life_in_modern_britain',
    'rights_and_responsibilities',
    'government_and_democracy',
    'uk_and_wider_world',
    'active_citizenship',
  ];
  const radarLabels = topicOrder.map(t => data.by_topic[t] ? data.by_topic[t].label : t);
  const radarValues = topicOrder.map(t => data.by_topic[t] ? (data.by_topic[t].avg_pct ?? 0) : 0);

  if (radarChart) radarChart.destroy();
  const radarCtx = document.getElementById('radarChart').getContext('2d');
  radarChart = new Chart(radarCtx, {
    type: 'radar',
    data: {
      labels: radarLabels,
      datasets: [{
        label: data.student.name,
        data: radarValues,
        backgroundColor: 'rgba(79, 134, 198, 0.2)',
        borderColor: 'rgba(79, 134, 198, 1)',
        pointBackgroundColor: 'rgba(79, 134, 198, 1)',
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      scales: {
        r: {
          min: 0,
          max: 100,
          ticks: {
            stepSize: 25,
            callback: v => v + '%',
          },
          pointLabels: { font: { size: 12 } }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ctx.raw + '%' } }
      }
    }
  });

  // Timeline chart
  if (timelineChart) timelineChart.destroy();
  const tlCtx = document.getElementById('timelineChart').getContext('2d');
  const tlData = data.timeline.slice(-50);

  // Rolling 5-attempt average
  const rolling = tlData.map((_, i) => {
    const window = tlData.slice(Math.max(0, i - 4), i + 1);
    return Math.round(window.reduce((s, d) => s + d.pct, 0) / window.length);
  });

  timelineChart = new Chart(tlCtx, {
    type: 'line',
    data: {
      labels: tlData.map((d, i) => i + 1),
      datasets: [
        {
          label: 'Score per question',
          data: tlData.map(d => d.pct),
          borderColor: 'rgba(200,200,200,0.5)',
          backgroundColor: 'transparent',
          pointBackgroundColor: tlData.map(d => d.color),
          pointRadius: 5,
          borderWidth: 1,
          tension: 0.2,
        },
        {
          label: '5-question rolling avg',
          data: rolling,
          borderColor: '#4f86c6',
          backgroundColor: 'transparent',
          pointRadius: 0,
          borderWidth: 3,
          tension: 0.4,
        }
      ]
    },
    options: {
      responsive: true,
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: { callback: v => v + '%' }
        },
        x: {
          title: { display: true, text: 'Question number' }
        }
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: (ctx) => {
              if (ctx.datasetIndex === 0) {
                return tlData[ctx.dataIndex].pct + '% — ' + (tlData[ctx.dataIndex].topic || '');
              }
              return 'Avg: ' + ctx.raw + '%';
            }
          }
        }
      }
    }
  });

  // Topic bars
  const topicBarsEl = document.getElementById('topic-bars');
  topicBarsEl.innerHTML = '';
  const sorted = topicOrder
    .map(t => ({ key: t, ...data.by_topic[t] }))
    .sort((a, b) => (a.avg_pct ?? -1) - (b.avg_pct ?? -1));

  for (const topic of sorted) {
    const pct = topic.avg_pct;
    const bar = document.createElement('div');
    bar.className = 'topic-bar-row';
    bar.innerHTML = `
      <div class="topic-bar-label">
        <span>${topic.label}</span>
        <span class="topic-bar-count">${topic.attempts} attempt${topic.attempts !== 1 ? 's' : ''}</span>
      </div>
      <div class="topic-bar-track">
        <div class="topic-bar-fill" style="width:${pct ?? 0}%;background:${topic.color}"></div>
      </div>
      <span class="topic-bar-pct">${pct !== null ? pct + '%' : 'No data'}</span>
    `;
    topicBarsEl.appendChild(bar);
  }

  // Recent sessions table
  const tableWrap = document.getElementById('sessions-table-wrap');
  if (!data.recent_sessions.length) {
    tableWrap.innerHTML = '<p class="no-data">No completed sessions yet.</p>';
    return;
  }
  let html = '<table class="sessions-table"><thead><tr><th>Date</th><th>Topic</th><th>Questions</th><th>Score</th></tr></thead><tbody>';
  for (const s of data.recent_sessions) {
    const cls = s.pct >= 70 ? 'pct-good' : s.pct >= 40 ? 'pct-ok' : 'pct-low';
    html += `<tr>
      <td>${s.date}</td>
      <td>${s.topic}</td>
      <td>${s.questions}</td>
      <td><span class="pct-badge ${cls}">${s.pct}%</span></td>
    </tr>`;
  }
  html += '</tbody></table>';
  tableWrap.innerHTML = html;
}
