# 五模型原始流程对比

[打开模块](https://d3i7ogfx5ml7dc.cloudfront.net/#comparison) · [English](FIVE_MODELS.en.md)

对比 Strands Decider、Liquid d1-2B、H2O-Lightning 4B、Jev 与 GPT。支付和模型路由直接复用原模块的任务、业务事实、问题、候选条件与顺序。三个本地模型继续常驻同一台 g6.xlarge / L4，按单并发依次运行。

## 与原模块保持一致

| 项目 | 当前行为 |
| --- | --- |
| 支付四场景 | USDC 确认数 3/12；USDT 应付 100、到账 99.80；退款地址未验证且未审批；4800 USDC 商户结算未到账、已等待 120 分钟 |
| 支付五步 | 客服受理 → 交易核验 → 风控审批 → 业务处置 → 回复结案。全部判断使用同一份初始快照；五次判断结束后，按顺序执行这五个建议 |
| 支付输入 | `crypto.payload_for(..., input_profile="baseline")`；无执行反馈、无额外条件或完成标记，保留原候选顺序 |
| 模型路由 | 原支付客服、代码、结算分析、中英文索引四任务；先选流程，再根据该流程的原评测证据选具体型号 |
| 路由目标 | 两步路由结束后，可点击每列“执行目标模型”；目标生成不参与路由决策完成数或耗时 |
| GPT | 沿用原模块提示词、单个 `{"choice":"candidate_id"}` 返回协议、1024 输出 token 和 low effort；支付 GPT-6.1 Sol，路由 GPT-6 Sol |
| 调用路径 | 原三个模型直接复用 `crypto.decision` / `routing.decision`；Liquid/H2O 接收相同 `state` / `questions`，通过各自原生接口调用 |

支付与路由的任务和候选顺序已锁定。模型仍可能选择不同答案，原始返回、执行保护和实际状态均保留。支付初始条件尚未满足资金处理要求时，正确挂起或跟进不会被强行改成入账、退款或结案。

“设备控制”下包含回家开启空调、调整温度、设置常用温度范围三个业务场景，均保留三步交互，仍可编辑任务或反转候选。页面分为支付客服、模型路由、设备控制三类，共 11 条场景；本轮复测聚焦恢复原输入的支付与路由八场景。

## 原输入复测结果（2026-10-29）

线上浏览器完成八场景、五模型 **140/140 次真实决策调用**，全部返回有效选择，140 份实际模型输入与修正前采集的原模块输入一致。每场景每模型运行一次，按 Decider → Liquid → H2O → Jev → GPT 顺序、单并发执行。以下耗时包含后端通信与服务封装，未预热、剔除首请求或扣减网络时间；这些小样本结果不代表通用准确率或稳定延迟。

[完整请求、返回、逐步执行与计时](results/five-models-original-2026-10-29.json) · [支付截图](screenshots/five-models-original/payment-pending-results.png) · [退款处置截图](screenshots/five-models-original/payment-refund-execute.png) · [路由截图](screenshots/five-models-original/routing-support-results.png)

### 支付四场景

每格为 **五步累计决策耗时 / 与原演示参考一致次数**。参考一致反映候选选择，不等于资金处理完成。

| 原始场景 | Decider | Liquid d1-2B | H2O 4B | Jev | GPT-6.1 Sol |
| --- | ---: | ---: | ---: | ---: | ---: |
| USDC 未到账，确认数 3/12 | 244.9 ms / 4/5 | 183.5 ms / 2/5 | 391.2 ms / 3/5 | 3719.7 ms / 4/5 | 4748.8 ms / 5/5 |
| USDT 少付，差额 0.20 | 242.2 ms / 4/5 | 185.5 ms / 2/5 | 388.1 ms / 3/5 | 1740.4 ms / 4/5 | 4292.4 ms / 5/5 |
| 退款，地址未验证且未审批 | 240.4 ms / 2/5 | 187.5 ms / 4/5 | 389.8 ms / 4/5 | 1719.5 ms / 5/5 | 7186.3 ms / 5/5 |
| 商户结算，超过 SLA | 242.7 ms / 4/5 | 191.9 ms / 4/5 | 388.2 ms / 4/5 | 1747.5 ms / 5/5 | 4147.4 ms / 5/5 |
| **20 步参考一致合计** | **14/20** | **12/20** | **14/20** | **18/20** | **20/20** |
| **每步平均后端耗时** | **48.5 ms** | **37.4 ms** | **77.9 ms** | **446.4 ms** | **1018.7 ms** |

Decider 四场景的 4/5、4/5、2/5、4/5 与此前原始固定流程记录一致，上一版提高完成数的结果来自改变了初始输入。此次 Liquid 最快，GPT 最符合原演示参考；H2O 与 Decider 总一致次数相同，具体错误不同。Jev 的 USDC 首次受理为 1987.1 ms，原值计入表中；本轮没有验证其是否由 Lambda 冷启动造成。

几个实际差异：

| 步骤 | 原演示参考 | Decider | Liquid | H2O | Jev | GPT |
| --- | --- | --- | --- | --- | --- | --- |
| USDC 风控审批 | `approved` | `await_approval` | `await_approval` | `await_approval` | `await_approval` | `approved` |
| USDT 业务处置 | `hold` | `credit_order` | `wait_settlement` | `wait_settlement` | `hold` | `hold` |
| 退款风控审批 | `verify_destination` | `await_approval` | `verify_destination` | `verify_destination` | `verify_destination` | `verify_destination` |
| 退款业务处置 | `hold` | `refund_payment` | `credit_order` | `wait_settlement` | `hold` | `hold` |
| 退款回复结案 | `follow_up` | `resolve` | `follow_up` | `follow_up` | `follow_up` | `follow_up` |

所有支付场景均执行了保存的五步建议。原始资金条件尚未满足，最终没有入账、退款或结算完成，工单均保持跟进；这也包含 GPT 的正确处理结果。执行保护调整 Decider 4 次、Liquid 2 次，其余为 0 次。例如 Decider 退款建议 `refund_payment` 被保护为 `hold`，随后 `resolve` 被保护为 `follow_up`。H2O 的错误等待动作不会触发资金保护，**保护次数为零也不代表选对**。最终客服回复来自原模拟执行器，不能当作模型自行生成且正确的证据。

### 模型路由四场景

原任务分别为：USDC 3/12 确认数的客服回复；带去重、验签、Decimal 和并发保护的支付 webhook；12000 USDC 结算、0.8% 手续费及 24 USDC 暂扣的差额分析；中英文支付工单向量索引。完整原文及候选证据随每次请求保存。

五个模型在四任务的**流程类型选择均为 4/4**；第二步所选具体型号如下。本表列出真实选择，不为开放式型号选择指定唯一正确答案。

| 场景 | Decider | Liquid d1-2B | H2O 4B | Jev | GPT-6 Sol |
| --- | --- | --- | --- | --- | --- |
| 高频支付客服 | Claude Opus 5 | Claude Opus 4.6 | GPT 5.5 | Claude Opus 5.5 | GPT-6 Luna |
| 支付 webhook 代码 | Claude Opus 5 | Claude Opus 5.5 | Claude Sonnet 5.5 | Claude Sonnet 5.5 | Claude Sonnet 5.5 |
| 结算差额分析 | Claude Opus 5 | Claude Opus 5.5 | Claude Opus 5.5 | Claude Opus 5.5 | GPT-6.1 Sol |
| 中英文工单索引 | **Embed English** | Embed Multilingual | Embed Multilingual | Embed Multilingual | Embed Multilingual |

| 两步累计决策耗时 | Decider | Liquid d1-2B | H2O 4B | Jev | GPT-6 Sol |
| --- | ---: | ---: | ---: | ---: | ---: |
| 高频支付客服 | 212.7 ms | 194.1 ms | 438.6 ms | 644.6 ms | 1372.0 ms |
| 支付 webhook 代码 | 211.3 ms | 196.1 ms | 439.5 ms | 701.8 ms | 1251.4 ms |
| 结算差额分析 | 212.6 ms | 195.0 ms | 439.4 ms | 779.7 ms | 1454.9 ms |
| 中英文工单索引 | 101.5 ms | 78.4 ms | 165.3 ms | 733.4 ms | 998.6 ms |

Decider 识别出了四种流程，但中英文索引仍选了仅支持英文的型号，与原候选中的语言能力不符；其余四模型选了多语言型号，见[实际输入与返回截图](screenshots/five-models-original/routing-retrieval-model.png)。三个文本任务中 Decider 均选择 Claude Opus 5，其他模型会随任务改变型号。仅据路由返回不能判断最终答案质量或成本是否最优，本轮按原流程没有自动执行目标模型。

## 输入核对

修正前从线上原 `/api/crypto`、`/api/routing` 模块采集模型输入：支付 20 步、路由 4 个流程判断和 20 组分支候选。完整输入保存在[核对样本](../tests/fixtures/original_comparison_inputs.json)。

每次复测都逐项比较实际发送的 `state`、指令、候选内容和顺序；除核对原模块的 `input_hash`，还比较保留字段顺序的完整 JSON。参考答案只用于展示，不进入模型请求。模型目录和评测快照在本批复测期间固定。

支付各模型、各步骤共享原始快照。路由第一步输入相同；第二步由模型自己选择的流程决定候选，选择相同流程时输入完全相同。调用无效、超时或选错时保留真实结果。

## 页面与计时

顶部五列显示累计决策耗时、步骤选择和最终状态，下方点击步骤查看原始输入、模型返回和执行记录。支付显示资金/工单状态；路由突出所选流程及型号，目标执行另行显示。单并发调度改变提交顺序，不改变原始请求。

每步与累计耗时统一采用后端调用计时。原三模型使用原接口的 `backend_total_ms`，Liquid/H2O 使用 ECS 到常驻网关的完整调用耗时；各步之和与顶部相同。工具执行另计。Jev 包含现有 Lambda 转发，各服务原生 tokenizer 与服务封装不同。

登录、CSRF、账号隔离与私有 S3 + CloudFront OAC 保留。一个 API 进程同时运行一轮，记录最多 20 条、保留 30 分钟。可选目标结果保存在当前页面及 JSON 导出中，刷新后需另行执行。

部署：Decider v19 / vLLM 0.31.0 / BF16；Liquid d1-2B / 官方 Transformers / BF16；H2O 4B / 补丁 vLLM 0.30.0 / FP8。三个模型位于现有 `i-0ae7eb0abcb815af3`，每模型并发 1。[维护说明](../deployment/open_models/README.zh.md)。

实现：[原支付模块](../app/crypto.py) · [原路由模块](../app/routing.py) · [五模型编排](../app/comparison.py) · [场景与执行](../app/comparison_workflows.py)。部署时使用 `--keep-model-catalog` 保持本轮候选目录不变。

上一版变更了初始条件和流程，已移至[变更输入版历史记录](FIVE_MODELS_CHANGED_INPUTS.md)。其中的 8/11 等完成数及测速不再作为原模块的比较结论。
