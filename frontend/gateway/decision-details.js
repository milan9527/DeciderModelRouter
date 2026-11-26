export function decisionConfidenceHTML(distribution, esc) {
  if (!distribution) return "";
  if (distribution.status !== "reported") {
    return '<p class="footnote">选择倾向：此记录未获得有效候选概率。</p>';
  }
  const percent = (v) => Number.isFinite(v) ? `${(v * 100).toFixed(2)}%` : "未知";
  const gap = Number.isFinite(distribution.margin) ? `${(distribution.margin * 100).toFixed(2)} 个百分点` : "未知";
  return `<div class="decision-confidence"><strong>决策模型的选择倾向</strong>
    <p>当前选择 ${esc(percent(distribution.selected_probability))} · 第二名 ${esc(percent(distribution.runner_up_probability))} · 相差 ${esc(gap)}</p>
    <p class="footnote">仅表示本次 ${esc(distribution.candidate_count)} 个选项中的选择倾向，不是回答正确率。差距越小，前两项越接近；不同候选集合的概率不能直接比较。</p></div>`;
}

export function evidencePresentationHTML(presentation, esc) {
  if (!presentation?.anonymous) return "";
  const primary = presentation.primary_benchmark;
  return `<p class="footnote">本次GPU使用匿名候选编号比较，实际型号在后台映射。${primary
    ? `主要参考 ${esc(primary.benchmark)} · ${esc(primary.release)}，分项为 ${esc(primary.dimensions.join(" / "))}。辅助均值只计算此批次的完整相关分项，原始成绩保留；它不是业务通过率。`
    : "没有可统一比较的文本评测批次，按本任务已有证据判断。"}</p>`;
}

export function selectionAdapterHTML(adapter, esc) {
  if (!adapter?.active) return "";
  return `<p class="footnote">本轮Liquid选模使用公开评测微调版本；第一步需求判断仍使用原模型。版本 ${esc(adapter.version)} · 权重 ${esc(adapter.sha256.slice(0, 12))}。</p>`;
}

export function runtimeObservationsHTML(observations, esc) {
  return (observations || []).filter((r) => r.status === "response_timeout").map((r) =>
    `<small>本配置在 ${esc(r.workflow)} 请求中有 ${esc(r.observations)} 次超过 ${esc(r.deadline_ms / 1000)} 秒响应等待，是否最终完成未知；当时输出上限 ${esc(r.output_token_limit)}。这是超时记录，不是成功请求P95。</small>`
  ).join("");
}

export function taskScoreSummaryHTML(presentation, candidateId, esc) {
  if (!presentation?.primary_benchmark) return "";
  const score = presentation.task_score_summaries?.[candidateId]?.mean;
  return `<small>主要批次相关分项均值：${Number.isFinite(score) ? `${esc(score.toFixed(2))}/100` : "未知"}（辅助值，不是通过率）</small>`;
}
