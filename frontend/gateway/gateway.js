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
  classification: "GPU 任务分类",
  selection: "策略选模",
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
  decider: "Strands Decider · 2B",
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
  events = [];
  stages = {};
  flow = selection = route = execution = completed = null;
  lastBrowserMs = null;
  terminalSeen = false;
  $("#request-error").hidden = true;
  $("#response").innerHTML =
    '<p class="empty-copy">执行后，模型的实际输出将显示在这里。</p>';
  $("#decision").innerHTML =
    '<div class="empty-state"><div class="route-art" aria-hidden="true"><span>任务</span><i></i><b>◈</b><i></i><span>模型</span></div><h3>等待任务分类</h3><p>GPU 分类结果与程序选模依据将依次显示。</p></div>';
  $("#request-id").textContent = "REQUEST ID · —";
  $("#wall-clock").textContent = "等待开始";
  $("#export").disabled = true;
  render();
}
function showLogin() {
  controller?.abort();
  history = [];
  $("#app").hidden = true;
  $("#auth").hidden = false;
}
async function enter(user) {
  catalog = await api("/gateway/catalog");
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
  $("#examples").innerHTML = catalog.examples
    .map(
      (e) =>
        `<button type="button" data-example="${esc(e.id)}">${esc(e.title)} ↗</button>`,
    )
    .join("");
  $("#category").innerHTML = Object.entries(catalog.categories)
    .map(
      ([key, value]) =>
        `<option value="${esc(key)}">${esc(value.label)}</option>`,
    )
    .join("");
  $("#catalog-summary").textContent =
    `${catalog.models.length} 个 Bedrock 型号 · ${catalog.evidence_inventory.mapped_models} 个已有精确评测映射 · ${catalog.evidence_inventory.counts.configuration_pending || 0} 个评测配置待核验。资料和价格不替代能力成绩；零配额端点不参与选模。`;
  $("#benchmark-release").innerHTML =
    '<option value="auto">自动 · 优先可比型号覆盖数</option>' +
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
  if (!$("#task").value) setExample("code");
  checkHealth();
}
function setPage() {
  const page = ["workspace", "models", "performance"].includes(
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
    models: "Bedrock 模型",
    performance: "会话性能",
  }[page];
  if (page === "performance") renderHistory();
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
  $("#metrics").innerHTML = [
    metric(
      "GPU 分类往返",
      ms(flow?.timings?.upstream_ms),
      "API → GPU 服务 → API",
      "◈",
    ),
    metric(
      "路由总耗时",
      ms(route?.route_total_ms),
      "目录、分类、策略与路由凭据",
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
    ${s ? `<div class="reason">${esc(s.tier_reason)} ${esc(s.reason)}</div><div class="selection-meta"><span>${s.candidate_count} 个合格配置</span><span>${s.frontier.length} 个非劣候选</span><span>${s.evidence_mode === "business_validation" ? "业务验证数据" : "榜单能力代理 · 待业务校准"}</span></div>${s.benchmark_group ? `<p class="footnote">本轮评测：${esc(s.benchmark_group.source)} ${esc(s.benchmark_group.release || "")} · ${s.benchmark_group.metrics.map(esc).join(" / ")}。其他批次分别比较。</p>` : ""}` : ""}
    </div>`;
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
  $("#candidates").innerHTML =
    `<p class="footnote">本轮依据：${esc(s.evidence_mode)} · ${esc(s.cost_mode || "暂无可比费用")} · ${esc(s.benchmark_group?.release || "")}。非劣候选表示没有另一配置同时在所有比较项更好。能力分项不是业务成功率。</p>
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
            )}</td><td>${r.candidate_id === s.selected_candidate_id ? "✓ 已选择" : s.frontier.includes(r.candidate_id) ? "非劣候选" : "被其他配置支配"}</td></tr>`,
      )
      .join("")}
    </tbody></table></div><p class="footnote">输入估计 ${s.cost_context.input_tokens} Token（${s.cost_context.input_token_source === "utf8_bytes_proxy" ? "UTF-8 字节代理，并非模型分词" : "调用方估计"}）；期望输出 ${s.cost_context.expected_output_tokens}，上限 ${s.cost_context.max_output_tokens}。费用按快照参考价计算，不含 GPU、缓存和平台折扣。</p>
    <details class="excluded"><summary>查看排除记录（${Object.keys(s.excluded).length}）</summary>${Object.entries(
      s.excluded,
    )
      .map(([k, v]) => `<p><code>${esc(k)}</code><br>${esc(v)}</p>`)
      .join("")}</details>`;
}
function renderTiming() {
  const ft = flow?.timings,
    st = selection?.timings,
    et = execution?.timings;
  const rows = [
    ["API 分类排队", ft?.queue_ms],
    ["GPU HTTP 往返", ft?.upstream_ms],
    ["分类结果校验", ft?.validation_ms],
    ["策略计算", st?.policy_ms],
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
    `<p class="footnote">GPU 服务自报耗时：${ms(ft?.service_reported_ms)}，可能含服务端排队，属于 HTTP 往返的内部观测，不重复累加。<br>后端总耗时：${ms(completed?.backend_total_ms)}；浏览器等待：${currentMode === "live" ? ms(lastBrowserMs) : "回放不记入性能"}。当前接口返回完整模型响应，未测量 TTFT、纯 GPU 计算时间或生成速率。</p>
    <p class="footnote">GPU 分类 Token：${flow?.usage ? `${flow.usage.input_tokens} 输入 / ${flow.usage.output_tokens} 输出` : "未提供"}。GPU 单请求成本未配置，不计入目标参考费用。</p>`;
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
    `<div class="output-meta"><span>${execution.complete ? "已返回完整输出" : "输出未完成"}</span><span>停止原因：${esc(execution.stop_reason || "—")}</span><span>${esc(usage?.inputTokens ?? usage?.input_tokens ?? "未知")} 输入 / ${esc(usage?.outputTokens ?? usage?.output_tokens ?? "未知")} 输出 Token</span></div>
    <pre class="output">${esc(execution.text ?? JSON.stringify(execution, null, 2))}</pre>
    <p class="footnote">此状态描述输出是否完整，业务正确性尚需验证。</p>`;
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
      if (event.stage === "execution") {
        execution = event.data;
        renderOutput();
      }
    }
  } else if (event.type === "route") {
    route = event.data;
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
    "#request-form input, #request-form textarea, #request-form select, #request-form button",
  ).forEach((el) => {
    el.disabled = value;
  });
  $("#cancel").disabled = false;
  $("#cancel").hidden = !value;
  $("#logout").disabled = value;
}
async function start(executeTarget) {
  if (busy || !$("#request-form").reportValidity()) return;
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
    task: {
      text: $("#task").value,
      allow_cross_region: $("#cross-region").checked,
      routing_preferences: preferences,
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
        (filter === "unmapped" && !row.configurations.length));
  });
  const quotaBlocked = catalog.models.filter((m) =>
    m.inference_quotas?.checks.some((q) => q.value === 0),
  );
  $("#model-table").innerHTML =
    `<table><thead><tr><th>模型</th><th>供应商</th><th>资料与评测依据</th><th>执行标识</th></tr></thead><tbody>${rows.map((m) => `<tr data-model-id="${esc(m.id)}"><td>${esc(m.name)}<small>${esc(m.id)}</small></td><td>${esc(m.provider)}</td><td>${modelEvidence(evidence[m.id])}</td><td><code>${esc(m.invoke_id)}</code></td></tr>`).join("")}</tbody></table><p class="footnote" style="padding:0 16px">筛选显示 ${rows.length} 个型号；该类别共有 ${category.configuration_count} 个配置通过基础筛选（含不同评测批次）。具体请求只在一个可比批次内选模，还需通过语言、费用和账户额度检查。</p>` +
    (quotaBlocked.length
      ? `<details class="excluded"><summary>账户零配额模型（${quotaBlocked.length}）</summary>${quotaBlocked.map((m) => `<p><strong>${esc(m.name)}</strong><br><code>${esc(m.invoke_id)}</code><br>核验时间：${esc(m.inference_quotas.checked_at)}<br>${m.inference_quotas.checks.filter((q) => q.value === 0).map((q) => `${esc(q.name)}：0`).join("<br>")}<br>配额恢复并刷新目录后可重新参与选模。</p>`).join("")}</details>`
      : "");
}
function modelEvidence(row) {
  const labels = {
    benchmarked: "已映射评测",
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
  return `<strong>${esc(labels[row.status])}</strong><small>${esc(row.reason)}</small>${
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
$("#task").addEventListener("input", () => {
  $("#char-count").textContent = `${$("#task").value.length} / 1400`;
});
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
