let ui, root, catalog, current, job = null, task = "", group = "payment";
let variant = "original", busy = false, error = "", loaded = false, loading = false, polling = false;
let activeStep = 0, pinnedStep = false;
const expanded = new Set();
const savedKey = "decider-five-model-original-contract-v1";
const esc = (value) => ui.esc(value);
const ms = (value) => value == null ? "—" : value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value.toFixed(1)} ms`;
const lock = () => busy ? "disabled" : "";
const modelName = (p) => p.id === "gpt" && group === "routing" ? "GPT-6 Sol" : p.name;
const pretty = (value) => JSON.stringify(value, null, 2);
const phase = {queued: "等待轮次", running: "正在执行", done: "执行结束", error: "调用中断", skipped: "未执行", ok: "已执行", decided: "决策已返回"};

export async function mountComparison(helpers) {
  ui = helpers;
  if (loaded || loading) return;
  loading = true; root = document.querySelector("#comparison-workspace");
  try {
    catalog = await ui.api("/api/comparison/catalog");
    // Group temperature presets under devices in the UI; keep the API workflow unchanged.
    catalog.groups = catalog.groups.filter((g) => g.id !== "temperature_multiselect");
    catalog.cases = catalog.cases.map((c) => c.group === "temperature_multiselect"
      ? {...c, group: "device_control"} : c);
    select(catalog.cases[0].id);
    root.addEventListener("click", click);
    root.addEventListener("change", change);
    root.addEventListener("toggle", (event) => {
      const key = event.target.dataset.cDetail;
      if (key) event.target.open ? expanded.add(key) : expanded.delete(key);
    }, true);
    root.addEventListener("input", (event) => {
      if (event.target.matches("[data-c-task]") && !busy) {
        task = event.target.value;
        if (job) {
          job = null; sessionStorage.removeItem(savedKey);
          root.querySelector(".mc-models").innerHTML = catalog.providers.map(card).join("");
          root.querySelector(".mc-step-grid").innerHTML = catalog.providers.map(stepCard).join("");
          root.querySelector(".mc-summary").textContent = "任务已修改，重新运行后显示对应结果";
          root.querySelector("[data-c-export]").disabled = true;
        }
      }
    });
    loaded = true;
    const saved = sessionStorage.getItem(savedKey);
    if (saved) {
      try {
        job = await ui.api(`/api/comparison/runs/${encodeURIComponent(saved)}`);
        if (job.version !== "original-contract-v1") throw Error("旧版记录");
        select(job.case_id, false); task = job.task; variant = job.variant;
        busy = job.status === "running";
      } catch { sessionStorage.removeItem(savedKey); job = null; }
    }
    render(); if (busy) poll();
  } catch (e) {
    root.innerHTML = `<div class="result-error">${esc(e.message)} <button data-c-reload>重新加载</button></div>`;
    root.querySelector("[data-c-reload]").onclick = () => mountComparison(helpers);
  } finally { loading = false; }
}

function select(id, clear = true) {
  current = catalog.cases.find((c) => c.id === id);
  group = current.group; task = current.task; if (current.original) variant = "original"; activeStep = 0; pinnedStep = false; expanded.clear();
  if (clear) { job = null; error = ""; sessionStorage.removeItem(savedKey); }
}

function details(key, title, value) {
  return `<details class="mc-evidence" data-c-detail="${esc(key)}" ${expanded.has(key) ? "open" : ""}><summary>${esc(title)}</summary><pre>${esc(typeof value === "string" ? value : pretty(value))}</pre></details>`;
}

function summary() {
  if (!job) return "同一初始任务，按模型依次执行；三个本地模型常驻同一张 L4，每模型并发 1。";
  const outcomes = Object.values(job.outcomes);
  const done = outcomes.filter((r) => ["done", "error"].includes(r.status)).length;
  const achieved = outcomes.filter((r) => r.final?.complete === true).length;
  const acceptance = task === current.task ? `${achieved}/5 ${group === "routing" ? "完成两步路由" : "达成样例任务目标"}` : "自定义任务，需人工验收";
  return `<strong>${done}/5 模型执行结束</strong> · ${acceptance}${job.total_ms == null ? "" : ` · 本轮总等待 ${ms(job.total_ms)}`}`;
}

function render() {
  root.innerHTML = `
    <div class="mc-heading"><div><span class="tiny-label">FIVE MODELS · BUSINESS WORKFLOWS</span>
      <h1>五模型流程对比</h1><p>比较决策速度，以及任务真正完成了多少。</p></div><span class="mc-live">${busy ? "正在实时执行" : "GPU 常驻 · 单并发"}</span></div>
    <div class="mc-tabs" role="group" aria-label="对比场景">${catalog.groups.map((g) =>
      `<button data-c-group="${g.id}" class="${g.id === group ? "active" : ""}" aria-pressed="${g.id === group}" ${lock()}>${esc(g.name)}</button>`).join("")}</div>
    <div class="mc-toolbar">
      <button class="primary-button" data-c-run ${lock()}>${ui.icon("play")}${busy ? "整条流程执行中…" : "开始五模型流程对比"}</button>
      <label>业务场景<select data-c-case ${lock()}>${catalog.cases.filter((c) => c.group === group).map((c) =>
        `<option value="${esc(c.id)}" ${c.id === current.id ? "selected" : ""}>${esc(c.title)}</option>`).join("")}</select></label>
      <label>候选顺序<select data-c-order ${busy || current.original ? "disabled" : ""}><option value="original" ${variant === "original" ? "selected" : ""}>原始顺序</option><option value="reversed" ${variant === "reversed" ? "selected" : ""}>反转顺序</option></select></label>
      <button class="secondary-button" data-c-export ${!job || busy ? "disabled" : ""}>导出整轮结果</button>
    </div>
    ${error ? `<div class="mc-error" role="alert">${esc(error)}${busy ? ' <button data-c-resume>继续查询</button>' : ""}</div>` : ""}
    <section class="mc-task"><div class="mc-panel-heading"><h2>共同任务</h2><button class="text-button" data-c-reset ${lock()}>还原场景</button></div>
      <textarea data-c-task aria-label="共同任务输入" maxlength="1400" ${lock()} ${current.original ? "readonly" : ""}>${esc(task)}</textarea>
      <div class="mc-task-footer"><span>${current.original ? "沿用原模块输入 · " : ""}${esc(current.goal)}</span>${details("initial", "初始业务事实", current.initial)}</div>
    </section>
    <div class="mc-summary" aria-live="polite">${summary()}</div>
    <section class="mc-models" aria-label="五模型任务结果">${catalog.providers.map(card).join("")}</section>
    <div class="mc-step-heading"><h2>逐步输入、决策与执行结果</h2><span>点击步骤，对照五个模型的实际处理</span></div>
    <div class="mc-step-nav" role="group" aria-label="流程步骤">${current.steps.map((s, i) => `<button data-c-step="${i}" class="${activeStep === i ? "active" : ""}" aria-pressed="${activeStep === i}"><b>${i + 1}</b>${esc(s.title)}${s.kind === "execution" ? '<small>工具执行</small>' : ""}</button>`).join("")}</div>
    <section class="mc-step-grid" aria-label="当前步骤五模型对比">${catalog.providers.map(stepCard).join("")}</section>
    <details class="mc-notes"><summary>计时与任务验收</summary><p>累计决策耗时为各决策步骤的后端调用耗时之和；工具执行和本模型流程耗时单独列出，不包含等待其他模型轮次的时间。本轮总等待包含五个模型依次运行的时间。</p>
      <p>三个本地模型常驻同一张 L4。Decider / Liquid 为 BF16，H2O 为 FP8；每次只发起一个模型请求，避免同时抢占 GPU。支付固定流程对同一原始快照完成五次判断，再执行建议。模型路由沿用原始任务和候选证据，两步选型后可另行执行目标模型。</p>
      <p>支付与设备使用模拟执行器。支付与路由的任务、条件、候选顺序及 GPT 配置与原模块一致；路由选择完成不代表目标任务已执行。规则只保护执行，不替模型补选或重试。业务状态验收与参考选择一致次数分别展示；路由的基础验收不代表生成内容全部正确。修改任务后不沿用样例验收。</p></details>`;
}

function card(provider) {
  const r = job?.outcomes[provider.id], final = r?.final;
  const steps = r?.steps.filter((s) => s.status === "ok").length || 0;
  const hasTime = r?.steps.some((s) => s.timings?.processing_ms != null);
  const checks = final?.checks || [], passed = checks.filter((c) => c.passed).length;
  return `<article class="mc-model ${provider.id}" data-c-provider="${provider.id}">
    <header><span class="mc-phase ${r?.status || ""}">${phase[r?.status] || "等待开始"}</span><h2>${esc(modelName(provider))}</h2><small>${esc(provider.detail)}</small></header>
    <div class="mc-primary-time"><span>累计决策耗时</span><strong data-c-ms>${hasTime ? ms(r.decision_ms) : "—"}</strong></div>
    <div class="mc-timing"><span>工具执行 ${r ? ms(r.execution_ms) : "—"}</span><span>本模型流程 ${ms(r?.total_ms)}</span></div>
    <div class="mc-goal ${final?.complete === true ? "achieved" : final?.complete === false ? "incomplete" : ""}"><strong>${r?.status === "error" ? "调用中断 · 结果待核对" : final ? final.complete == null ? "自定义任务 · 待人工验收" : final.complete ? (group === "routing" ? "两步路由已完成" : "任务目标已达成") : (group === "routing" ? "路由尚未完成" : "任务目标未完成") : "任务尚未结束"}</strong>
      <span>已执行 ${steps}/${current.steps.length} 步${checks.length ? ` · 目标达成 ${passed}/${checks.length}` : ""}</span>
      <div class="mc-progress"><i style="width:${steps / current.steps.length * 100}%"></i></div>
      ${checks.map((c) => `<small>${c.passed ? "✓" : "×"} ${esc(c.label)}</small>`).join("")}</div>
    ${r?.scored_questions ? `<p class="mc-score">步骤选择符合参考 ${r.matched}/${r.scored_questions}</p>` : ""}
    ${final ? `<div class="mc-final"><span>最终结果</span><p>${esc(final.text)}</p>${details(provider.id + "-acceptance", "验收说明与最终状态", {note: final.acceptance_note, state: final.state})}</div>` : `<p class="mc-short-pending">${r?.status === "running" ? "正在处理当前业务步骤…" : "点击开始，运行完整业务流程"}</p>`}
    ${group === "routing" && r?.target_request ? `<div class="mc-target"><button class="secondary-button" data-c-target="${provider.id}" ${lock()}>执行目标模型</button>${r.target_execution ? `<p>${esc(r.target_execution.status)} · ${ms(r.target_execution.elapsed_ms)}</p><p>${esc(r.target_execution.result?.text || r.target_execution.error || "")}</p>${details(provider.id + "-target", "目标请求与执行结果", {request: r.target_request, ...r.target_execution})}` : '<p>目标尚未执行</p>'}</div>` : ""}
  </article>`;
}

function stepCard(provider) {
  const s = job?.outcomes[provider.id]?.steps[activeStep];
  return `<article class="mc-step-card ${provider.id}" data-c-step-provider="${provider.id}"><h3>${esc(modelName(provider))}</h3>
    ${!s ? '<p class="mc-short-pending">本步骤尚未执行</p>' : `<div class="mc-step-time">${phase[s.status]}<strong>${s.kind === "execution" ? ms(s.execution_ms) : ms(s.decision_ms ?? s.timings?.processing_ms)}</strong></div>
      ${s.request ? `<div class="mc-step-input"><span>${s.kind === "execution" ? "发送给目标模型" : "本步输入"}</span><pre>${esc(s.kind === "execution" ? s.request.task : s.request.state)}</pre>${details(`${provider.id}-${activeStep}-input`, "完整请求与候选项", s.request)}</div>` : ""}
      ${s.choices ? `<div class="mc-step-choice"><span>模型选择</span><strong>${esc(s.label || Object.values(s.choices).join("、"))}</strong><code>${esc(JSON.stringify(s.choices))}</code>${s.matched == null ? "" : `<small>符合参考 ${s.matched}/${s.question_count}</small>`}${details(`${provider.id}-${activeStep}-output`, "完整模型输出", s.output)}</div>` : ""}
      ${s.execution ? `<div class="mc-step-execution"><span>${s.execution.simulated ? "模拟执行结果" : "执行结果"}</span><p>${esc(s.execution.customer_reply || s.execution.text || s.execution.safety_reason || s.execution.message || s.execution.label)}</p>${s.execution.safety_adjusted ? `<small class="mc-protected">实际动作：${esc(s.execution.action)} · 执行保护已介入</small>` : ""}${details(`${provider.id}-${activeStep}-execution`, "完整执行记录", s.execution)}</div>` : ""}
      ${s.error ? `<div class="mc-error">${esc(s.error)}${s.raw_output || s.output ? details(`${provider.id}-${activeStep}-error`, "原始返回", s.raw_output || s.output) : ""}</div>` : ""}`}
  </article>`;
}

async function executeTarget(provider) {
  if (busy || !job?.outcomes[provider]?.target_request) return;
  const r = job.outcomes[provider];
  busy = true; error = ""; r.target_execution = {status: "执行中"}; render();
  const started = performance.now();
  try {
    const result = await ui.api("/api/routing/execute", r.target_request);
    r.target_execution = {status: result.complete ? "目标执行完成" : "目标未完成", result, elapsed_ms: performance.now() - started};
  } catch (e) {
    r.target_execution = {status: "目标调用失败", error: e.message, elapsed_ms: performance.now() - started};
  } finally { busy = false; render(); }
}

async function start() {
  if (busy) return;
  task = root.querySelector("[data-c-task]").value;
  if (!task.trim()) { error = "请输入任务"; render(); return; }
  busy = true; job = null; error = ""; pinnedStep = false; activeStep = 0; expanded.clear(); render();
  try {
    job = await ui.api("/api/comparison/runs", {case_id: current.id, task, variant});
    sessionStorage.setItem(savedKey, job.id); render(); await poll();
  } catch (e) { error = e.message; busy = false; render(); }
}

async function poll() {
  if (polling || !job) return;
  polling = true; let failures = 0;
  try {
    while (job.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 700));
      try {
        job = await ui.api(`/api/comparison/runs/${encodeURIComponent(job.id)}`);
        failures = 0; error = ""; busy = job.status === "running";
        if (!pinnedStep) {
          const running = Object.values(job.outcomes).find((r) => r.status === "running");
          activeStep = running ? Math.max(0, running.steps.length - 1) : job.status === "done" ? current.steps.length - 1 : activeStep;
        }
        render();
      } catch (e) {
        if ([401, 404].includes(e.status)) { busy = false; sessionStorage.removeItem(savedKey); error = e.message; render(); return; }
        if (++failures >= 4) { error = "查询中断，已提交的请求不会重复执行。"; render(); return; }
      }
    }
    busy = false; render();
  } finally { polling = false; }
}

function click(event) {
  const button = event.target.closest("button"); if (!button) return;
  if (button.hasAttribute("data-c-run")) start();
  if (button.dataset.cTarget) executeTarget(button.dataset.cTarget);
  if (button.hasAttribute("data-c-resume")) { error = ""; poll(); }
  if (button.hasAttribute("data-c-step")) { activeStep = Number(button.dataset.cStep); pinnedStep = true; render(); }
  if (button.hasAttribute("data-c-reset") && !busy) { select(current.id); render(); }
  if (button.dataset.cGroup && !busy) { select(catalog.cases.find((c) => c.group === button.dataset.cGroup).id); render(); }
  if (button.hasAttribute("data-c-export") && job && !busy) {
    const url = URL.createObjectURL(new Blob([pretty(job)], {type: "application/json"}));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `five-model-workflow-${job.case_id}-${job.id.slice(0, 6)}.json`;
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
function change(event) {
  if (busy) return;
  if (event.target.matches("[data-c-case]")) { select(event.target.value); render(); }
  if (event.target.matches("[data-c-order]")) { variant = event.target.value; job = null; sessionStorage.removeItem(savedKey); render(); }
}
