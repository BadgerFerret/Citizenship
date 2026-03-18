let pendingImport = null;

async function uploadPapers() {
  const label = document.getElementById('paper-label').value.trim();
  const code = document.getElementById('paper-code').value.trim();
  const paperFile = document.getElementById('paper-pdf').files[0];
  const msFile = document.getElementById('ms-pdf').files[0];

  if (!label || !code) { alert('Please enter the paper label and code.'); return; }
  if (!paperFile || !msFile) { alert('Please select both PDF files.'); return; }

  document.getElementById('upload-btn').disabled = true;
  document.getElementById('upload-status').style.display = 'flex';
  document.getElementById('import-result').innerHTML = '';

  const formData = new FormData();
  formData.append('paper', label);
  formData.append('code', code);
  formData.append('paper_pdf', paperFile);
  formData.append('ms_pdf', msFile);

  try {
    const res = await fetch('/api/upload-papers', { method: 'POST', body: formData });
    const data = await res.json();
    document.getElementById('upload-status').style.display = 'none';
    document.getElementById('upload-btn').disabled = false;

    if (data.error) {
      document.getElementById('import-result').innerHTML = `<div class="alert alert-error">Error: ${data.error}</div>`;
      return;
    }

    // Put extracted questions into the preview flow
    pendingImport = data.questions;
    document.getElementById('import-json').value = JSON.stringify(data.questions, null, 2);

    const preview = document.getElementById('import-preview');
    const previewInner = document.getElementById('import-preview-inner');
    preview.style.display = 'block';
    document.getElementById('confirm-import-btn').style.display = 'inline-block';

    let html = `<p><strong>${data.count} question(s) extracted</strong> from ${label}. Review below then click "Add to Question Bank".</p>`;
    html += `<table class="bank-table"><thead><tr><th>ID</th><th>Topic</th><th>Marks</th><th>Question</th></tr></thead><tbody>`;
    for (const q of data.questions) {
      html += `<tr><td>${q.id}</td><td>${(q.topic||'').replace(/_/g,' ')}</td><td>${q.marks}</td><td>${(q.question_text||'').slice(0,70)}…</td></tr>`;
    }
    html += '</tbody></table>';
    previewInner.innerHTML = html;

    // Scroll to preview
    preview.scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    document.getElementById('upload-status').style.display = 'none';
    document.getElementById('upload-btn').disabled = false;
    document.getElementById('import-result').innerHTML = `<div class="alert alert-error">Request failed: ${err.message}</div>`;
  }
}

function showTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).style.display = 'block';
  event.target.classList.add('active');
}

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

function validateImport() {
  const raw = document.getElementById('import-json').value.trim();
  let questions;
  try {
    const parsed = JSON.parse(raw);
    questions = Array.isArray(parsed) ? parsed : parsed.questions;
    if (!Array.isArray(questions)) throw new Error('Expected an array of questions');
  } catch (e) {
    alert('Invalid JSON: ' + e.message);
    return;
  }

  const required = ['id', 'topic', 'question_text', 'marks', 'mark_scheme'];
  const errors = [];
  questions.forEach((q, i) => {
    required.forEach(f => {
      if (!q[f]) errors.push(`Question ${i+1}: missing '${f}'`);
    });
    if (q.mark_scheme && !Array.isArray(q.mark_scheme.indicative_points)) {
      errors.push(`Question ${i+1}: mark_scheme.indicative_points must be an array`);
    }
  });

  if (errors.length) {
    alert('Validation errors:\n' + errors.join('\n'));
    return;
  }

  pendingImport = questions;
  const preview = document.getElementById('import-preview');
  const previewInner = document.getElementById('import-preview-inner');
  preview.style.display = 'block';
  document.getElementById('confirm-import-btn').style.display = 'inline-block';

  let html = `<p>${questions.length} question(s) ready to import:</p><table class="bank-table"><thead><tr><th>ID</th><th>Topic</th><th>Marks</th><th>Question</th></tr></thead><tbody>`;
  for (const q of questions) {
    html += `<tr><td>${q.id}</td><td>${q.topic}</td><td>${q.marks}</td><td>${q.question_text.slice(0, 60)}…</td></tr>`;
  }
  html += '</tbody></table>';
  previewInner.innerHTML = html;
}

async function confirmImport() {
  if (!pendingImport) return;
  const res = await fetch('/api/import', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ questions: pendingImport })
  });
  const data = await res.json();
  const resultEl = document.getElementById('import-result');
  resultEl.innerHTML = `<div class="alert alert-success">Added ${data.added} question(s). Skipped ${data.skipped} duplicate(s). Total in bank: ${data.total}.</div>`;
  document.getElementById('import-json').value = '';
  document.getElementById('import-preview').style.display = 'none';
  document.getElementById('confirm-import-btn').style.display = 'none';
  pendingImport = null;
  // Switch to bank tab to show new questions
  showTab('bank');
  loadBank();
}
