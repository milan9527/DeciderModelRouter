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
    ["#history-detail", "#session-dialog"].forEach((id) => $(id).close());
    ["#history", "#evaluation-result", "#evaluation-history", "#users-table", "#history-detail-content"].forEach((id) => { $(id).innerHTML = ""; });
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
    const [account, sessions, cases] = await Promise.all([
      this.api("/gateway/account"), this.api("/gateway/sessions"), this.api("/gateway/evaluations/cases"),
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
    return this.enabled ? { session_id: this.sessionId, client_request_id: crypto.randomUUID() } : {};
  }
  page(page) {
    if (!this.enabled) return;
    if (page === "performance") this.action(() => this.refreshHistory());
    if (page === "evaluations") this.action(() => this.refreshEvaluations());
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
