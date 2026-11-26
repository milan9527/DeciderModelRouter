import { decisionConfidenceHTML, evidencePresentationHTML, selectionAdapterHTML, runtimeObservationsHTML, taskScoreSummaryHTML, pricingHTML } from "./decision-details.js";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c],
  );
const ms = (value) => (Number.isFinite(value) ? `${value.toFixed(1)} ms` : "—");
const usd = (value) =>
  value != null && Number.isFinite(Number(value))
    ? `$${Number(value).toFixed(6)}`
    : "未知";
const stageNames = {
  catalog: "目录校验",
  classification: "GPU 判断需求",
  selection: "GPU 选择模型",
  execution: "Bedrock 执行",
};
const stateNames = {
  started: "处理中",
  completed: "完成",
  failed: "失败",
  pending: "等待",
  skipped: "未执行",
};
const providerNames = {
  decider: "Strands Decider · 2B（对比用）",
  liquid: "LiquidAI d1 · 2B",
  h2o: "H2O Lightning · 4B",
};
const statuses = {
  executed: "已完成",
  incomplete: "输出未完成",
  selected: "已选模",
  no_route: "无符合条件模型",
  error: "请求失败",
  cancelled: "已停止等待",
};
let catalog,
  controller,
  busy = false,
  events = [],
  stages = {},
  flow,
  selection,
  route,
  execution,
  completed;
let history = [],
  currentMode = "live",
  startTime = 0,
  clockTimer,
  lastBrowserMs = null,
  terminalSeen = false;
let activeTemplateId = null;
let mediaInputs = [], uploadBusy = false, mediaEpoch = 0, mediaJobTimer;
const workspace = new Workspace({
  api, esc, ms, usd, signedOut: showLogin,
  reuse: (request) => {
    if (busy) { workspace.notify("请等待当前请求结束后再载入历史任务"); return; }
    $("#task").value = request.task.text;
    $("#provider").value = request.provider;
    $("#mode").value = request.mode;
    const p = request.task.routing_preferences || {};
    for (const [id, key] of [["expected-output", "expected_output_tokens"], ["max-output", "max_output_tokens"], ["max-cost", "max_reference_cost_usd"], ["min-success", "min_success_rate"], ["max-latency", "max_latency_ms"]])
      $(`#${id}`).value = p[key] ?? "";
    if (p.cost_tier) $(`input[name="tier"][value="${p.cost_tier}"]`).checked = true;
    $("#benchmark-release").value = p.benchmark_release || "auto";
    $("#cross-region").checked = request.task.allow_cross_region !== false;
    activeTemplateId = request.template_id;
    mediaInputs = (request.task.attachments || []).map((a) => ({ ...a }));
    $("#output-modality").value = request.task.output_modality || "auto";
    $("#image-operation").value = request.task.media_options?.image_operation || "generate";
    $("#edit-target").value = request.task.media_options?.edit_target || "";
    $("#speech-voice").value = request.task.media_options?.voice || "tiffany";
    $("#rerank-documents").value = (request.task.media_options?.documents || []).join("\n");
    renderMediaInputs(); updateMediaOptions();
    $("#char-count").textContent = `${$("#task").value.length} / 1400`;
    updateMode();
  },
});

