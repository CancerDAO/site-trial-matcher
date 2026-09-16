const I18N = {
  zh: {
    title:'中国临床试验批量预筛', datasetLoading:'正在读取试验库', inputTitle:'批量患者资料',
    inputHint:'每行一个 JSON 对象。仅使用临床字段，不要填写姓名、电话、证件号或详细地址。',
    loadSample:'载入示例', mode:'模式', deterministic:'本地确定性预筛', full:'完整排除核查', start:'开始匹配',
    currentStage:'当前阶段', cancel:'退出匹配', results:'预筛结果',
    resultNotice:'“潜在试验”仅表示没有充分排除证据，仍需研究中心确认。', newRun:'新建任务',
    patient:'患者编号', potential:'潜在试验', excluded:'已排除', partner:'合作方潜在试验', view:'查看试验',
    registry:'注册号', trial:'试验', organization:'机构', status:'状态', noTrials:'没有潜在试验',
    cancelTitle:'确认退出匹配？', cancelBody:'任务将停止，尚未完成的中间数据会被清理。',
    continue:'继续匹配', confirmCancel:'确认退出', invalidJson:'患者 JSONL 格式不正确', empty:'请至少输入一名患者',
    failed:'任务暂时失败，请稍后重试。', stages:{prepare:'准备阶段',execute:'执行比对',generate:'生成结果',deliver:'保存结果',complete:'已完成'}
  },
  en: {
    title:'China Clinical Trial Batch Prescreen', datasetLoading:'Loading trial dataset', inputTitle:'Batch patient records',
    inputHint:'One JSON object per line. Use clinical facts only; do not enter names, phone numbers, IDs, or detailed addresses.',
    loadSample:'Load sample', mode:'Mode', deterministic:'Local deterministic prescreen', full:'Full exclusion review', start:'Start matching',
    currentStage:'Current stage', cancel:'Stop matching', results:'Prescreen results',
    resultNotice:'A potential trial only means there is no sufficient exclusion evidence. The study site must confirm eligibility.', newRun:'New run',
    patient:'Patient ID', potential:'Potential', excluded:'Excluded', partner:'Partner potential', view:'View trials',
    registry:'Registry ID', trial:'Trial', organization:'Organization', status:'Status', noTrials:'No potential trials',
    cancelTitle:'Stop this match?', cancelBody:'The task will stop and unfinished intermediate data will be removed.',
    continue:'Continue', confirmCancel:'Stop task', invalidJson:'Invalid patient JSONL', empty:'Enter at least one patient',
    failed:'The task failed temporarily. Please try again.', stages:{prepare:'Preparing',execute:'Comparing',generate:'Generating results',deliver:'Saving results',complete:'Complete'}
  }
};

const sample = [
  {patient_id:'DEMO-NSCLC-01',country:'中国',age:58,sex:'男',cancer_type:'非小细胞肺癌',histology:'肺腺癌',disease_stage:'IV期',biomarkers:['EGFR L858R'],ecog:1,treatment_lines_completed:1,prior_therapies:['奥希替尼']},
  {patient_id:'DEMO-BREAST-01',country:'中国',age:45,sex:'女',cancer_type:'乳腺癌',disease_stage:'转移性',biomarkers:['HER2阳性'],ecog:1,treatment_lines_completed:1,prior_therapies:['曲妥珠单抗']}
];

const el = id => document.getElementById(id);
let language = localStorage.getItem('trial-demo-language') || 'zh';
let runId = localStorage.getItem('trial-demo-run');
let completedRunId = null;
let pollTimer = null;
let dataset = null;

function t(key) { return I18N[language][key] ?? key; }
function translate() {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
  el('stageText').textContent = t('stages')[el('stageText').dataset.stage || 'prepare'];
}

