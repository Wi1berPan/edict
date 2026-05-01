let state = null;
let selectedVideoId = null;
let latestRun = null;

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function load() {
  state = await api("/api/state");
  selectedVideoId = selectedVideoId || state.videos[0]?.id;
  render();
}

function render() {
  const cfg = state.provider_config || {};
  $("providerBadge").textContent = `${cfg.provider || "local"} · ${cfg.ark_model || "doubao"} · key ${cfg.ark_api_key_configured ? "ok" : "missing"}`;
  $("kpis").innerHTML = [
    ["Risk domains", state.summary.domains],
    ["Videos", state.summary.videos],
    ["Analysis runs", state.summary.runs],
    ["Feedback", state.summary.feedback],
    ["Benchmarks", state.summary.benchmark_items],
  ].map(([label, value]) => `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div></div>`).join("");

  $("videoList").innerHTML = state.videos.map((video) => `
    <div class="card ${video.id === selectedVideoId ? "active" : ""}" onclick="selectVideo('${video.id}')">
      <div class="card-title">${escapeHtml(video.title)}</div>
      <div class="card-meta">${video.id}<br>${escapeHtml(video.description || video.transcript || "").slice(0, 130)}</div>
      ${video.file_name ? `<div class="small">File: ${escapeHtml(video.file_name)}</div>` : ""}
      ${video.source_url ? `<div class="small">URL: ${escapeHtml(video.source_url).slice(0, 80)}</div>` : ""}
    </div>
  `).join("");

  $("domainList").innerHTML = state.risk_domains.map((domain) => `
    <div class="card">
      <div class="row">
        <div class="card-title">${escapeHtml(domain.name)}</div>
        <span class="badge">v${state.agent_versions[domain.id] || 1}</span>
      </div>
      <div class="card-meta">${domain.subagent_id} · threshold ${domain.threshold}</div>
      <div class="small">${domain.keywords.map(escapeHtml).join(", ")}</div>
    </div>
  `).join("");

  renderBenchmarks();
  if (!latestRun && state.runs.length) {
    latestRun = state.runs[0];
  }
  renderRun();
}

function renderRun() {
  if (!latestRun) return;
  $("runDetail").classList.remove("empty");
  $("runDetail").innerHTML = `
    <div class="run-summary">
      <div class="metric">Final verdict <b class="${latestRun.final_verdict}">${latestRun.final_verdict}</b></div>
      <div class="metric">Risk score <b>${latestRun.risk_score}</b></div>
      <div class="metric">Video <b>${latestRun.video_id}</b></div>
    </div>
    <div class="trace-grid">
      <div class="trace-box"><h4>Grounding</h4><pre>${escapeHtml(JSON.stringify(latestRun.trace.grounding, null, 2))}</pre></div>
      <div class="trace-box"><h4>OCR</h4><pre>${escapeHtml(JSON.stringify(latestRun.trace.ocr, null, 2))}</pre></div>
      <div class="trace-box"><h4>Transcript</h4><pre>${escapeHtml(latestRun.trace.transcript || "")}</pre></div>
    </div>
    ${latestRun.domain_results.map(renderDomainResult).join("")}
  `;
}

function renderDomainResult(result) {
  const evidence = JSON.stringify(result.evidence, null, 2);
  const reasoning = JSON.stringify(result.reasoning, null, 2);
  return `
    <article class="domain-result">
      <div class="row">
        <div>
          <b>${escapeHtml(result.domain_name)}</b>
          <div class="small">${result.subagent_id} · v${result.agent_version}</div>
        </div>
        <div>
          <span class="badge ${result.verdict === "violation" ? "bad" : "ok"}">${result.verdict}</span>
          <span class="badge">score ${result.score}</span>
          <span class="badge">confidence ${result.confidence}</span>
          <span class="badge">${escapeHtml(result.provider || "local")}${result.model ? " · " + escapeHtml(result.model) : ""}</span>
        </div>
      </div>
      <div class="trace-grid">
        <div class="trace-box"><h4>Evidence</h4><pre>${escapeHtml(evidence)}</pre></div>
        <div class="trace-box"><h4>Analysis Process</h4><pre>${escapeHtml(reasoning)}</pre></div>
        <div class="trace-box"><h4>Feedback</h4>
          <div class="feedback-tools">
            <select id="label_${result.domain_id}">
              <option value="violation">violation</option>
              <option value="pass">pass</option>
            </select>
            <input id="note_${result.domain_id}" placeholder="Business feedback, badcase notes, new keyword..." />
            <select id="bench_${result.domain_id}" title="Sub-benchmark domain">
              ${state.risk_domains.map((domain) => `<option value="${domain.id}" ${domain.id === result.domain_id ? "selected" : ""}>${escapeHtml(domain.name)}</option>`).join("")}
            </select>
            <button onclick="sendFeedback('${result.domain_id}')">Submit Badcase</button>
          </div>
          <div class="feedback-row">
            <label class="checkline"><input id="addbm_${result.domain_id}" type="checkbox" checked /> Add to sub-benchmark</label>
            <label class="checkline"><input id="autoit_${result.domain_id}" type="checkbox" checked /> Auto iterate this subagent</label>
            <button class="ghost" onclick="rebuildSubagents('${result.domain_id}')">Rebuild This Subagent</button>
          </div>
        </div>
      </div>
    </article>
  `;
}

