# v15 · 原始客服路由与选项顺序诊断

[PPTX](./Strands-Decider-v15-原始客服路由与选项顺序诊断-32页-20261016.pptx) · [PDF](./Strands-Decider-v15-原始客服路由与选项顺序诊断-32页-20261016.pdf) · [讲稿与来源](./讲稿与来源.md) · [新增页预览](./updated-overview.png)

基于 v14 新增两页，共 **32 页**。原有 30 页正文、图片及讲稿保留，页码顺延，优化流程页面不变。

- **P19：支付客服原始流程复测。** 展示同一请求、三路所选型号、三轮路由均值及各一次目标答复。采用 2026-10-16 已保存的原始流程数据，没有使用增强输入结果，也没有重新调用模型。
- **P32：选项顺序问题。** 最后一页明确标注 **internal only**。只展示原始输入的三次顺序诊断：候选信息与 ID 不变，Decider 三次均选当前排列首项。说明当前观察和待定位问题，不推广为所有任务的结论。

## P19 数据

业务请求：“回复客户的问题：USDC 已转出，为什么订单仍显示待支付？当前链上确认数为 3/12。”

| 路由器 | 原始流程所选型号 | 两步路由均值，n=3 | 目标答复，n=1 |
|---|---|---:|---|
| Strands Decider | Claude Opus 5 | 449.2 ms | 40 秒响应等待上限触发，HTTP 504 |
| Jev | Claude Opus 5.5 | 917.8 ms | 34.08 s 返回答复，包含输入未提供的“30 分钟”时限 |
| GPT-6 Sol low | GPT-6 Luna | 2474.1 ms | 3.50 s 返回答复，建议等待确认、避免重复付款 |

三路都选择 `customer_support`，同阶段使用相同业务输入与候选资料。路由均值和目标单次耗时分别展示，不相加为单次端到端测量。GPT 路由器使用 low；目标调用沿用评测映射的 max 档位，输出预算 4096 token、响应等待上限 40 秒。完整答复不等于全面业务验收通过。

前一页继续保留 v14 的 2026-10-15 单次观察，本页补充新的原始流程复测；未改写历史数据。

## 来源与核对

- [客服原始请求、路由响应与目标结果](./sources/support-original-comparison.json)
- [顺序诊断三次原始请求与响应](./sources/order-original-diagnostic.json)（internal only）
- [完整原始批次](../../results/model-evidence-2026-10-16/results.json)
- [构建清单](./build-manifest.json) · [渲染核对](./render-audit.json)

P32 的原始候选提供 MIRACL dev en/zh 平均检索分数，English 30.651、Multilingual 59.225、v4 50.6635。三次仅交换排列，不能据此认定所有任务均偏好首项，也未对 Jev/GPT 做相同顺序测试。模型行为、提示表达和底层推理实现的影响仍需进一步定位。

构建脚本：`scripts/build_presentation_v15.py`；核对脚本：`scripts/verify_presentation_v15.py`。核对包括原有页面正文 XML、图片与讲稿保持不变，以及新增页数据、文本渲染、边界和 internal only 标记。