function errorMessage(data) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail))
    return data.detail.map((item) => item.msg).join("；");
  return "请求失败，请稍后重试。";
}
async function api(path, body) {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers: {
      "X-Decider-Client": "web",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.status === 401) showLogin();
  if (!response.ok) throw new Error(errorMessage(await response.json()));
  return response.json();
}
function resetRun() {
  clearTimeout(mediaJobTimer);
  events = [];
  stages = {};
  flow = selection = route = execution = completed = null;
  lastBrowserMs = null;
  terminalSeen = false;
  $("#request-error").hidden = true;
  $("#conversation-state").textContent = "";
  $("#response").innerHTML =
    '<p class="empty-copy">执行后，模型的实际输出将显示在这里。</p>';
  $("#decision").innerHTML =
    '<div class="empty-state"><div class="route-art" aria-hidden="true"><span>任务</span><i></i><b>◈</b><i></i><span>模型</span></div><h3>等待模型决策</h3><p>GPU 判断任务需求，并依据评测选择模型。</p></div>';
  $("#request-id").textContent = "REQUEST ID · —";
  $("#wall-clock").textContent = "等待开始";
  $("#export").disabled = true;
  render();
}
function showLogin() {
  mediaEpoch++;
  mediaInputs = [];
  clearTimeout(mediaJobTimer);
  renderMediaInputs();
  controller?.abort();
  workspace.reset();
  history = [];
  $("#app").hidden = true;
  $("#auth").hidden = false;
}
async function enter(user) {
  catalog = await api("/gateway/catalog");
  renderMediaCapabilities();
  history = [];
  resetRun();
  $("#username").textContent = user.username;
  $("#region").textContent = `Amazon Bedrock · ${catalog.region}`;
  $("#provider").innerHTML = catalog.decision_service.providers
    .map(
      (p) =>
        `<option value="${esc(p.id)}" ${!p.configured ? "disabled" : ""}>${esc(providerNames[p.id])}${p.configured ? "" : " · 未配置"}</option>`,
    )
    .join("");
  $("#provider").value = catalog.decision_service.default_provider;
  $("#template-category").innerHTML = '<option value="all">全部场景</option>' +
    [...new Set(catalog.examples.map((e) => e.category))].map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  renderTemplates();
  $("#category").innerHTML = Object.entries(catalog.categories)
    .map(
      ([key, value]) =>
        `<option value="${esc(key)}">${esc(value.label)}</option>`,
    )
    .join("");
  $("#catalog-summary").textContent =
    `${catalog.models.length} 个 Bedrock 型号 · ${catalog.evidence_inventory.bedrock_measured_models || 0} 个已有端点实测记录 · ${catalog.evidence_inventory.public_benchmarks?.model_count ?? catalog.evidence_inventory.mapped_models} 个有公开能力评测。文本能力按 LiveBench 判断；旧答题测试仅作历史展示，不参与文本选模。端点实测仍用于延迟和接口兼容，多模态保留专项依据。`;
  $("#benchmark-release").innerHTML =
    '<option value="auto">自动 · 全部可用候选</option>' +
    [...catalog.evidence_inventory.benchmark_releases].reverse()
      .map((r) => `<option value="${esc(r.release)}">LiveBench ${esc(r.release)}</option>`)
      .join("");
  $("#auth").hidden = true;
  $("#app").hidden = false;
  $("#login-form").reset();
  setPage();
  renderModels();
  renderHistory();
  updateMode();
  await workspace.enter(user, catalog);
  checkHealth();
}
function setPage() {
  const page = ["workspace", "agents", "models", "performance", "evaluations", "account"].includes(
    location.hash.slice(1),
  )
    ? location.hash.slice(1)
    : "workspace";
  $$("[data-view]").forEach((el) => {
    el.hidden = el.dataset.view !== page;
  });
  $$("[data-page]").forEach((el) =>
    el.classList.toggle("active", el.dataset.page === page),
  );
  $("#page-title").textContent = {
    workspace: "网关工作台",
    agents: "行业 Agent",
    models: "Bedrock 模型",
    performance: "历史与性能",
    evaluations: "决策模型评测",
    account: "账户管理",
  }[page];
  if (page === "performance") renderHistory();
  else workspace.page(page);
}
async function checkHealth() {
  const provider = $("#provider").value;
  $("#health").disabled = true;
  $("#health span").textContent = "检查 GPU 服务…";
  try {
    const status = await api(
      `/gateway/status?provider=${encodeURIComponent(provider)}`,
    );
    if ($("#provider").value !== provider) return;
    $("#health").className =
      `status ${status.decision.connected ? "ready" : "down"}`;
    $("#health span").textContent = status.decision.connected
      ? "GPU 服务已连接"
      : "GPU 服务未就绪";
    $("#health").title = status.decision.message + "；此检查不执行模型推理";
  } catch (error) {
    $("#health").className = "status down";
    $("#health span").textContent = "连接检查失败";
    $("#health").title = error.message;
  } finally {
    $("#health").disabled = false;
  }
}
function setExample(id) {
  const example = catalog.examples.find((e) => e.id === id);
  $("#task").value = example.task;
  $("#expected-output").value = example.routing_preferences.expected_output_tokens;
  $("#max-output").value = example.routing_preferences.max_output_tokens;
  $("#char-count").textContent = `${$("#task").value.length} / 1400`;
  activeTemplateId = id;
  $("#output-modality").value = example.output_modality || "auto";
  $("#image-operation").value = example.media_options?.image_operation || "generate";
  $("#edit-target").value = example.media_options?.edit_target || "";
  updateMediaOptions();
  $("#template-rubric").textContent = "验收参考：" + (example.rubric || []).join("；");
}
function renderTemplates() {
  if (!catalog) return;
  const category = $("#template-category").value, search = $("#template-search").value.trim().toLowerCase();
  const templates = catalog.examples.filter((e) =>
    (category === "all" || e.category === category) &&
    `${e.title} ${e.description} ${e.task}`.toLowerCase().includes(search));
  $("#examples").innerHTML = templates.map((e) =>
    `<button type="button" data-example="${esc(e.id)}" title="${esc(e.description)}">${esc(e.title)} ↗</button>`).join("") ||
    '<p class="footnote">没有符合条件的模板。</p>';
}
function updateMediaOptions() {
  const output = $("#output-modality").value;
  $("#image-options").hidden = output !== "image";
  $("#edit-target-options").hidden = output !== "image" || !["recolor", "replace"].includes($("#image-operation").value);
  $("#voice-options").hidden = output !== "audio";
  $("#rerank-options").hidden = output !== "rerank";
  const scenario = catalog?.media?.scenarios.find((s) =>
    s.output_modality === output && (output !== "image" || s.image_operation === $("#image-operation").value));
  $("#media-notice").textContent = scenario?.status === "unavailable"
    ? "当前区域没有此操作的可执行模型；仍可分析路由并查看原因。"
    : output === "audio" ? "Nova 2 Sonic 支持英语、西班牙语等语音；本次已验证英语，暂不支持中文语音。每次生成一段回复。"
    : "可直接自由输入，也可结合附件提问。模板只提供示例。";
}
function renderMediaInputs() {
  $("#media-inputs").innerHTML = mediaInputs.map((a, i) =>
    `<div class="media-input"><span>${esc({ image: "图片", audio: "录音", video: "视频" }[a.kind])} · ${esc(a.id.slice(0, 8))}${a.size ? ` · ${(a.size / 1000).toFixed(0)} KB` : ""}</span><button type="button" class="text-button" data-remove-media="${i}">移除</button></div>`).join("");
}
function renderMediaCapabilities() {
  const scenarios = catalog.media?.scenarios || [];
  $("#media-capabilities").innerHTML = `<table><thead><tr><th>能力</th><th>执行与评测</th><th>匹配型号</th><th>输入要求</th></tr></thead><tbody>${scenarios.map((s) => {
    const measured = s.model_ids.filter(id => catalog.evidence_inventory.models[id]?.bedrock_evaluation?.measurements?.[s.workflow]?.samples > 0).length;
    return `<tr><td>${esc(s.label)}</td><td>${s.status === "available" ? `接口已接入<br>${measured}/${s.model_ids.length} 个型号有本类实测记录` : "当前区域无可用模型"}</td><td>${s.model_ids.length ? `<details><summary>${s.model_ids.length} 个匹配型号</summary>${s.model_ids.map(esc).join("<br>")}</details>` : "—"}</td><td>${esc(s.needs || "文字或适配的媒体输入")}</td></tr>`;
  }).join("")}</tbody></table><p class="footnote">有测量记录不等于质量通过；逐型号的内容正确、待复核和调用错误见下方评测详情。</p>`;
}
async function uploadMedia() {
  if (busy || uploadBusy) return;
  const files = [...$("#media-upload").files], epoch = mediaEpoch;
  if (files.length + mediaInputs.length > 4) { showError("每次请求最多四个附件。"); return; }
  uploadBusy = true;
  $("#media-upload").disabled = true;
  $("#media-notice").textContent = "正在上传到私有媒体存储…";
  try {
    for (const file of files) {
      const limit = file.type.startsWith("image/") ? 3500000 : file.type.startsWith("audio/") ? 10000000 : 20000000;
      if (!file.size || file.size > limit) throw new Error("文件为空或超过对应格式的上传上限。");
      const response = await fetch("/api/gateway/media", {
        method: "POST", credentials: "same-origin",
        headers: { "X-Decider-Client": "web", "Content-Type": file.type || "application/octet-stream" },
        body: file,
      });
      const data = await response.json();
      if (epoch !== mediaEpoch) return;
      if (!response.ok) throw new Error(errorMessage(data));
      mediaInputs.push(data); renderMediaInputs();
    }
    if (!$("#task").value.trim()) $("#task").value = "请分析上传的内容并用中文说明。";
    updateMediaOptions();
  } catch (error) {
    if (epoch === mediaEpoch) { showError(error.message); $("#media-notice").textContent = error.message; }
  } finally {
    uploadBusy = false; $("#media-upload").disabled = false; $("#media-upload").value = "";
  }
}
async function pollMediaJob(job, epoch) {
  try {
    const result = await api("/gateway/media/jobs/" + encodeURIComponent(job.id));
    if (epoch !== mediaEpoch || execution?.media_job?.id !== job.id) return;
    $("#media-job-state").textContent = `视频任务：${{ running: "处理中", completed: "已完成", failed: "失败" }[result.status] || result.status}`;
    $("#media-job-output").innerHTML = mediaHTML(result.artifacts);
    if (result.status === "running") mediaJobTimer = setTimeout(() => pollMediaJob(job, epoch), 10000);
  } catch (error) {
    if (epoch === mediaEpoch && $("#media-job-state")) $("#media-job-state").textContent = error.message;
  }
}
function updateMode() {
  const replay = $("#mode").value === "replay";
  $("#run").innerHTML = replay
    ? "查看流程回放 <span>→</span>"
    : "路由并执行 <span>→</span>";
  $("#mode-note").textContent = replay
    ? "回放仅演示规则路径，不调用 GPU 或 Bedrock，也不产生模型性能数据。"
    : "参考费用用于选模，不是实际账单；不含 GPU 决策成本。";
}
function metric(label, value, note, icon) {
  return `<div class="metric"><div class="metric-label">${esc(label)}<span>${icon}</span></div><div class="metric-value">${esc(value)}</div><small>${esc(note)}</small></div>`;
}
function renderMetrics() {
  const calls = selection?.selection?.decision_calls;
  const gpuMs = calls?.length
    ? calls.reduce((sum, c) => sum + (c.timings?.upstream_ms || 0), 0)
    : flow?.timings?.upstream_ms;
  $("#metrics").innerHTML = [
    metric(
      "GPU 请求往返",
      ms(gpuMs),
      "全部已完成 GPU 请求的往返合计",
      "◈",
    ),
    metric(
      "路由总耗时",
      ms(route?.route_total_ms),
      "需求判断、模型选择、路由凭据",
      "↗",
    ),
    metric(
      "目标模型耗时",
      ms(execution?.backend_total_ms),
      "Bedrock 排队与调用总耗时",
      "◷",
    ),
    metric(
      "用量参考费用",
      execution ? usd(execution.observed_reference_cost_usd) : "—",
      "实际 Token × 参考单价，不含 GPU",
      "$",
    ),
  ].join("");
}
function renderStages() {
  $("#stages").innerHTML = Object.entries(stageNames)
    .map(([key, name], index) => {
      const stage = stages[key] || {
        status: completed ? "skipped" : "pending",
      };
      const timing =
        stage.data?.timings?.backend_total_ms ?? stage.data?.backend_total_ms;
      const label =
        currentMode === "replay" && stage.status === "completed"
          ? "回放 · 无推理计时"
          : Number.isFinite(timing)
            ? ms(timing)
            : stateNames[stage.status];
      return `<div class="stage ${esc(stage.status)}"><div class="stage-icon">${stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : `0${index + 1}`}</div><strong>${name}</strong><small>${esc(label)}</small></div>`;
    })
    .join("");
  const status = completed?.status;
  $("#run-badge").textContent = busy
    ? currentMode === "replay"
      ? "流程回放中"
      : "实时处理中"
    : status
      ? `${currentMode === "replay" ? "回放 · " : ""}${statuses[status] || status}`
      : "等待请求";
  $("#run-badge").className =
    `badge ${busy ? "running" : ["error", "cancelled", "no_route", "incomplete"].includes(status) ? "failed" : status ? "ok" : ""}`;
}
function renderDecision() {
  if (!flow) return;
  const profile = flow.profile;
  const chips = [
    ["类别", flow.label],
    [
      "难度",
      { simple: "简单", moderate: "中等", complex: "复杂" }[profile.difficulty],
    ],
    [
      "强模型收益",
      { low: "低", medium: "中", high: "高" }[profile.stronger_model_benefit],
    ],
    ["延迟", profile.latency === "sensitive" ? "敏感" : "常规"],
    ["工具", profile.tools === "yes" ? "需要" : "不需要"],
    ["结构输出", profile.structured_output === "yes" ? "需要" : "不需要"],
    [
      "语言",
      { en: "英语", zh: "中文", en_zh: "中英", other: "其他" }[
        profile.languages
      ],
    ],
  ];
  const s = selection?.selection,
    model = selection?.selected_model;
  $("#decision").innerHTML = `<div class="decision-content">
    <div class="winner"><div class="winner-icon">◈</div><div><h3>${esc(model?.name || (selection ? "无符合条件的模型" : "已完成任务分类"))}</h3>
    <code>${esc(model?.id || (selection ? "NO ELIGIBLE MODEL" : flow.model || "流程回放"))}</code>
    <small>${esc(s?.selected_benchmark?.benchmark_configuration || "等待策略筛选")}</small></div></div>
    <div class="profile-chips">${chips.map(([k, v]) => `<span>${esc(k)} · ${esc(v)}</span>`).join("")}</div>
    ${flow.profile_adjustment ? `<p class="footnote">${esc(flow.profile_adjustment)} GPU 原始类别：${esc(flow.assessed_profile?.workflow)}。</p>` : ""}
    ${s ? `<div class="reason">${esc(s.tier_reason)} ${esc(s.reason)}</div><div class="selection-meta"><span>${s.candidate_count} 个符合本轮判断的配置</span><span>${s.selection_source === "gpu_decision_model" ? `${s.evaluated_configurations || 0} 个参与 GPU 判断 · ${s.decision_calls?.length || 0} 次 GPU 请求` : `${s.frontier.length} 个非劣候选`}</span><span>${s.selection_source === "gpu_decision_model" ? `${s.decision_mode === "two_stage" ? "两阶段决策" : s.decision_mode === "joint" ? "联合决策" : "选模"}耗时 ${ms(s.decision_ms)} · 直接实测 + 外部参考` : "流程回放"}</span></div>${s.benchmark_group ? `<p class="footnote">本轮评测：${esc(s.benchmark_group.source)} ${esc(s.benchmark_group.release || "")} · ${s.benchmark_group.metrics.map(esc).join(" / ")}。</p>` : ""}` : ""}
    ${evidencePresentationHTML(s?.evidence_presentation, esc)}
    ${selectionAdapterHTML(s?.selection_adapter, esc)}
    ${decisionConfidenceHTML(s?.decision_distribution, esc)}</div>`;
}
function renderCandidates() {
  const s = selection?.selection;
  if (!s) {
    $("#candidates").innerHTML =
      '<p class="empty-copy">分类后展示候选模型、过滤原因和可比评测。</p>';
    return;
  }
  const rows = [...s.candidates].sort(
    (a, b) =>
      Number(b.candidate_id === s.selected_candidate_id) -
      Number(a.candidate_id === s.selected_candidate_id),
  );
  if (s.selection_source === "gpu_decision_model") {
    const names = Object.fromEntries(rows.map(r => [r.candidate_id, r.model.name]));
    $("#candidates").innerHTML = `<p class="footnote">${s.decision_mode === "joint" ? "所有可能适用的配置一次交给 GPU，同时判断任务属性和模型选择；下表显示符合本轮任务属性的配置。" : s.decision_mode === "two_stage" ? "第一次GPU判断需求，筛选后第二次GPU比较全部合格配置。" : s.decision_mode === "single_choice" ? "全部合格配置在一次GPU请求中比较。" : "此历史记录使用分组比较。"}${s.quality_policy?.preference_handling === "gpu_only" ? "三种偏好统一交给GPU判断，不按质量分数淘汰候选，不增加质量重选。" : s.quality_policy ? "此历史策略曾额外执行能力优先校验，最多一次GPU重选。" : "此历史记录保留当时策略。"}外部成绩不跨批次直接比较。</p>
      ${s.quality_basis === "livebench_only" ? "<p class=\"footnote\">本次文本质量仅依据 LiveBench；旧答题测试和开发回归分数不参与选择。延迟、价格、接口兼容仍单独检查。</p>" : ""}
      ${qualityPolicyHTML(s)}<div class="table-scroll"><table class="candidate-table"><thead><tr><th>模型 / 配置</th><th>预计费用</th><th>运行实测与质量依据</th><th>GPU 结果</th></tr></thead><tbody>${rows.map(r => `<tr class="${r.candidate_id === s.selected_candidate_id ? "selected" : ""}"><td>${esc(r.model.name)}<small>${esc(JSON.stringify(r.benchmark.request_fields || {}))} · ${r.measurement_configuration ? (r.measurement_configuration.max_output_tokens != null ? `实测上限 ${r.measurement_configuration.max_output_tokens} Token` : "专用接口实测") : "此配置没有直接实测"}</small><small>本次输出上限 ${esc(r.output_token_limit ?? "由专用接口决定")}</small></td><td>${usd(r.cost_evidence?.expected_usd)}<small>${esc(r.price_scope)}</small>${pricingHTML(r.benchmark.pricing_source, esc, r.cost_evidence?.context_tier)}</td><td>${r.quality_basis === "livebench_only" ? operationalHTML(r.measured_evidence) : measurementHTML(r.measured_evidence)}${taskScoreSummaryHTML(s.evidence_presentation, r.candidate_id, esc)}${runtimeObservationsHTML(r.runtime_observations, esc)}${feedbackHTML(r.quality_feedback)}${publicEvidenceHTML(r.public_benchmarks, "本次公开依据（原始记录）")}${!r.public_benchmarks?.length ? (r.quality_basis === "livebench_only" ? "<small>缺少本次相关的 LiveBench 依据</small>" : (r.public_evidence || []).map(e => `<small>外部参考：${esc(e.source)} ${esc(e.release)} · ${esc(JSON.stringify(e.scores))}</small>`).join("")) : ""}</td><td>${r.candidate_id === s.selected_candidate_id ? "✓ GPU 已选择" : "已参与判断，未选中"}${s.decision_distribution?.probabilities?.[r.candidate_id] != null ? `<small>选择倾向 ${(s.decision_distribution.probabilities[r.candidate_id] * 100).toFixed(2)}%</small>` : ""}${r.quality_assessment ? `<small>${esc(r.quality_assessment.reason)}</small>` : ""}</td></tr>`).join("")}</tbody></table></div>
      <details class="excluded"><summary>GPU 决策过程（${s.decision_calls?.length || 0} 次）</summary>${(s.decision_calls || []).map(c => `<p>${["profile", "assessment"].includes(c.mode) ? "需求判断" : c.mode === "selection" ? "模型选择" : c.mode === "quality_reselection" ? "质量校验后的GPU重选" : `第 ${c.round} 轮`} · ${esc(c.model)} · ${ms(c.timings?.backend_total_ms)}<br>候选：${["profile", "assessment"].includes(c.mode) ? "一次评估全部7项需求" : (c.candidate_ids || []).map(id => esc(names[id] || id)).join("、")}<br>选择：${["profile", "assessment"].includes(c.mode) ? "见上方任务属性" : esc(names[c.selected_candidate_id] || "不执行")}${c.quality_check ? `<small>${s.quality_policy?.active ? "质量校验" : "候选校验"}：${c.quality_check.accepted ? "通过" : "未通过"} ${esc(c.quality_check.reason || "")}</small>` : ""}<small>输入摘要 ${esc(c.input_hash)}</small></p>`).join("")}</details>
      <details class="excluded"><summary>未进入本次决策的原因（${Object.keys(s.excluded).length}）</summary>${Object.entries(s.excluded).map(([k,v]) => `<p><code>${esc(k)}</code><br>${esc(v)}</p>`).join("")}</details>
      <p class="footnote">实测版本 ${esc(s.measurement_version)} · 公开评测版本 ${esc(s.public_benchmark_version || "此历史记录未接入")} · 任务回归 ${s.quality_basis === "livebench_only" ? "不用于本次文本选模" : esc(s.quality_feedback_version || "此历史记录未接入")}。费用不含 GPU，不代表账单；公开分数保留原始口径，不代表本次成功概率。</p>`;
    return;
  }
  $("#candidates").innerHTML =
    `<p class="footnote">本轮依据：${esc(s.evidence_mode)} · ${esc(s.cost_mode || "暂无可比费用")} · ${esc(s.benchmark_group?.release || "")}。${s.evidence_mode === "capability_only" ? "仅按输入输出能力与执行接口匹配；未使用文本榜单，也未将未知媒体费用按零计算。" : "非劣候选表示没有另一配置同时在所有比较项更好。能力分项不是业务成功率。"}</p>
    <div class="table-scroll"><table class="candidate-table"><thead><tr><th>模型 / 实测配置</th><th>预计参考费用</th><th>能力分项</th><th>策略结果</th></tr></thead><tbody>
    ${rows
      .map(
        (r) =>
          `<tr class="${r.candidate_id === s.selected_candidate_id ? "selected" : ""}"><td>${esc(r.model.name)}<small>${esc(r.benchmark.benchmark_configuration)}</small></td><td>${usd(r.cost_evidence?.expected_usd)}<small>上限 ${usd(r.cost_evidence?.at_output_cap_usd)}</small></td><td>${Object.entries(
            r.task_scores,
          )
            .map(([k, v]) => `${esc(k)} ${Number(v).toFixed(2)}`)
            .join(
              "<br>",
            ) || "未评测"}</td><td>${r.candidate_id === s.selected_candidate_id ? "✓ 已选择" : s.evidence_mode === "capability_only" ? "能力匹配" : s.frontier.includes(r.candidate_id) ? "非劣候选" : "被其他配置支配"}</td></tr>`,
      )
      .join("")}
    </tbody></table></div><p class="footnote">${s.evidence_mode === "capability_only" ? "图片、音频、视频的用量与计价口径取决于型号；当前费用未知，不能用文字 Token 估算替代。" : `输入估计 ${s.cost_context.input_tokens} Token（${s.cost_context.input_token_source === "utf8_bytes_proxy" ? "UTF-8 字节代理，并非模型分词" : "调用方估计"}）；期望输出 ${s.cost_context.expected_output_tokens}，上限 ${s.cost_context.max_output_tokens}。费用按快照参考价计算，不含 GPU、缓存和平台折扣。`}</p>
    <details class="excluded"><summary>查看排除记录（${Object.keys(s.excluded).length}）</summary>${Object.entries(
      s.excluded,
    )
      .map(([k, v]) => `<p><code>${esc(k)}</code><br>${esc(v)}</p>`)
      .join("")}</details>`;
}
function qualityPolicyHTML(s) {
  const p = s.quality_policy;
  if (!p?.active) return "";
  const comparison = p.comparison;
  return `<section class="quality-policy"><h3>能力优先校验</h3><p>${esc(p.note)}</p>
    ${comparison ? `<p>比较依据：${esc(comparison.benchmark || "Bedrock同套件开发测试")} ${esc(comparison.release || "")} · ${comparison.dimensions.map(esc).join(" / ")}。${p.allowed_candidate_ids.length} 个配置通过，GPU重选 ${s.quality_reselection_count || 0} 次。</p>` : ""}
    <p class="footnote">此处是按证据计算的策略核验，不是决策模型的思维过程；不能保证目标回答正确。</p></section>`;
}
function feedbackHTML(f) {
  if (!f?.samples) return "";
  return `<details class="model-evidence"><summary>新增任务回归：内容 ${f.content_passed}/${f.samples} · 完整验收 ${f.strict_passed}/${f.samples}</summary>
    <p>${esc(f.failure_categories.join("、") || "这些样本没有已判定失败")}</p>
    <p class="footnote">已复核的开发任务，输入和预算不同，单独提供给GPU；不与原6题或公开榜单混算，不代表生产成功率。</p></details>`;
}
function operationalHTML(m) {
  return `<small>端点运行记录：P95 ${ms(m?.p95_ms)} · 调用错误 ${m?.call_errors ?? "未知"}<br>短请求实测，仅供延迟参考，不是本次耗时预测或质量分数。</small>`;
}
function measurementHTML(m) {
  if (!m?.samples) return "此类任务尚无直接质量实测";
  return `内容正确 ${m.content_passed}/${m.content_scored}${m.content_pending ? ` · 待复核 ${m.content_pending}` : ""}<br>严格通过 ${m.strict_passed}/${m.samples}${m.strict_pending ? ` · 待评 ${m.strict_pending}` : ""} · 调用失败 ${m.call_errors}<small>P95 ${ms(m.p95_ms)} · 公开开发测试题，小样本<br>${esc(m.metric_description || "")}</small>`;
}
function publicEvidenceHTML(records, label = "查看公开能力评测") {
  if (!records?.length) return "";
  return `<details class="model-evidence public-benchmarks"><summary>${esc(label)}（${records.length} 条）</summary><p>同型号的外部参考；测试推理档位、端点和输出预算可能不同。分项均分不等于通过率。</p>${records.map(r => {
    const validURL = /^https:\/\/(openrouter\.ai|livebench\.ai)(\/|$)/.test(r.url || "");
    const title = `${r.source === "openrouter" ? "OpenRouter" : "LiveBench"} · ${r.benchmark} · ${r.release}`;
    const source = validURL ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>` : esc(title);
    const scores = Object.entries(r.scores).map(([name, value]) => `${esc(name)} ${Number(value).toFixed(2)}${r.metric === "accuracy_percent" ? "%" : "/100"}（样本 ${esc(r.sample_counts?.[name] ?? "未知")}）`).join("<br>");
    return `<p>${source}<br>测试配置：${esc(r.configuration)}${r.status === "incomplete" ? " · 评测未完成" : ""}<br>${scores}<small>${esc(r.source_model_id)}${r.recorded_at ? " · " + esc(r.recorded_at) : ""}</small></p>`;
  }).join("")}</details>`;
}
function renderTiming() {
  const ft = flow?.timings,
    st = selection?.timings,
    et = execution?.timings;
  const joint = selection?.selection?.decision_mode === "joint";
  const twoStage = selection?.selection?.decision_mode === "two_stage";
  const second = selection?.selection?.decision_calls?.find(c => c.mode === "selection");
  const rows = [
    ["需求判断 · API 排队", ft?.queue_ms],
    ["需求判断 · GPU 往返", ft?.upstream_ms],
    ["需求判断 · 结果校验", ft?.validation_ms],
    ["能力与硬约束筛选", st?.policy_ms],
    ...(twoStage ? [
      ["模型选择 · API 排队", second?.timings?.queue_ms],
      ["模型选择 · GPU 往返", second?.timings?.upstream_ms],
      ["两阶段决策合计（含上项）", st?.model_decision_ms],
    ] : [[joint ? "联合决策总耗时（含上项）" : "GPU 选模总耗时", st?.model_decision_ms]]),
    ["路由凭据存储", st?.receipt_ms],
    ["Bedrock 排队", et?.queue_ms],
    ["Bedrock 调用", et?.upstream_ms],
  ];
  const maximum = Math.max(1, ...rows.map(([, value]) => value || 0));
  $("#timing").innerHTML =
    rows
      .map(
        ([name, value]) =>
          `<div class="timing-row"><span>${name}</span><div class="timing-track">${Number.isFinite(value) ? `<div class="timing-fill" style="width:${Math.max(1, (100 * value) / maximum)}%"></div>` : ""}</div><strong>${ms(value)}</strong></div>`,
      )
      .join("") +
    `<p class="footnote">GPU 服务自报耗时：${ms(ft?.service_reported_ms)}，属于 HTTP 往返的内部观测，不重复累加。${Number.isInteger(ft?.forward_passes) ? `该阶段实际前向计算 ${ft.forward_passes} 次。` : ""}<br>后端总耗时：${ms(completed?.backend_total_ms)}；浏览器等待：${currentMode === "live" ? ms(lastBrowserMs) : "回放不记入性能"}。${joint ? "任务判断与模型选择共享同一次GPU请求；联合决策总耗时包含证据准备和HTTP往返，各项不能重复相加。" : twoStage ? "两阶段决策合计包含两次GPU调用及选模准备；无候选时仅执行需求判断。" : "选模总耗时包含该记录的全部选模调用。"}均不是纯 GPU 计算时间。流式首字时间见回答详情。</p>
    <p class="footnote">GPU ${joint ? "联合决策" : "需求判断"} Token：${flow?.usage ? `${flow.usage.input_tokens} 输入 / ${flow.usage.output_tokens} 输出` : "未提供"}。${twoStage && second ? `模型选择 Token：${second.usage ? `${second.usage.input_tokens} 输入 / ${second.usage.output_tokens} 输出` : "未提供"}；该阶段前向 ${second.timings?.forward_passes ?? "未知"} 次。` : ""}GPU 单请求成本未配置，不计入目标参考费用。</p>`;
}
function renderTrace() {
  $("#trace").innerHTML = events.length
    ? events
        .map(
          (e) =>
            `<div class="trace-row"><span>${e.elapsed_ms.toFixed(1)} ms</span><strong>${esc(e.type === "stage" ? `${stageNames[e.stage]} · ${stateNames[e.status]}` : { accepted: "网关接收请求", route: "路由结果已返回", completed: "请求结束", error: e.data.message }[e.type] || e.type)}</strong></div>`,
        )
        .join("") +
      `<details class="trace-json"><summary>原始事件 JSON（移除执行凭据）</summary><pre>${esc(JSON.stringify(exportRecord(), null, 2))}</pre></details>`
    : '<p class="empty-copy">阶段事件将在请求运行时逐条到达。</p>';
}
function render() {
  renderMetrics();
  renderStages();
  renderDecision();
  renderCandidates();
  renderTiming();
  renderTrace();
}
function renderOutput() {
  if (!execution) return;
  const usage = execution.usage;
  $("#response").innerHTML =
    `<div class="output-meta"><span>${execution.complete ? "已返回完整输出" : "输出未完成"}</span><span>停止原因：${esc(execution.stop_reason || "—")}</span><span>${esc(execution.cache?.total_input_tokens ?? usage?.inputTokens ?? usage?.input_tokens ?? "未知")} 总输入 / ${esc(usage?.outputTokens ?? usage?.output_tokens ?? "未知")} 输出 Token</span>${execution.output_budget ? `<span>本次输出上限 ${esc(execution.output_budget.effective_max_tokens)} / 请求上限 ${esc(execution.output_budget.requested_max_tokens)}</span>` : ""}</div>
    ${execution.cache ? `<p class="footnote">缓存${execution.cache.observed ? `：读 ${execution.cache.read_tokens} / 写 ${execution.cache.write_tokens} Token` : "：尚未收到缓存用量，不能认定命中"} · 首字 ${ms(execution.timings?.ttft_ms)} · 回退 ${execution.fallback_count || 0} 次 · 实际模型 ${esc(execution.model_id)}</p>` : ""}
    <pre class="output">${esc(execution.text ?? JSON.stringify(execution, null, 2))}</pre>
    ${mediaHTML(execution.artifacts)}
    ${execution.media_job ? `<p id="media-job-state">视频任务：${esc(execution.media_job.status)}</p><div id="media-job-output"></div>` : ""}
    <p class="footnote">此状态描述输出是否完整，业务正确性尚需验证。</p>`;
  if (execution.media_job) pollMediaJob(execution.media_job, mediaEpoch);
}
function receive(event) {
  if (terminalSeen) throw new Error("网关在结束后返回了额外事件。");
  if (
    !Number.isInteger(event.sequence) ||
    event.sequence !== events.length + 1 ||
    !Number.isFinite(event.elapsed_ms) ||
    (events.length && event.request_id !== events[0].request_id)
  ) {
    throw new Error("网关事件顺序无效，请重新发起请求。");
  }
  events.push(event);
  $("#request-id").textContent = `REQUEST ID · ${event.request_id}`;
  $("#export").disabled = false;
  if (event.type === "stage") {
    stages[event.stage] = { status: event.status, data: event.data };
    if (event.status === "completed") {
      if (event.stage === "classification") flow = event.data;
      if (event.stage === "selection") selection = event.data;
      if (event.stage === "selection" && event.data.selection?.stickiness)
        $("#conversation-state").textContent += " " + event.data.selection.stickiness.reason;
      if (event.stage === "execution") {
        execution = event.data;
        renderOutput();
      }
    }
  } else if (event.type === "route") {
    route = event.data;
  } else if (event.type === "context") {
    $("#conversation-state").textContent = `带入 ${event.data.history_turns} 轮历史，裁剪 ${event.data.trimmed_turns} 轮。`;
  } else if (event.type === "attempt") {
    if (event.data.status === "started")
      $("#conversation-state").textContent += ` 第 ${event.data.number} 次调用：${event.data.model_id}。`;
    if (event.data.will_fallback)
      $("#conversation-state").textContent += ` ${event.data.error_code}，正在尝试下一候选。`;
  } else if (event.type === "reroute") {
    $("#conversation-state").textContent += ` GPU 已重新选模，耗时 ${ms(event.data.model_decision_ms)}。`;
  } else if (event.type === "delta") {
    let output = $("#stream-output");
    if (!output) {
      $("#response").innerHTML = '<p class="muted">正在生成…</p><pre id="stream-output" class="output" aria-live="polite"></pre>';
      output = $("#stream-output");
    }
    output.textContent += event.data.text;
  } else if (event.type === "completed") {
    terminalSeen = true;
    completed = {
      ...event.data,
      status:
        event.data.status === "executed" && execution?.complete === false
          ? "incomplete"
          : event.data.status,
    };
    if (!execution)
      $("#response").innerHTML =
        `<p class="empty-copy">${route?.status === "no_route" ? "没有符合全部条件的模型，本次未执行。" : currentMode === "replay" ? "流程回放结束，没有调用任何模型。" : "路由已完成，本次仅分析路由。"}</p>`;
  } else if (event.type === "error") {
    terminalSeen = true;
    completed = { status: "error" };
    stages[event.stage] = { status: "failed" };
    showError(event.data.message);
  } else if (event.type !== "accepted") throw new Error("无法识别网关事件。");
  render();
}
function showError(message) {
  $("#request-error").hidden = false;
  $("#request-error").textContent = message;
}
function setBusy(value) {
  busy = value;
  $$(
    "#request-form input, #request-form textarea, #request-form select, #request-form button, #session-select, #new-session, #rename-session",
  ).forEach((el) => {
    el.disabled = value;
  });
  $("#cancel").disabled = false;
  $("#cancel").hidden = !value;
  $("#logout").disabled = value;
}
async function start(executeTarget) {
  if (busy || uploadBusy || !$("#request-form").reportValidity()) return;
  const replay = $("#mode").value === "replay";
  const preferences = {
    cost_tier: $('input[name="tier"]:checked').value,
    expected_output_tokens: Number($("#expected-output").value),
    max_output_tokens: Number($("#max-output").value),
    benchmark_release: $("#benchmark-release").value,
  };
  for (const [id, key] of [
    ["max-cost", "max_reference_cost_usd"],
    ["min-success", "min_success_rate"],
    ["max-latency", "max_latency_ms"],
  ]) {
    if ($(`#${id}`).value !== "")
      preferences[key] =
        key === "max_reference_cost_usd"
          ? $(`#${id}`).value
          : Number($(`#${id}`).value);
  }
  if (preferences.expected_output_tokens > preferences.max_output_tokens) {
    showError("期望输出 Token 不能超过输出上限。");
    return;
  }
  const body = {
    ...workspace.requestFields(),
    template_id: activeTemplateId,
    task: {
      text: $("#task").value,
      allow_cross_region: $("#cross-region").checked,
      routing_preferences: preferences,
      attachments: mediaInputs.map(({ id, kind }) => ({ id, kind })),
      output_modality: $("#output-modality").value,
      media_options: {
        image_operation: $("#image-operation").value, voice: $("#speech-voice").value,
        edit_target: $("#edit-target").value,
        documents: $("#rerank-documents").value.split("\n").map((v) => v.trim()).filter(Boolean),
      },
    },
    provider: $("#provider").value,
    mode: replay ? "replay" : "live",
    execute_target: executeTarget && !replay,
    catalog_version: catalog.catalog_version,
    ranking_version: catalog.ranking_version,
    policy_version: catalog.policy_version,
  };
  currentMode = body.mode;
  resetRun();
  setBusy(true);
  renderStages();
  startTime = performance.now();
  controller = new AbortController();
  clockTimer = setInterval(() => {
    $("#wall-clock").textContent =
      `已等待 ${((performance.now() - startTime) / 1000).toFixed(1)} s`;
  }, 100);
  try {
    const response = await fetch("/api/gateway/runs", {
      method: "POST",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Decider-Client": "web",
      },
      body: JSON.stringify(body),
    });
    if (response.status === 401) showLogin();
    if (!response.ok) throw new Error(errorMessage(await response.json()));
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let pending = "";
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let offset;
      while ((offset = pending.indexOf("\n")) >= 0) {
        const line = pending.slice(0, offset);
        pending = pending.slice(offset + 1);
        if (line.trim()) receive(JSON.parse(line));
      }
      if (done) break;
    }
    if (pending.trim()) receive(JSON.parse(pending));
    if (!terminalSeen)
      throw new Error("连接在请求完成前中断；请查看已到达的阶段记录。");
  } catch (error) {
    controller.abort();
    completed = { status: error.name === "AbortError" ? "cancelled" : "error" };
    showError(
      error.name === "AbortError"
        ? "已停止等待；已经提交的模型调用可能仍会执行并产生用量。"
        : error.message,
    );
    Object.values(stages).forEach((stage) => {
      if (stage.status === "started") stage.status = "failed";
    });
  } finally {
    clearInterval(clockTimer);
    lastBrowserMs = performance.now() - startTime;
    $("#wall-clock").textContent =
      `${replay ? "回放结束" : "浏览器等待"} · ${(lastBrowserMs / 1000).toFixed(2)} s`;
    setBusy(false);
    history.unshift({
      id: events[0]?.request_id || "未建立请求",
      mode: currentMode,
      status: completed?.status || "error",
      model: selection?.selected_model?.name || "—",
      route_ms: route?.route_total_ms,
      total_ms: completed?.backend_total_ms,
      gpu_ms: flow?.timings?.upstream_ms,
      target_ms: execution?.backend_total_ms,
      reference_cost: execution?.observed_reference_cost_usd,
    });
    history = history.slice(0, 50);
    render();
    renderHistory();
  }
}
function exportRecord() {
  return JSON.parse(
    JSON.stringify(
      {
        exported_at: new Date().toISOString(),
        mode: currentMode,
        events,
        browser_wait_ms: currentMode === "live" ? lastBrowserMs : null,
        note: "参考费用非实际账单；回放不属于模型性能。",
      },
      (key, value) => (key === "route_token" ? undefined : value),
    ),
  );
}
function renderModels() {
  if (!catalog) return;
  const category = catalog.categories[$("#category").value];
  const evidence = catalog.evidence_inventory.models;
  const filter = $("#evidence-filter").value;
  const search = $("#model-search").value.trim().toLowerCase();
  const rows = catalog.models.filter((m) => {
    const row = evidence[m.id];
    return category.available_model_ids.includes(m.id) &&
      `${m.name} ${m.id} ${m.provider}`.toLowerCase().includes(search) &&
      (filter === "all" || row.status === filter ||
        (filter === "benchmarked" && row.public_benchmarks?.length) ||
        (filter === "unmapped" && !row.configurations.length && !row.public_benchmarks?.length));
  });
  const quotaBlocked = catalog.models.filter((m) =>
    m.inference_quotas?.checks.some((q) => q.value === 0),
  );
  $("#model-table").innerHTML =
    `<table><thead><tr><th>模型</th><th>供应商</th><th>资料与评测依据</th><th>执行标识</th></tr></thead><tbody>${rows.map((m) => `<tr data-model-id="${esc(m.id)}"><td>${esc(m.name)}<small>${esc(m.id)}</small></td><td>${esc(m.provider)}</td><td>${modelEvidence(evidence[m.id])}${pricingHTML(evidence[m.id].bedrock_evaluation?.pricing, esc)}</td><td><code>${esc(m.invoke_id)}</code></td></tr>`).join("")}</tbody></table><p class="footnote" style="padding:0 16px">筛选显示 ${rows.length} 个型号。实际请求按能力、账户、预算和会话检查候选，再由 GPU 读取评测选择；自动模式不因评测批次不同而排除型号。</p>` +
    (quotaBlocked.length
      ? `<details class="excluded"><summary>账户零配额模型（${quotaBlocked.length}）</summary>${quotaBlocked.map((m) => `<p><strong>${esc(m.name)}</strong><br><code>${esc(m.invoke_id)}</code><br>核验时间：${esc(m.inference_quotas.checked_at)}<br>${m.inference_quotas.checks.filter((q) => q.value === 0).map((q) => `${esc(q.name)}：0`).join("<br>")}<br>配额恢复并刷新目录后可重新参与选模。</p>`).join("")}</details>`
      : "");
}
function modelEvidence(row) {
  const direct = row.bedrock_evaluation;
  const directHTML = direct ? `<strong>Bedrock 端点${({measured:"已补测",blocked:"调用受限",unmeasured:"待补测"})[direct.status] || direct.status}</strong><small>${esc(direct.reason || "接口完成不等于内容正确，分项见下方")}</small><details class="model-evidence"><summary>查看直接实测与历史评分</summary><p class="footnote">旧文本答题分数不参与当前选模；保留运行延迟、接口兼容和多模态专项依据。</p>${Object.entries(direct.measurements || {}).map(([k,v]) => `<p><strong>${esc(k)}</strong><br>${measurementHTML(v)}</p>`).join("")}${Object.keys(direct.errors || {}).length ? `<p>错误记录：${esc(JSON.stringify(direct.errors))}</p>` : ""}<p><a href="https://github.com/milan9527/DeciderModelRouter/blob/main/validation/independent_gateway/bedrock-evidence-2026-11-26/README.zh.md" target="_blank" rel="noopener noreferrer">评测方法与完整记录 ↗</a></p></details>` : "";
  const labels = {
    benchmarked: "已映射评测",
    public_reference: "已接入公开能力评测",
    configuration_pending: "已映射 · 配置待核验",
    reference_only: "型号资料已核验 · 待评测",
    capacity_variant: "容量变体 · 待独立评测",
    specialized: "待专项评测",
    unmapped: "待补充公开评测",
  };
  const configs = row.configurations;
  const reference = row.reference;
  const refHtml = reference
    ? `<p><a href="${esc(reference.url)}" target="_blank" rel="noopener noreferrer">外部型号与价格来源 ↗</a><br>每百万 Token：输入 ${usd(reference.input_price)} / 输出 ${usd(reference.output_price)}<br>此资料不证明 Bedrock 能力或业务成功率。</p>`
    : "";
  return `${directHTML}${publicEvidenceHTML(row.public_benchmarks)}<strong>外部参考：${esc(labels[row.status])}</strong><small>${esc(row.reason)}</small>${
    configs.length || reference
      ? `<details class="model-evidence"><summary>查看来源与分项（${configs.length} 个评测配置）</summary>${configs.map((c) => `<p><a href="${esc(c.source_url)}" target="_blank" rel="noopener noreferrer">${esc(c.source)} ${esc(c.release || "")} ↗</a><br>${esc(c.benchmark_configuration)}<br>${Object.entries(c.category_scores || c.subsets || {}).map(([k, v]) => `${esc(k)} ${v == null ? "未知" : (Number(v) * (c.source === "mteb" ? 100 : 1)).toFixed(2)}`).join(" · ")} ${c.source === "mteb" ? "（nDCG@10 × 100）" : "（0–100）"}<br>${esc(c.configuration_note || "同一数据集内比较")}</p>`).join("")}${refHtml}</details>`
      : ""
  }`;
}
function percentile(values, p) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  return sorted.length
    ? sorted[Math.max(0, Math.ceil(sorted.length * p) - 1)]
    : null;
}
function renderHistory() {
  if (workspace.enabled) {
    workspace.refreshHistory().catch((error) => workspace.notify(error.message));
    return;
  }
  const live = history.filter((r) => r.mode === "live"),
    routed = live.filter((r) => Number.isFinite(r.route_ms));
  const executed = live.filter((r) => r.status === "executed");
  $("#session-metrics").innerHTML = [
    metric(
      "实时请求",
      String(live.length),
      `${history.length - live.length} 次回放不计入`,
      "↗",
    ),
    metric(
      "路由 P50",
      ms(
        percentile(
          routed.map((r) => r.route_ms),
          0.5,
        ),
      ),
      `${routed.length} 个已返回路由样本`,
      "◷",
    ),
    metric(
      "路由 P95",
      ms(
        percentile(
          routed.map((r) => r.route_ms),
          0.95,
        ),
      ),
      "小样本仅描述当前会话",
      "◷",
    ),
    metric(
      "完整执行",
      String(executed.length),
      "输出完整，不代表业务正确",
      "✓",
    ),
  ].join("");
  $("#history").innerHTML = history.length
    ? `<table><thead><tr><th>请求</th><th>状态</th><th>目标模型</th><th>GPU 往返</th><th>路由</th><th>目标调用</th><th>参考费用</th></tr></thead><tbody>${history.map((r) => `<tr><td><code>${esc(r.id)}</code><small>${r.mode === "replay" ? "流程回放" : "实时调用"}</small></td><td>${esc(statuses[r.status])}</td><td>${esc(r.model)}</td><td>${ms(r.gpu_ms)}</td><td>${ms(r.route_ms)}</td><td>${ms(r.target_ms)}</td><td>${r.reference_cost == null ? "—" : usd(r.reference_cost)}</td></tr>`).join("")}</tbody></table>`
    : '<p class="empty-copy">还没有请求。完成一次实时路由后，性能数据将显示在这里。</p>';
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-submit").disabled = true;
  $("#login-error").textContent = "";
  try {
    await enter(
      await api("/auth/login", Object.fromEntries(new FormData(event.target))),
    );
  } catch (error) {
    $("#login-error").textContent = error.message;
  } finally {
    $("#login-submit").disabled = false;
  }
});
$("#logout").addEventListener("click", async () => {
  try {
    await api("/auth/logout", {});
    showLogin();
    resetRun();
    $("#task").value = "";
  } catch (error) {
    showError(error.message);
  }
});
$("#request-form").addEventListener("submit", (event) => {
  event.preventDefault();
  start(true);
});
$("#route-only").addEventListener("click", () => start(false));
$("#cancel").addEventListener("click", () => controller?.abort());
$("#health").addEventListener("click", checkHealth);
$("#provider").addEventListener("change", checkHealth);
$("#mode").addEventListener("change", updateMode);
$("#category").addEventListener("change", renderModels);
$("#evidence-filter").addEventListener("change", renderModels);
$("#model-search").addEventListener("input", renderModels);
$("#output-modality").addEventListener("change", updateMediaOptions);
$("#image-operation").addEventListener("change", updateMediaOptions);
$("#media-upload").addEventListener("change", uploadMedia);
$("#media-inputs").addEventListener("click", (event) => {
  const index = event.target.dataset.removeMedia;
  if (index !== undefined && !busy) { mediaInputs.splice(Number(index), 1); renderMediaInputs(); }
});
$("#task").addEventListener("input", () => {
  activeTemplateId = null;
  $("#template-rubric").textContent = "自由输入按实际内容分类，无需匹配场景模板。";
  $("#char-count").textContent = `${$("#task").value.length} / 1400`;
});
$("#template-category").addEventListener("change", renderTemplates);
$("#template-search").addEventListener("input", renderTemplates);
$("#examples").addEventListener("click", (event) => {
  if (event.target.dataset.example) setExample(event.target.dataset.example);
});
$$("[data-tab]").forEach((button) =>
  button.addEventListener("click", () => {
    $$("[data-tab]").forEach((el) =>
      el.setAttribute("aria-selected", String(el === button)),
    );
    $$("[data-tabpanel]").forEach((el) => {
      el.hidden = el.dataset.tabpanel !== button.dataset.tab;
    });
  }),
);
$("#export").addEventListener("click", () => {
  const link = document.createElement("a"),
    url = URL.createObjectURL(
      new Blob([JSON.stringify(exportRecord(), null, 2)], {
        type: "application/json",
      }),
    );
  link.href = url;
  link.download = `gateway-${events[0]?.request_id || "request"}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener("hashchange", setPage);
render();
renderHistory();
api("/auth/me")
  .then(enter)
  .catch((error) => {
    if (!error.message.includes("登录"))
      $("#login-error").textContent = error.message;
  });
import { Workspace, mediaHTML } from "./workspace.js";
