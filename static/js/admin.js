async function loadBank() {
  const topic = document.getElementById('filter-topic').value;
  const showInactive = document.getElementById('filter-inactive').checked;
  const params = new URLSearchParams();
  if (topic) params.set('topic', topic);
  if (showInactive) params.set('active', 'false');

  const res = await fetch('/api/questions?' + params.toString());
  const data = await res.json();
  document.getElementById('bank-count').textContent = data.total + ' question' + (data.total !== 1 ? 's' : '');

  const wrap = document.getElementById('bank-table-wrap');
  if (!data.questions.length) {
    wrap.innerHTML = '<p class="no-data">No questions found.</p>';
    return;
  }

  let html = '<table class="bank-table"><thead><tr><th>Question</th><th>Topic</th><th>Marks</th><th>Type</th><th>Active</th></tr></thead><tbody>';
  for (const q of data.questions) {
    const short = q.question_text.length > 80 ? q.question_text.slice(0, 80) + '…' : q.question_text;
    const activeLabel = q.active !== false ? '✅' : '❌';
    html += `<tr class="bank-row" onclick="toggleExpand('${q.id}')">
      <td class="q-text-cell">
        <span class="q-short">${short}</span>
        <div class="q-expand" id="expand-${q.id}" style="display:none">
          <p>${q.question_text}</p>
          <h4>Mark scheme points:</h4>
          <ul>${(q.mark_scheme?.indicative_points || []).map(p => `<li>${p}</li>`).join('')}</ul>
          <p class="q-guidance"><em>${q.mark_scheme?.marking_guidance || ''}</em></p>
          <button class="btn btn-sm btn-outline" onclick="event.stopPropagation(); toggleActive('${q.id}', ${q.active !== false})">
            ${q.active !== false ? 'Deactivate' : 'Activate'}
          </button>
        </div>
      </td>
      <td>${q.topic?.replace(/_/g, ' ') || ''}</td>
      <td>${q.marks}</td>
      <td>${q.question_type || ''}</td>
      <td>${activeLabel}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

function toggleExpand(id) {
  const el = document.getElementById('expand-' + id);
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function toggleActive(id, currentlyActive) {
  await fetch('/api/questions/' + id + '/toggle', { method: 'POST' });
  loadBank();
}