function renderBenchmarks(evaluation) {
  const summaries = state.sub_benchmarks || [];
  const subBenchHtml = summaries.map((item) => `
    <div class="subbench-card">
      <div class="row">
        <div class="subbench-title">${escapeHtml(item.domain_name)}</div>
        <span class="badge">v${item.agent_version}</span>
      </div>
      <div class="small">${item.subagent_id}</div>
      <div class="small">benchmarks ${item.benchmark_count} · badcases ${item.badcase_count} · feedback ${item.feedback_count}</div>
      <div class="subbench-actions">
        <button class="ghost" onclick="evaluateDomain('${item.domain_id}')">Eval</button>
        <button class="ghost" onclick="rebuildSubagents('${item.domain_id}')">Rebuild</button>
      </div>
    </div>
  `).join("");
  const rows = evaluation?.rows || state.benchmark_items.map((item) => ({
    benchmark_id: item.id,
    video_id: item.video_id,
    domain_id: item.domain_id,
    expected: item.expected_label,
    predicted: "-",
    passed: null,
  }));
  $("benchmarkBox").innerHTML = `
    <div class="panel-head">
      <p>Sub-benchmark maintenance by risk domain</p>
      <button class="ghost" onclick="rebuildSubagents('')">Rebuild All Subagents</button>
    </div>
    <div class="subbench-grid">${subBenchHtml}</div>
    ${evaluation ? `<p>Accuracy ${evaluation.accuracy} (${evaluation.passed}/${evaluation.total}) ${evaluation.domain_id ? "· " + evaluation.domain_id : ""}</p>` : ""}
    <div class="benchmark-row"><b>Benchmark</b><b>Video</b><b>Domain</b><b>Expected</b><b>Predicted</b></div>
    ${rows.map((row) => `
      <div class="benchmark-row">
        <span>${row.benchmark_id}</span>
        <span>${row.video_id}</span>
        <span>${row.domain_id}</span>
        <span>${row.expected}</span>
        <span class="${row.passed === false ? "violation" : row.passed === true ? "pass" : ""}">${row.predicted}</span>
      </div>
    `).join("")}
    ${renderIterationLog()}
  `;
}

function renderIterationLog() {
  const items = (state.iteration_log || []).slice(0, 8);
  if (!items.length) return "";
  return `
    <div class="iter-log">
      <p>Recent subagent rebuilds</p>
      ${items.map((item) => `
        <div class="iter-item">
          <span>${escapeHtml(item.domain_id)} · ${escapeHtml(item.reason)} · feedback ${item.feedback_count} · benchmark ${item.benchmark_count}</span>
          <span>v${item.old_version} → v${item.new_version}</span>
        </div>
      `).join("")}
    </div>
  `;
}

window.selectVideo = (id) => {
  selectedVideoId = id;
  render();
};

$("seedBtn").onclick = async () => {
  if (!selectedVideoId) return;
  const provider = $("providerSelect").value;
  const data = await api(`/api/videos/${selectedVideoId}/analyze?provider=${encodeURIComponent(provider)}`, { method: "POST" });
  latestRun = data.run;
  await load();
};

$("evalBtn").onclick = async () => {
  const data = await api("/api/benchmarks/evaluate", { method: "POST" });
  renderBenchmarks(data);
};

$("refreshBtn").onclick = load;

$("videoForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const grounding = String(form.get("grounding") || "")
    .split("\n")
    .map((label) => label.trim())
    .filter(Boolean)
    .map((label, index) => ({ frame: `00:00:0${index}.000`, label, confidence: 0.75, box: [] }));
  const payload = {
    title: String(form.get("title") || ""),
    source_url: String(form.get("source_url") || ""),
    transcript: String(form.get("transcript") || ""),
    ocr_texts: String(form.get("ocr_texts") || "").split("\n").map((s) => s.trim()).filter(Boolean),
    grounding,
  };
  const data = await api("/api/videos", { method: "POST", body: JSON.stringify(payload) });
  selectedVideoId = data.video.id;
  event.target.reset();
  await load();
};

$("uploadForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const res = await fetch("/api/videos/upload", { method: "POST", body: form });
  if (!res.ok) {
    alert(await res.text());
    return;
  }
  const data = await res.json();
  selectedVideoId = data.video.id;
  event.target.reset();
  await load();
};

$("themeBtn").onclick = () => {
  document.body.classList.toggle("light");
  const light = document.body.classList.contains("light");
  localStorage.setItem("vm_theme", light ? "light" : "dark");
  $("themeBtn").textContent = light ? "Dark Mode" : "Light Mode";
};

if (localStorage.getItem("vm_theme") === "light") {
  document.body.classList.add("light");
  $("themeBtn").textContent = "Dark Mode";
}

window.sendFeedback = async (domainId) => {
  if (!latestRun) return;
  const correctLabel = $(`label_${domainId}`).value;
  const notes = $(`note_${domainId}`).value;
  const benchmarkDomainId = $(`bench_${domainId}`).value;
  const addToBenchmark = $(`addbm_${domainId}`).checked;
  const autoIterate = $(`autoit_${domainId}`).checked;
  await api(`/api/runs/${latestRun.id}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      domain_id: domainId,
      correct_label: correctLabel,
      notes,
      benchmark_domain_id: benchmarkDomainId,
      add_to_benchmark: addToBenchmark,
      auto_iterate: autoIterate,
    }),
  });
  await load();
};

window.evaluateDomain = async (domainId) => {
  const data = await api(`/api/sub-benchmarks/${domainId}/evaluate`, { method: "POST" });
  renderBenchmarks(data);
};

window.rebuildSubagents = async (domainId) => {
  await api("/api/subagents/rebuild", {
    method: "POST",
    body: JSON.stringify({ domain_id: domainId || null, reason: domainId ? "manual_domain_rebuild" : "manual_all_rebuild" }),
  });
  await load();
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

load().catch((err) => {
  $("runDetail").textContent = err.message;
});
