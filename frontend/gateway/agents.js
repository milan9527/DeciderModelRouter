const $ = (s) => document.querySelector(s);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const ADVANCED_BILLING = "针对刚才T2重复计费，设计可落地的跨区域幂等方案：PostgreSQL是账务真源，Kafka至少一次投递，消费者可能在扣款提交后、ACK前崩溃。两个区域在网络分区时都可接收请求，相同tenant+event可能携带冲突金额，退款事件可早于扣款，旧事件在30天后重放，租户迁移期间有双写。明确线性化点、事务边界、隔离级别、outbox/inbox状态机与恢复顺序。证明任何故障交错下至多扣款一次且不会遗漏合法退款；说明不能同时保证的性质、如何检测和人工解决冲突。提供核心伪代码、两条反例执行轨迹、故障注入测试矩阵和不停止服务的迁移回滚方案。不要声称单个唯一索引就能保证外部支付exactly once。";

export class IndustryAgents {
  constructor({ api, esc, ms, usd, notify }) {
    Object.assign(this, { api, esc, ms, usd, notify, epoch: 0, busy: false });
    $("#agent-scenario").addEventListener("change", () => this.newSession());
    $("#agent-new").addEventListener("click", () => this.newSession());
    $("#agent-form").addEventListener("submit", (e) => { e.preventDefault(); this.execute(false); });
    $("#agent-run-all").addEventListener("click", () => this.execute(true));
    $("#agent-advanced").addEventListener("click", () => { $("#agent-prompt").value = ADVANCED_BILLING; });
    $("#agent-summary").addEventListener("click", () => {
      $("#agent-prompt").value = "请用一段120字以内的中文总结你上一条回答，只保留主要结论和限制条件，不添加任何新事实，不调用工具。";
    });
    $("#agent-turns").addEventListener("click", (e) => {
      const button = e.target.closest("[data-agent-turn]");
      if (button && !this.busy) $("#agent-prompt").value = this.item.turns[Number(button.dataset.agentTurn)];
    });
    $("#agent-history").addEventListener("click", (e) => {
      const button = e.target.closest("[data-agent-job]");
      if (button && !this.busy) this.load(button.dataset.agentJob).catch((e) => this.notify(e.message));
    });
  }
  reset() {
    this.epoch++;
    this.session = null;
    this.busy = false;
    this.catalog = null;
    for (const id of ["agent-result", "agent-history", "agent-metrics"]) $("#" + id).innerHTML = "";
  }
  async enter() {
    const epoch = this.epoch;
    const catalog = await this.api("/gateway/agent/scenarios");
    if (epoch !== this.epoch) return;
    this.catalog = catalog;
    $("#agent-scenario").innerHTML = catalog.items.map((s) =>
      `<option value="${this.esc(s.id)}">${this.esc(s.industry)} · ${this.esc(s.title)}</option>`).join("");
    $("#agent-runtime-state").textContent = catalog.configured
      ? "AgentCore Runtime V2 · 模型入口 Model Router · 默认 Liquid 分类"
      : "AgentCore Runtime 尚未部署";
    this.newSession();
    await this.history();
  }
  newSession() {
    if (this.busy || !this.catalog) return;
    this.session = null;
    this.item = this.catalog.items.find((s) => s.id === $("#agent-scenario").value);
    $("#agent-context").textContent = this.item.context;
    $("#agent-data").textContent = JSON.stringify(this.item.datasets, null, 2);
    $("#agent-policy").textContent = this.item.policy;
    $("#agent-acceptance").textContent = this.item.acceptance.join("；");
    $("#agent-turns").innerHTML = this.item.turns.map((t, i) =>
      `<button type="button" class="agent-turn" data-agent-turn="${i}"><strong>第 ${i + 1} 轮</strong><span>${this.esc(t)}</span></button>`).join("");
    $("#agent-prompt").value = this.item.turns[0];
    $("#agent-advanced").hidden = this.item.id !== "saas";
    $("#agent-session-label").textContent = "新会话 · 可使用建议任务或输入自己的连续追问";
    $("#agent-result").innerHTML = "";
    $("#agent-metrics").innerHTML = "";
    this.controls();
  }
  controls() {
    for (const id of ["agent-send", "agent-run-all", "agent-new", "agent-scenario", "agent-prompt", "agent-budget", "agent-tier", "agent-advanced", "agent-summary"])
      $("#" + id).disabled = this.busy || !this.catalog?.configured;
    $("#agent-summary").disabled ||= !this.session;
  }
  async execute(all) {
    if (this.busy) return;
    this.busy = true; this.controls();
    const epoch = this.epoch;
    // A four-turn exercise always starts its own conversation.
    if (all) this.session = null;
    const prompts = all ? [...this.item.turns] : [$("#agent-prompt").value.trim()];
    try {
      for (let i = 0; i < prompts.length && epoch === this.epoch; i++) {
        if (!prompts[i]) throw new Error("请输入业务任务");
        $("#agent-session-label").textContent = all ? `正在验证第 ${i + 1} / 4 轮` : "Agent正在处理…";
        const job = await this.api("/gateway/agent/jobs", {
          scenario_id: this.item.id, prompt: prompts[i], session_id: this.session,
          client_request_id: crypto.randomUUID(),
          routing_preferences: { cost_tier: $("#agent-tier").value, expected_output_tokens: 1024,
            max_output_tokens: Number($("#agent-budget").value) },
        });
        if (epoch !== this.epoch) return;
        this.session = job.session_id;
        let current = job;
        while (epoch === this.epoch) {
          this.render(current);
          if (current.status !== "running") break;
          await delay(1500);
          if (epoch !== this.epoch) return;
          current = await this.api("/gateway/agent/jobs/" + job.id);
        }
        if (current.status !== "completed") throw new Error(current.error || "本轮未完成，已停止后续轮次");
      }
      if (epoch === this.epoch) $("#agent-session-label").textContent = "会话已保留 · 可继续追问，模型会按新任务重新评估";
    } catch (error) {
      if (epoch === this.epoch) this.notify(error.message);
    } finally {
      if (epoch === this.epoch) {
        this.busy = false; this.controls();
        await this.history().catch((e) => this.notify(e.message));
      }
    }
  }
  render(job) {
    const steps = job.steps || [], events = job.events || [], m = job.metrics || {};
    const metric = (title, value) => `<div class="metric"><div class="metric-label">${title}</div><div class="metric-value">${value ?? 0}</div></div>`;
    const end = job.finished_at || (job.status === "running" ? Date.now() : job.updated_at);
    $("#agent-metrics").innerHTML = metric("本轮模型调用", m.model_calls) + metric("工具调用", m.tool_calls)
      + metric("实际模型切换", m.model_switches) + metric("沿用配置次数", m.retained_calls)
      + metric("本轮总耗时", this.ms(Math.max(0, end - job.created_at))) + metric("缓存读取 tokens", m.cache_read_tokens);
    $("#agent-result").innerHTML = `<div class="panel-heading"><h2>第 ${job.turn} 轮 · ${this.esc({ running: "处理中", completed: "已完成", error: "失败", interrupted: "已中断" }[job.status])}</h2><span class="tiny">${this.esc(job.id)}</span></div>
      <p>${this.esc(job.prompt)}</p>
      <div class="agent-table"><table><thead><tr><th>调用</th><th>实际模型</th><th>判断与切换依据</th><th>性能</th><th>参考费用</th></tr></thead><tbody>${steps.map((s) =>
        `<tr><td>#${s.step}<br>${this.esc(s.stop_reason || s.status)}</td><td>${this.esc(s.model_id || (job.status === "running" && s.status === "started" ? "处理中" : "未完成，请查看历史记录"))}
        ${s.previous_model_id ? `<small>上次：${this.esc(s.previous_model_id)}</small>` : ""}</td>
        <td>${s.model_changed ? '<strong class="agent-switch">已切换模型</strong>' : s.stickiness?.retained ? "沿用模型" : "重新评估"}
        <small>${this.esc(s.profile ? `${s.profile.workflow} · ${s.profile.difficulty}` : "")}</small>
        <small>${this.esc(s.stickiness?.reason || s.error?.message || "")}</small>
        <details><summary>选型依据与用量</summary><p>${this.esc(s.selection_reason)}</p><pre>${this.esc(JSON.stringify({
          profile: s.profile, configuration_id: s.configuration_id,
          configuration_changed: s.configuration_changed, usage: s.usage,
        }, null, 2))}</pre></details></td>
        <td>${s.decision_mode === "joint" ? `联合决策 ${this.ms(s.model_decision_ms)}（${s.model_decision_calls || 0} 次请求）<small>含 GPU 往返 ${this.ms(s.gpu_ms)}，不重复相加</small>` : `GPU 分类 ${this.ms(s.gpu_ms)}<br>GPU 选模 ${this.ms(s.model_decision_ms)}（${s.model_decision_calls || 0} 次）`}<br>目标 ${this.ms(s.target_ms)}<small>缓存读 ${s.cache?.read_tokens || 0}</small></td>
        <td>${this.usd(s.reference_cost_usd)}<small>缓存计价未知时不估算折扣</small></td></tr>`).join("") ||
        '<tr><td colspan="5">等待AgentCore启动与第一次模型调用…</td></tr>'}</tbody></table></div>
      <details class="agent-tools" open><summary>工具过程与业务依据</summary>${events.filter((e) => e.type === "tool_result").map((e) =>
        `<article><strong>${this.esc(e.name)} · ${this.esc(e.status)} · ${this.ms(e.elapsed_ms)}</strong>
        <pre>${this.esc(JSON.stringify({ arguments: e.arguments, result: e.result }, null, 2))}</pre></article>`).join("") ||
        '<p class="muted">本轮尚无工具结果；Agent可以复用之前的查询结果。</p>'}</details>
      ${job.answer ? `<h3>Agent答复</h3><pre class="agent-answer">${this.esc(job.answer)}</pre>` : ""}
      ${job.error ? `<p role="alert">${this.esc(job.error)}</p>` : ""}
      ${job.trimmed_turns ? `<p class="footnote">本轮因长度限制移除了较早的 ${job.trimmed_turns} 轮对话，判断仅基于保留的上下文。</p>` : ""}
      ${job.context_messages != null ? `<p class="footnote">本轮带入 ${job.context_messages} 条历史消息，${job.context_bytes} 字节。</p>` : ""}
      <p class="footnote">本轮偏好：${this.esc({ low: "优先节省", medium: "自动均衡", high: "优先能力" }[job.routing_preferences?.cost_tier])}；
      输出上限 ${job.routing_preferences?.max_output_tokens ?? "—"} tokens。总耗时包含Runtime启动、工具编排与网关往返。
      执行完成不等于业务验收通过；切换次数本身不是质量指标。</p>`;
  }
  async history() {
    if (!this.catalog) return;
    const epoch = this.epoch, data = await this.api("/gateway/agent/jobs");
    if (epoch !== this.epoch) return;
    $("#agent-history").innerHTML = data.items.map((j) => `<button type="button" class="agent-history-row" data-agent-job="${this.esc(j.id)}">
      <strong>${this.esc(j.industry)} · 第 ${j.turn} 轮</strong><span>${this.esc(j.prompt)}</span>
      <small>${this.esc(new Date(j.created_at).toLocaleString("zh-CN"))} · ${this.esc(j.status)}</small></button>`).join("") ||
      '<p class="empty-copy">运行记录将按账户保存，刷新后仍可查看。</p>';
  }
  async load(id) {
    const epoch = this.epoch;
    const job = await this.api("/gateway/agent/jobs/" + id);
    if (epoch !== this.epoch) return;
    $("#agent-scenario").value = job.scenario_id;
    this.newSession();
    this.session = job.session_id;
    $("#agent-budget").value = String(job.routing_preferences.max_output_tokens);
    $("#agent-tier").value = job.routing_preferences.cost_tier;
    $("#agent-session-label").textContent = "已载入该Agent会话，可继续追问";
    this.render(job);
    this.controls();
  }
}