async function api(path, options={}) {
  const response = await fetch(path, {cache:'no-store', headers:{'Content-Type':'application/json',...(options.headers||{})}, ...options});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

function parsePatients() {
  const lines = el('patientInput').value.split(/\r?\n/).filter(line => line.trim());
  if (!lines.length) throw new Error(t('empty'));
  try { return lines.map(line => JSON.parse(line)); }
  catch { throw new Error(t('invalidJson')); }
}

function setRunning(state) {
  el('runBand').hidden = false;
  el('resultsBand').hidden = true;
  el('stageText').dataset.stage = state.stage || 'prepare';
  el('stageText').textContent = t('stages')[state.stage] || state.stage;
  el('statusText').textContent = state.message || '';
  el('progressBar').style.width = `${state.progress || 0}%`;
  el('cancelButton').disabled = state.status === 'stopping';
  el('cancelButton').hidden = ['completed','failed','cancelled'].includes(state.status);
}

async function poll() {
  if (!runId) return;
  try {
    const state = await api(`/api/runs/${runId}`);
    setRunning(state);
    if (state.status === 'completed') {
      clearInterval(pollTimer); pollTimer = null;
      const results = await api(`/api/runs/${runId}/results?limit=20`);
      renderResults(results.results);
    } else if (state.status === 'failed' || state.status === 'cancelled') {
      clearInterval(pollTimer); pollTimer = null;
      if (state.status === 'failed') showError(t('failed'));
      localStorage.removeItem('trial-demo-run'); runId = null;
    }
  } catch (error) { showError(error.message); }
}

function renderResults(rows) {
  completedRunId = runId;
  el('resultsBody').replaceChildren(...rows.map((row, index) => {
    const tr = document.createElement('tr');
    const summary = row.summary || {};
    [row.patient_id, summary.potential_count ?? 0, summary.excluded_count ?? 0, summary.partner_potential_count ?? 0].forEach(value => {
      const td = document.createElement('td'); td.textContent = value; tr.appendChild(td);
    });
    const actionCell = document.createElement('td');
    const button = document.createElement('button'); button.type = 'button'; button.className = 'table-action'; button.textContent = t('view');
    button.addEventListener('click', () => showPatientTrials(index)); actionCell.appendChild(button); tr.appendChild(actionCell);
    return tr;
  }));
  el('resultsBand').hidden = false;
  el('runBand').hidden = true;
  localStorage.removeItem('trial-demo-run'); runId = null;
}

async function showPatientTrials(index) {
  if (!completedRunId) return;
  try {
    const payload = await api(`/api/runs/${completedRunId}/results/${index}`);
    const result = payload.result;
    const trials = [...(result.potential_trials?.partner || []), ...(result.potential_trials?.non_partner || [])];
    el('patientDetailTitle').textContent = `${result.patient_id} · ${t('potential')} (${trials.length})`;
    const rows = trials.map(trial => {
      const tr = document.createElement('tr');
      const registry = document.createElement('td'); registry.textContent = trial.registry_id || trial.chictr_registration_number || '';
      const title = document.createElement('td');
      if (trial.source_url) { const link = document.createElement('a'); link.className = 'trial-link'; link.href = trial.source_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = trial.title || ''; title.appendChild(link); }
      else title.textContent = trial.title || '';
      const organization = document.createElement('td'); organization.textContent = trial.organization_name || '';
      const status = document.createElement('td'); status.textContent = trial.recruitment_status || '';
      tr.append(registry, title, organization, status); return tr;
    });
    if (!rows.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 4; td.textContent = t('noTrials'); tr.appendChild(td); rows.push(tr); }
    el('trialDetailBody').replaceChildren(...rows); el('patientDetail').hidden = false;
  } catch (error) { showError(error.message); }
}

function showError(message) { el('inputError').textContent = message; el('inputError').hidden = false; }

el('sampleButton').addEventListener('click', () => { el('patientInput').value = sample.map(item => JSON.stringify(item)).join('\n'); });
el('language').value = language;
el('language').addEventListener('change', event => { language = event.target.value; localStorage.setItem('trial-demo-language', language); translate(); });
el('startButton').addEventListener('click', async () => {
  el('inputError').hidden = true;
  try {
    const patients = parsePatients();
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const state = await api('/api/runs', {method:'POST', body:JSON.stringify({patients,mode})});
    runId = state.run_id; localStorage.setItem('trial-demo-run', runId); setRunning(state);
    pollTimer = setInterval(poll, 1000); await poll();
  } catch (error) { showError(error.message); }
});
el('cancelButton').addEventListener('click', () => el('cancelDialog').showModal());
el('cancelDialog').addEventListener('close', async () => {
  if (el('cancelDialog').returnValue !== 'confirm' || !runId) return;
  try { setRunning(await api(`/api/runs/${runId}/cancel`, {method:'POST'})); } catch (error) { showError(error.message); }
});
el('newRunButton').addEventListener('click', () => { completedRunId = null; el('patientDetail').hidden = true; el('resultsBand').hidden = true; el('patientInput').focus(); });

async function init() {
  translate();
  el('patientInput').value = sample.map(item => JSON.stringify(item)).join('\n');
  try {
    dataset = await api('/api/dataset');
    el('datasetBadge').textContent = `${dataset.counts.trials} trials · ${dataset.database_as_of || 'snapshot'}`;
    const full = document.querySelector('input[value="full"]');
    full.disabled = !dataset.model_enabled;
    el('fullModeLabel').title = dataset.model_enabled ? '' : 'Model screening is disabled on this deployment';
  } catch (error) { el('datasetBadge').textContent = error.message; }
  if (runId) { el('runBand').hidden = false; pollTimer = setInterval(poll, 1000); await poll(); }
}
init();
