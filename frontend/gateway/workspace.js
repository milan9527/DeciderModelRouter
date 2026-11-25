const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const date = (v) => new Date(v).toLocaleString("zh-CN", { hour12: false });
const labels = { running: "处理中", executed: "已完成", selected: "已选模", no_route: "无候选", incomplete: "输出未完成", error: "失败", interrupted: "已中断", completed: "已完成" };

export function mediaHTML(items = []) {
  return items.filter((a) => /^[a-f0-9]{32}$/.test(a.id) && ["image", "audio", "video"].includes(a.kind)).map((a) => {
    const url = `/api/gateway/media/${a.id}`;
    const preview = a.kind === "image" ? `<img src="${url}" alt="任务图片" loading="lazy" />` :
      `<${a.kind} controls preload="metadata" src="${url}"></${a.kind}>`;
    return `<figure class="media-artifact">${preview}<figcaption><a href="${url}" download>下载${{ image: "图片", audio: "音频", video: "视频" }[a.kind]}</a></figcaption></figure>`;
  }).join("");
}

export class Workspace {
  constructor({ api, esc, ms, usd, reuse, signedOut }) {
    Object.assign(this, { api, esc, ms, usd, reuse, signedOut, enabled: false, epoch: 0, historyRequest: 0 });
    this.bind();
  }
  notify(message) {
    $("#notification").textContent = message;
    $("#notification").hidden = false;
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => { $("#notification").hidden = true; }, 6000);
  }
  async action(action, button) {
    if (button) button.disabled = true;
    try { await action(); } catch (error) { this.notify(error.message); }
    finally { if (button) button.disabled = false; }
  }
  reset() {
    this.epoch++;
    this.enabled = false;
    this.sessionId = null;
    this.rows = [];
    this.selectedRecord = null;
    clearTimeout(this.pollTimer);
    clearTimeout(this.effectPollTimer);
    ["#history-detail", "#session-dialog"].forEach((id) => $(id).close());
    ["#history", "#conversation-thread", "#evaluation-result", "#evaluation-history", "#effect-result", "#effect-history", "#users-table", "#history-detail-content"].forEach((id) => { $(id).innerHTML = ""; });
    this.effect = null;
    $("#effect-form").reset();
    $("#password-form").reset();
    $("#user-form").reset();
    $("#reset-password-form").reset();
  }
  async enter(user, catalog) {
    this.reset();
    this.enabled = catalog.persistent_history;
    $$("[data-persistent]").forEach((el) => { el.hidden = !this.enabled; });
    if (!this.enabled) return;
    const epoch = this.epoch;
    this.user = user;
    this.catalog = catalog;
    const [account, sessions, cases, effects] = await Promise.all([
      this.api("/gateway/account"), this.api("/gateway/sessions"), this.api("/gateway/evaluations/cases"),
      this.api("/gateway/effects/cases"),
    ]);
    if (epoch !== this.epoch) return;
    this.account = account;
    this.sessions = sessions.items;
    if (!this.sessions.length) {
      const first = await this.api("/gateway/sessions", { title: "我的第一个会话" });
      if (epoch !== this.epoch) return;
      this.sessions = [first];
    }
    const remembered = sessionStorage.getItem("router-session-" + user.username);
    this.sessionId = this.sessions.find((s) => s.id === remembered)?.id || this.sessions[0].id;
    this.renderSessions();
    this.cases = cases.items;
    this.renderEvaluationForm();
    this.effectSuite = effects;
    this.renderEffectForm();
    $("#record-storage-note").textContent = "完整记录已接入数据库，可在历史与性能中查看";
    $("#history-description").textContent = "按账户保存最近 180 天的会话和运行记录。刷新页面或重新登录后仍可查看；回放不计入模型性能。";
    $("#account-description").textContent = `${user.username} · ${account.role === "admin" ? "管理员" : "普通用户"}`;
    $("#admin-panel").hidden = $("#users-panel").hidden = account.role !== "admin";
    await this.refreshHistory();
    this.page(location.hash.slice(1));
  }
  renderSessions() {
    $("#session-select").innerHTML = this.sessions.map((s) => `<option value="${this.esc(s.id)}">${this.esc(s.title)}</option>`).join("");
    $("#session-select").value = this.sessionId;
    sessionStorage.setItem("router-session-" + this.user.username, this.sessionId);
  }
  requestFields() {
    return this.enabled ? { session_id: this.sessionId, client_request_id: crypto.randomUUID(),
      session: { use_history: $("#use-history").checked, stickiness: $("#use-stickiness").checked,
        prompt_cache: $("#use-cache").checked, stream: $("#use-stream").checked,
        fallback: $("#use-fallback").checked, max_history_turns: Number($("#history-turns").value) },
    } : {};
  }
  async refreshConversation() {
    const epoch = this.epoch, session = this.sessionId;
    const page = await this.api("/gateway/history?session_id=" + session + "&limit=16&days=180");
    const records = await Promise.all(page.items.map((r) => this.api("/gateway/history/" + r.id)));
    if (epoch !== this.epoch || session !== this.sessionId) return;
    $("#conversation-thread").innerHTML = records.reverse().map((r) => {
      const result = r.events.find((e) => e.stage === "execution" && e.status === "completed")?.data;
      const partial = r.events.filter((e) => e.type === "delta").map((e) => e.data.text).join("");
      return `<article class="conversation-turn"><p><strong>你</strong> · ${this.esc(date(r.created_at))}</p><pre>${this.esc(r.request.task.text)}</pre>${mediaHTML(r.request.task.attachments)}
        <p><strong>${this.esc(r.model || "网关")}</strong> · ${this.esc(labels[r.status] || r.status)}</p>
        <pre>${this.esc(result?.text || partial || r.error?.message || "本轮没有完成的目标回答")}</pre>${mediaHTML(result?.artifacts)}</article>`;
    }).join("") || '<p class="empty-copy">此会话还没有消息。</p>';
  }
  page(page) {
    if (!this.enabled) return;
    if (page === "performance") this.action(() => this.refreshHistory());
    if (page === "evaluations") this.action(() => Promise.all([this.refreshEvaluations(), this.refreshEffects()]));
    if (page === "account" && this.account?.role === "admin") this.action(() => this.refreshUsers());
  }
  query() {
    const p = new URLSearchParams({ days: $("#history-days").value });
    if ($("#history-scope").value === "session") p.set("session_id", this.sessionId);
    return p;
  }
  metric(label, value, note) {
    return `<div class="metric"><div class="metric-label">${this.esc(label)}</div><div class="metric-value">${this.esc(value)}</div><small>${this.esc(note)}</small></div>`;
  }
  async refreshHistory(more = false) {
    if (!this.enabled) return;
    const requestNumber = ++this.historyRequest;
    const epoch = this.epoch, query = this.query(), scope = query.toString();
    if (more && this.nextCursor) query.set("cursor", this.nextCursor);
    const [history, stats] = await Promise.all([
      this.api("/gateway/history?" + query), this.api("/gateway/performance?" + this.query()),
    ]);
    await this.refreshConversation();
    if (epoch !== this.epoch || scope !== this.query().toString() || requestNumber !== this.historyRequest) return;
    this.rows = more ? [...this.rows, ...history.items] : history.items;
    this.nextCursor = history.next_cursor;
    $("#more-history").hidden = !history.next_cursor;
    $("#session-metrics").innerHTML = [
      this.metric("实时请求", stats.live_requests, `${stats.replays} 次回放不计入性能`),
      this.metric("路由 P50", this.ms(stats.route_p50_ms), "已返回路由样本"),
      this.metric("路由 P95", this.ms(stats.route_p95_ms), "所选范围 · 最近秩法"),
      this.metric("完整执行", stats.completed, `${stats.failed} 次失败/无候选/中断`),
      this.metric("后端总耗时 P95", this.ms(stats.total_p95_ms), "含已记录的失败请求"),
      this.metric("已知参考费用", this.usd(stats.observed_reference_cost_usd), `${stats.cost_known_samples} 次已知 · ${stats.cost_unknown_samples} 次未知`),
      this.metric("人工业务验收", `${stats.business_passes} / ${stats.business_reviews}`, "通过数 / 已验收数"),
      this.metric("处理中", stats.running, "运行详情可查看已保存阶段"),
      this.metric("带历史 / 沿用模型", `${stats.context_requests || 0} / ${stats.sticky_requests || 0}`, "真实请求数，仍逐轮分类"),
      this.metric("缓存命中", stats.cache_hit_requests || 0, `${stats.cache_observed_requests || 0} 次收到缓存用量；读 ${stats.cache_read_tokens || 0} / 写 ${stats.cache_write_tokens || 0} Token`),
      this.metric("目标首字 P50", this.ms(stats.ttft_p50_ms), "从目标请求发起至首个文本片段"),
      this.metric("发生回退", stats.fallback_requests || 0, "未收到输出且遇到可回退错误"),
    ].join("");
    $("#performance-note").textContent = stats.note + (stats.truncated ? " 当前统计达到 5000 条上限，请缩小时间范围。" : " 小样本分位数仅描述当前样本。");
    const peak = Math.max(1, ...stats.models.map((m) => m.requests));
    $("#model-performance").innerHTML = `<div class="panel-heading"><h2>按目标模型比较</h2><span class="tiny">失败和未选模请求也保留</span></div><div class="model-bars">${stats.models.map((m) => `<div class="model-bar"><code>${this.esc(m.model_id)}</code><div class="bar-track"><i style="width:${m.requests / peak * 100}%"></i></div><span>${m.requests} 次 · 完成 ${m.completed} · 目标 P50 ${this.ms(m.target_p50_ms)} / P95 ${this.ms(m.target_p95_ms)}（${m.latency_samples} 样本）</span></div>`).join("") || '<p class="empty-copy">此范围内还没有实时请求。</p>'}</div>`;
    $("#history").innerHTML = this.rows.length ? `<table><thead><tr><th>时间 / 请求</th><th>任务</th><th>状态</th><th>目标模型</th><th>GPU 往返</th><th>路由 / 目标</th><th>参考费用</th></tr></thead><tbody>${this.rows.map((r) => `<tr><td><button class="text-button" data-run="${this.esc(r.id)}">${this.esc(date(r.created_at))}</button><small>${this.esc(r.id)}</small></td><td class="task-preview">${this.esc(r.task_preview)}<small>${r.mode === "live" ? "实时调用" : "流程回放"}</small></td><td>${this.esc(labels[r.status] || r.status)}${r.review ? `<small>人工验收：${r.review.passed ? "通过" : "未通过"}</small>` : ""}</td><td>${this.esc(r.model || "—")}</td><td>${this.ms(r.gpu_ms)}</td><td>${this.ms(r.route_ms)} / ${this.ms(r.target_ms)}</td><td>${r.reference_cost == null ? "未知" : this.usd(r.reference_cost)}</td></tr>`).join("")}</tbody></table>` : '<p class="empty-copy">此范围内没有保存的请求。</p>';
  }
  async openRecord(id) {
    const epoch = this.epoch;
    const record = await this.api("/gateway/history/" + encodeURIComponent(id));
    if (epoch !== this.epoch) return;
    this.selectedRecord = record;
    const result = record.events.find((e) => e.stage === "execution" && e.status === "completed")?.data;
    const fields = record.profile ? Object.entries(record.profile).map(([k, v]) => `${k}: ${v}`).join(" · ") : "尚未获得完整分类";
    $("#history-detail-content").innerHTML = `<p class="muted">${this.esc(date(record.created_at))} · ${this.esc(labels[record.status] || record.status)} · ${this.esc(record.id)}</p><h3>任务</h3><pre>${this.esc(record.request.task.text)}</pre><h3>分类与选模</h3><p>${this.esc(fields)}</p><p>${this.esc(record.model_id || "未选出目标模型")}</p><p>GPU ${this.ms(record.gpu_ms)} · 路由 ${this.ms(record.route_ms)} · 目标 ${this.ms(record.target_ms)} · 参考费用 ${this.usd(record.reference_cost)}</p><h3>输出</h3><pre>${this.esc(result?.text || (result?.dimensions ? `向量维度：${result.dimensions}` : record.error?.message || "没有已保存的目标输出"))}</pre>${record.execution_outcome_unknown ? '<p class="error">连接中断时执行已提交，最终结果和用量未知；不能据此认定未产生费用。</p>' : ""}<details><summary>查看已保存的 ${record.events.length} 个事件</summary><pre>${this.esc(JSON.stringify(record.events, null, 2))}</pre></details>`;
    $("#history-detail-content").insertAdjacentHTML("beforeend",
      mediaHTML(result?.artifacts) + (record.request.task.attachments?.length ? "<h3>输入附件</h3>" + mediaHTML(record.request.task.attachments) : ""));
    if (result?.media_job?.id) {
      const job = await this.api("/gateway/media/jobs/" + encodeURIComponent(result.media_job.id));
      if (epoch !== this.epoch) return;
      $("#history-detail-content").insertAdjacentHTML("beforeend", `<p>视频任务：${this.esc(job.status)}</p>${mediaHTML(job.artifacts)}`);
    }
    $("#review-form").hidden = record.mode !== "live" || !record.execute_target || !["executed", "incomplete", "error"].includes(record.status);
    const template = this.catalog.examples.find((c) => c.id === record.template_id);
    $("#review-rubric").value = record.review?.rubric || template?.rubric?.join("\n") || "根据原始任务约束和独立业务标准逐项核验";
    $("#review-notes").value = record.review?.notes || "";
    $("#review-passed").value = String(record.review?.passed ?? true);
    $("#history-detail").showModal();
  }
  download(value, name) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  renderEvaluationForm() {
    $("#evaluation-providers").innerHTML = this.catalog.decision_service.providers.map((p) => `<label class="check"><input type="checkbox" name="eval-provider" value="${this.esc(p.id)}" ${p.configured && p.id === this.catalog.decision_service.default_provider ? "checked" : ""} ${p.configured ? "" : "disabled"} />${this.esc(p.model)}</label>`).join("");
    $("#evaluation-cases").innerHTML = this.cases.map((c, i) => `<label class="check"><input type="checkbox" name="eval-case" value="${this.esc(c.id)}" ${i < 4 ? "checked" : ""} /><span>${this.esc(c.title)}<small>${this.esc(c.category)}</small></span></label>`).join("");
    this.budget();
  }
  renderEffectForm() {
    const cases = this.effectSuite.items, families = [...new Set(cases.map((c) => c.family))];
    $("#effect-scope").textContent = `${cases.length} 个场景 · ${families.length} 类 · ${cases.filter((c) => c.gap).length} 个已标注缺口。可增加自定义任务；此目录不声称穷尽所有输入。`;
    $("#effect-family").innerHTML = '<option value="">全部场景</option>' + families.map((f) => `<option>${this.esc(f)}</option>`).join("");
    $("#effect-providers").innerHTML = this.catalog.decision_service.providers.map((p) => `<label class="check"><input type="checkbox" name="effect-provider" value="${this.esc(p.id)}" ${p.configured ? "checked" : "disabled"} />${this.esc(p.id)}</label>`).join("");
    $("#effect-cases").innerHTML = cases.map((c, i) => `<div data-effect-family="${this.esc(c.family)}"><label class="check"><input type="checkbox" name="effect-case" value="${this.esc(c.id)}" ${i < 4 ? "checked" : ""} /><span>${this.esc(c.title)}<small>${this.esc(c.family)} · ${c.gap ? "能力/评测缺口" : c.judge.kind === "manual" ? "需人工评分" : "明确答案校验"}</small></span></label><details><summary>查看任务与验收依据</summary><p>${this.esc(c.task.text)}</p><p class="footnote">${this.esc(c.gap || c.judge.rubric)}</p>${c.judge.expected !== null ? `<pre>${this.esc(JSON.stringify(c.judge.expected))}</pre>` : ""}</details></div>`).join("");
    this.effectBudget();
  }
  effectBudget() {
    const ids = $$('input[name="effect-case"]:checked').map((e) => e.value);
    const custom = Number(Boolean($("#effect-custom").value.trim()));
    const providers = $$('input[name="effect-provider"]:checked').length;
    const cases = this.effectSuite?.items.filter((c) => ids.includes(c.id) && !c.gap).length || 0;
    $("#effect-budget").textContent = `已选 ${ids.length + custom} 个场景，计划最多 ${(cases + custom) * providers} 次 GPU 调用；目标调用最多 ${$("#effect-call-cap").value} 次，每次最多 ${$("#effect-token-cap").value} 输出 tokens。缺口不调用模型，达到上限的任务记为未测。费用含未知项，调用上限不是美元预算。`;
    $("#effect-start").disabled = !providers || !ids.length && !custom || ids.length + custom > 96;
  }
  async refreshEffects() {
    const epoch = this.epoch, page = await this.api("/gateway/effects");
    if (epoch !== this.epoch) return;
    $("#effect-history").innerHTML = page.items.map((j) => `<button class="evaluation-card" data-effect="${this.esc(j.id)}"><strong>${this.esc(date(j.created_at))}</strong><span>${this.esc(j.providers.join(" / "))} · ${j.summary?.[0]?.planned || "…"} 个场景</span><small>${this.esc(labels[j.status] || j.status)} · ${j.target_model_calls} 次目标调用</small></button>`).join("") || '<p class="empty-copy">结果、输出及评分将按账户保存180天。</p>';
  }
  effectReviewForm(job, row) {
    if (job.status === "running" || !row.output?.complete || row.review) return "";
    return `<form data-effect-review="${row.id}"><label>人工验收<select name="status"><option value="pass">通过</option><option value="fail">失败</option></select></label><label>具体证据<textarea name="reason" required minlength="10" maxlength="4000" placeholder="依据任务要求和实际输出说明评分理由"></textarea></label><button class="text-button">保存评分（保留自动评分记录）</button></form>`;
  }
  async openEffect(id) {
    const epoch = this.epoch, job = await this.api("/gateway/effects/" + id);
    if (epoch !== this.epoch) return;
    const expanded = new Set(this.effect?.id === id ? $$("[data-effect-row][open]").map((e) => e.dataset.effectRow) : []);
    this.effect = job;
    const percent = (v) => v == null ? "未测" : `${(v * 100).toFixed(1)}%`;
    const state = { pass: "通过", fail: "失败", pending: "待人工评分", unmeasured: "未测" };
    const rowStates = { capability_gap: "能力/评测缺口", execution_blocked: "执行未接通", budget_exhausted: "调用预算已用完", no_route: "无候选", error: "调用失败", completed: "输出已完成", incomplete: "输出未完成" };
    const resultLabel = (r) => {
      const overall = { pass: "整体验收通过", fail: "整体未通过", pending: "整体待评", unmeasured: "整体未测" }[r.overall?.status] || "尚无整体判定";
      if (r.overall?.kind === "expected_boundary") return `${overall} · 正确拦截`;
      if (r.status === "incomplete") return `${overall} · 输出未完成（含截断）`;
      if (r.status === "error") return `${overall} · 调用失败`;
      const g = r.review || r.grade;
      if (r.output?.complete && g.status === "fail") return `${overall} · 完整答案不合格`;
      return `${overall} · ${rowStates[r.status] || r.status}`;
    };
    $("#effect-result").hidden = false;
    $("#effect-result").innerHTML = `<div class="panel-heading"><h2>效果结果 · ${this.esc(labels[job.status] || job.status)}</h2><button id="export-effect" class="text-button">导出完整 JSON ↓</button>${job.status === "running" ? '<button id="cancel-effect" class="text-button">停止后续评测</button>' : ""}</div>
      <p>GPU ${job.started_gpu_calls} / ${job.planned_gpu_calls} · 目标调用尝试 ${job.target_model_calls} / ${job.max_target_calls} · 共享目标结果 ${job.reused_observations} 次 · 本批输出上限 ${job.max_output_tokens} tokens · 数据集 ${this.esc(job.dataset_version)}</p><p class="footnote">${this.esc(job.note)}</p>
      ${(job.reference_errata || []).map((e) => `<p class="form-note">参考勘误 · ${this.esc(e.case_id)}：${this.esc(e.corrected)} ${this.esc(e.basis)} 原始场景快照保留在导出中。</p>`).join("")}
      <h3>整体验收 · 全部场景统一分母</h3>
      <div class="table-scroll"><table id="effect-overall"><thead><tr><th>决策模型</th><th>整体验收通过/全部场景</th><th>通过构成：答案/正确拦截</th><th>未通过</th><th>待评</th><th>未测</th><th>已判定覆盖率</th><th>已记录/计划</th></tr></thead><tbody>${job.summary.map((s) => `<tr data-overall-provider="${this.esc(s.provider)}"><td>${this.esc(s.provider)}</td><td><strong>${s.overall_pass}/${s.overall_total} (${percent(s.overall_pass_rate)})</strong></td><td>${s.overall_answer_pass}/${s.overall_boundary_pass}</td><td>${s.overall_fail}</td><td>${s.overall_pending}</td><td>${s.overall_unmeasured}</td><td>${percent(s.overall_coverage)}</td><td>${s.recorded}/${s.planned}</td></tr>`).join("")}</tbody></table></div>
      <p class="footnote">整体验收通过率 =（答案通过 + 预设边界正确拦截）/ 全部计划场景。未通过、待评、未测始终留在分母；四类合计等于全部场景。错误路由、调用失败、完整答案不合格和输出未完成均计入未通过。正确拦截仅指用例预先规定的拒绝，例如零费用、空允许列表或缺少图片。此指标表示已证实的整体验收比例；待评与能力/评测缺口没有被判错。</p>
      <p class="footnote">${job.summary.map((s) => `${this.esc(s.provider)}：非预期无路由/执行受阻 ${s.overall_routing_failed}，边界违约 ${s.overall_boundary_failed}；未测中预登记缺口 ${s.capability_gaps}、预算未执行 ${s.budget_skipped}、尚无记录 ${s.not_recorded}`).join(" · ")}。指标版本 ${this.esc(job.overall_metric_version)}；历史记录按现有证据重新汇总，原始输出与评分保留。</p>
      <h3>答案质量与分类诊断（辅助）</h3>
      <div class="table-scroll"><table><thead><tr><th>决策模型</th><th>输出完成</th><th>已评分答案通过（辅助）</th><th>完整答案不合格/输出未完成</th><th>调用失败</th><th>任务类型判断正确/已检查</th></tr></thead><tbody>${job.summary.map((s) => `<tr><td>${this.esc(s.provider)}</td><td>${s.target_completed}</td><td>${s.quality_pass}/${s.quality_graded} (${percent(s.quality_pass_rate)})</td><td>${s.answer_failed}/${s.output_incomplete}</td><td>${s.errors}</td><td>${s.workflow_fit}/${s.workflow_checked}</td></tr>`).join("")}</tbody></table></div>
      <p class="footnote">已评分答案通过只看已有答案/调用评分的子集，各模型分母可能不同，不用于整体排名。任务类型判断正确检查用途标签是否匹配，是诊断项；最终完整答案通过可被验收，不因内部类型标签不同而否决。截断表示本次预算内没有完成交付，不等于模型答错。</p>
      <h3>性能、费用与已测对照</h3>
      <div class="table-scroll"><table><thead><tr><th>决策模型</th><th>路由 P50/P95</th><th>目标 P50/P95</th><th>参考费用/已知样本</th><th>可避免失败/基线配对</th></tr></thead><tbody>${job.summary.map((s) => `<tr><td>${this.esc(s.provider)}</td><td>${this.ms(s.route_p50_ms)}/${this.ms(s.route_p95_ms)}</td><td>${this.ms(s.target_p50_ms)}/${this.ms(s.target_p95_ms)}</td><td>${s.cost_known_samples ? this.usd(s.known_reference_cost_usd) : "未知"} · ${s.cost_known_samples}/${s.target_observations}</td><td>${s.avoidable_failures_vs_tested_baselines}/${s.paired_quality_cases}</td></tr>`).join("")}</tbody></table></div>
      <p class="footnote">延迟来自顺序调用；共享目标结果不是重复测量。费用为各路由方案的参考用量价格，不可把三行相加作为实际账单。</p>
      <h3>逐场景证据与验收</h3>${job.results.filter((r) => r.arm === "routed").map((r) => {
        const c = job.cases.find((c) => c.id === r.case_id), g = r.review || r.grade;
        const others = job.results.filter((b) => b.case_id === r.case_id && b.provider === r.provider && b.arm !== "routed");
        return `<details class="evaluation-row" data-effect-row="${r.id}"><summary>${this.esc(c.title)} · ${this.esc(r.provider)} · ${this.esc(resultLabel(r))}</summary>
          <p>${this.esc(c.task.text)}</p><p class="footnote">验收：${this.esc(c.gap || job.reference_errata?.find((e) => e.case_id === c.id)?.corrected || c.judge.rubric)}${c.judge.expected !== null ? " · 参考 " + this.esc(JSON.stringify(c.judge.expected)) : ""}</p>
          <p>${this.esc(r.selected_model?.name || "无目标模型")} · ${this.esc(r.profile?.workflow || "未执行分类")} · ${this.esc(r.configuration_id || "")}</p>
          <p>整体验收依据：${this.esc(r.overall?.reason || "尚无整体判定")}。原始答案评分：${this.esc(state[g.status] || g.status)}。</p>
          <p class="footnote">${this.esc(r.selection_reason || "")} ${this.esc(g.reason || "")}</p>${r.error ? `<p class="error">${this.esc(r.error.message)}</p>` : ""}
          ${r.profile ? `<details><summary>分类属性、选模依据与耗时</summary><pre>${this.esc(JSON.stringify({ profile: r.profile, assessed_profile: r.assessed_profile, candidate_count: r.candidate_count, cost_context: r.cost_context, classifier_timings: r.classifier_timings, route_ms: r.route_ms, target_timings: r.output?.timings, usage: r.output?.usage, reference_cost_usd: r.output?.observed_reference_cost_usd, reused: r.reused }, null, 2))}</pre></details>` : ""}
          ${mediaHTML(r.task?.attachments)}${r.output ? `<pre>${this.esc(r.output.text || "")}</pre>${mediaHTML(r.output.artifacts)}` : ""}
          ${r.review ? `<p>${{ user_review: "人工评审", assistant_review: "助手辅助评审", isolated_tests: "隔离测试" }[r.review.method] || "人工评审"}：${this.esc(r.review.reviewer)} · ${this.esc(r.review.reason)}</p>` : ""}
          ${others.length ? `<h4>同一分类下的对照策略</h4>${others.map((b) => `<details><summary>${b.arm === "low" ? "低价" : "高能力"} · ${this.esc(b.selected_model?.name || "无候选")} · ${this.esc(resultLabel(b))} ${b.reused ? "· 共享结果" : ""}</summary><pre>${this.esc(b.output?.text || b.error?.message || b.status)}</pre>${b.review ? `<p>${this.esc(b.review.method)} · ${this.esc(b.review.reason)}</p>` : this.effectReviewForm(job, b)}</details>`).join("")}` : ""}
          ${this.effectReviewForm(job, r)}
        </details>`;
      }).join("")}`;
    $$("[data-effect-row]").forEach((e) => { e.open = expanded.has(e.dataset.effectRow); });
    clearTimeout(this.effectPollTimer);
    if (job.status === "running") this.effectPollTimer = setTimeout(() => this.action(() => this.openEffect(id)), 3000);
    else this.refreshEffects().catch((e) => this.notify(e.message));
  }
  budget() {
    const providers = $$('input[name="eval-provider"]:checked').length, cases = $$('input[name="eval-case"]:checked').length;
    $("#evaluation-budget").textContent = `${providers} 个决策模型 × ${cases} 个任务 = ${providers * cases} 次 GPU 调用尝试；Bedrock 目标模型调用为 0。每次最多选择 16 个任务。`;
    $("#evaluation-start").disabled = !providers || !cases || cases > 16;
  }
  async refreshEvaluations() {
    const epoch = this.epoch, page = await this.api("/gateway/evaluations");
    if (epoch !== this.epoch) return;
    $("#evaluation-history").innerHTML = page.items.map((r) => `<button class="evaluation-card" data-evaluation="${this.esc(r.id)}"><strong>${this.esc(date(r.created_at))}</strong><span>${this.esc(r.providers.join(" / "))} · ${r.case_ids.length} 个任务</span><small>${this.esc(labels[r.status] || r.status)}</small></button>`).join("") || '<p class="empty-copy">尚未创建评测。结果会保存在数据库中。</p>';
  }
  async openEvaluation(id) {
    const epoch = this.epoch, job = await this.api("/gateway/evaluations/" + id);
    if (epoch !== this.epoch) return;
    this.evaluation = job;
    $("#evaluation-result").hidden = false;
    const percent = (x) => x == null ? "—" : `${(x * 100).toFixed(1)}%`;
    $("#evaluation-result").innerHTML = `<div class="panel-heading"><h2>评测结果 · ${this.esc(labels[job.status] || job.status)}</h2><button class="text-button" id="export-evaluation">导出 JSON ↓</button></div><p>${job.results.length} / ${job.planned_gpu_calls} 次已记录 · 数据集 ${this.esc(job.dataset_version)}</p><p class="footnote">${this.esc(job.note)}</p><div class="table-scroll"><table><thead><tr><th>决策模型</th><th>已测 / 计划</th><th>七项一致</th><th>一致率</th><th>失败</th><th>HTTP P50 / P95</th></tr></thead><tbody>${job.summary.map((s) => `<tr><td>${this.esc(s.provider)}</td><td>${s.samples} / ${s.planned_samples}</td><td>${s.exact_matches}</td><td>${percent(s.exact_match_rate)}</td><td>${s.failed_calls}</td><td>${this.ms(s.http_p50_ms)} / ${this.ms(s.http_p95_ms)}</td></tr>`).join("")}</tbody></table></div><div class="evaluation-fields">${job.summary.map((s) => `<p><strong>${this.esc(s.provider)}</strong> ${Object.entries(s.field_accuracy).map(([k, v]) => `${this.esc(k)} ${percent(v)}`).join(" · ")}</p>`).join("")}</div><h3>逐任务差异</h3>${job.results.map((r) => `<details class="evaluation-row"><summary>${this.esc(this.cases.find((c) => c.id === r.case_id)?.title || r.case_id)} · ${this.esc(r.provider)} · ${r.status !== "completed" ? "调用失败" : r.exact_match ? "七项一致" : "存在差异"}</summary>${r.deployment ? `<p class="footnote">${this.esc(r.deployment)}</p>` : ""}${r.error ? `<p class="error">${this.esc(r.error.message)}</p>` : `<div class="table-scroll"><table><thead><tr><th>字段</th><th>参考</th><th>实际</th><th>一致</th></tr></thead><tbody>${Object.entries(r.expected).map(([k, v]) => `<tr><td>${this.esc(k)}</td><td>${this.esc(v)}</td><td>${this.esc(r.actual[k])}</td><td>${r.matches[k] ? "✓" : "✕"}</td></tr>`).join("")}</tbody></table></div>`}</details>`).join("")}`;
    clearTimeout(this.pollTimer);
    if (job.status === "running") this.pollTimer = setTimeout(() => this.action(() => this.openEvaluation(id)), 1800);
    else this.refreshEvaluations().catch((e) => this.notify(e.message));
  }
  async refreshUsers() {
    const epoch = this.epoch, page = await this.api("/gateway/users");
    if (epoch !== this.epoch) return;
    $("#users-table").innerHTML = `<table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>${page.items.map((u) => `<tr><td>${this.esc(u.username)}</td><td>${u.role === "admin" ? "管理员" : "普通用户"}</td><td>${u.enabled ? "启用" : "停用"}</td><td>${u.username === this.user.username ? "当前账户" : `<button class="text-button" data-user="${this.esc(u.username)}" data-enable="${!u.enabled}">${u.enabled ? "停用并撤销登录" : "启用"}</button>`}</td></tr>`).join("")}</tbody></table>`;
    $("#reset-user").innerHTML = page.items.filter((u) => u.username !== this.user.username).map((u) => `<option value="${this.esc(u.username)}">${this.esc(u.username)}</option>`).join("");
  }
  bind() {
    $("#session-select").onchange = () => { this.sessionId = $("#session-select").value; this.renderSessions(); this.action(() => this.refreshHistory()); };
    const sessionDialog = (rename) => {
      this.renaming = rename;
      $("#session-dialog-title").textContent = rename ? "重命名会话" : "新建会话";
      $("#session-title").value = rename ? this.sessions.find((s) => s.id === this.sessionId).title : "";
      $("#session-dialog").showModal();
    };
    $("#new-session").onclick = () => sessionDialog(false);
    $("#rename-session").onclick = () => sessionDialog(true);
    $("#cancel-session").onclick = () => $("#session-dialog").close();
    $("#session-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      const item = await this.api("/gateway/sessions" + (this.renaming ? "/" + this.sessionId : ""), { title: $("#session-title").value });
      this.sessions = [item, ...this.sessions.filter((s) => s.id !== item.id)];
      this.sessionId = item.id; this.renderSessions(); $("#session-dialog").close();
      this.notify("会话已保存"); await this.refreshHistory();
    }, e.submitter); };
    ["#history-scope", "#history-days"].forEach((id) => { $(id).onchange = () => this.action(() => this.refreshHistory()); });
    $("#refresh-history").onclick = (e) => this.action(() => this.refreshHistory(), e.currentTarget);
    $("#more-history").onclick = (e) => this.action(() => this.refreshHistory(true), e.currentTarget);
    $("#history").onclick = (e) => { const id = e.target.closest("[data-run]")?.dataset.run; if (id) this.action(() => this.openRecord(id)); };
    $("#close-history-detail").onclick = () => $("#history-detail").close();
    $("#history-export").onclick = () => this.download(this.selectedRecord, `run-${this.selectedRecord.id}.json`);
    $("#history-reuse").onclick = () => { this.reuse(this.selectedRecord.request); $("#history-detail").close(); location.hash = "workspace"; };
    $("#review-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      this.selectedRecord.review = await this.api(`/gateway/history/${this.selectedRecord.id}/review`, { passed: $("#review-passed").value === "true", rubric: $("#review-rubric").value, notes: $("#review-notes").value });
      this.notify("人工验收已保存"); await this.refreshHistory();
    }, e.submitter); };
    $("#evaluation-form").onchange = () => this.budget();
    $("#evaluation-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      const job = await this.api("/gateway/evaluations", { providers: $$('input[name="eval-provider"]:checked').map((x) => x.value), case_ids: $$('input[name="eval-case"]:checked').map((x) => x.value) });
      await this.openEvaluation(job.id); await this.refreshEvaluations();
    }, e.submitter); };
    $("#refresh-evaluations").onclick = (e) => this.action(() => this.refreshEvaluations(), e.currentTarget);
    $("#evaluation-history").onclick = (e) => { const id = e.target.closest("[data-evaluation]")?.dataset.evaluation; if (id) this.action(() => this.openEvaluation(id)); };
    $("#evaluation-result").onclick = (e) => { if (e.target.id === "export-evaluation") this.download(this.evaluation, `evaluation-${this.evaluation.id}.json`); };
    $("#effect-form").oninput = () => this.effectBudget();
    $("#effect-form").onchange = () => this.effectBudget();
    $("#effect-family").onchange = () => {
      $$("[data-effect-family]").forEach((e) => { e.hidden = Boolean($("#effect-family").value && e.dataset.effectFamily !== $("#effect-family").value); });
      this.effectBudget();
    };
    $("#effect-select").onclick = () => { $$("[data-effect-family]").filter((e) => !e.hidden).forEach((e) => { e.querySelector("input").checked = true; }); this.effectBudget(); };
    $("#effect-clear").onclick = () => { $$('input[name="effect-case"]').forEach((e) => { e.checked = false; }); this.effectBudget(); };
    $("#effect-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      const text = $("#effect-custom").value.trim(), rubric = $("#effect-rubric").value.trim();
      if (text && !rubric) throw new Error("请填写自定义任务的验收标准");
      const job = await this.api("/gateway/effects", {
        providers: $$('input[name="effect-provider"]:checked').map((e) => e.value),
        case_ids: $$('input[name="effect-case"]:checked').map((e) => e.value),
        custom_cases: text ? [{ title: text.slice(0, 80), task: { text }, rubric, expected_text: $("#effect-expected").value || null }] : [],
        baselines: $("#effect-baselines").checked,
        max_target_calls: Number($("#effect-call-cap").value), max_output_tokens: Number($("#effect-token-cap").value),
      });
      await this.openEffect(job.id); await this.refreshEffects();
    }, $("#effect-start")); };
    $("#refresh-effects").onclick = (e) => this.action(() => this.refreshEffects(), e.currentTarget);
    $("#effect-history").onclick = (e) => { const id = e.target.closest("[data-effect]")?.dataset.effect; if (id) this.action(() => this.openEffect(id)); };
    $("#effect-result").onclick = (e) => {
      if (e.target.id === "export-effect") this.download(this.effect, `effect-${this.effect.id}.json`);
      if (e.target.id === "cancel-effect") this.action(async () => { await this.api(`/gateway/effects/${this.effect.id}/cancel`, {}); await this.openEffect(this.effect.id); }, e.target);
    };
    $("#effect-result").onsubmit = (e) => {
      const form = e.target.closest("[data-effect-review]");
      if (!form) return;
      e.preventDefault();
      this.action(async () => { await this.api(`/gateway/effects/${this.effect.id}/reviews/${form.dataset.effectReview}`, { status: form.elements.status.value, reason: form.elements.reason.value }); await this.openEffect(this.effect.id); }, form.querySelector("button"));
    };
    $("#password-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      if ($("#new-password").value !== $("#confirm-password").value) throw new Error("两次新密码不一致");
      await this.api("/gateway/account/password", { current_password: $("#current-password").value, new_password: $("#new-password").value });
      this.signedOut(); this.notify("密码已更新，请重新登录。");
    }, e.submitter); };
    $("#user-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      await this.api("/gateway/users", { username: $("#new-user-name").value, password: $("#new-user-password").value, admin: $("#new-user-admin").checked });
      $("#user-form").reset(); this.notify("账户已创建"); await this.refreshUsers();
    }, e.submitter); };
    $("#users-table").onclick = (e) => { const button = e.target.closest("[data-user]"); if (button) this.action(async () => {
      await this.api("/gateway/users/" + encodeURIComponent(button.dataset.user), { enabled: button.dataset.enable === "true" });
      await this.refreshUsers();
    }, button); };
    $("#reset-password-form").onsubmit = (e) => { e.preventDefault(); this.action(async () => {
      if (!$("#reset-user").value) throw new Error("请选择需要重置的用户");
      await this.api("/gateway/users/" + encodeURIComponent($("#reset-user").value), { new_password: $("#reset-user-password").value });
      $("#reset-user-password").value = ""; this.notify("密码已重置，旧登录已撤销"); await this.refreshUsers();
    }, e.submitter); };
  }
}
