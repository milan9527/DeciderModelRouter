# Bedrock 分类选型：任务评估与程序策略

2026-11-24。此版本落实“任务评估 → 按类别选择 Bedrock 模型与配置”。[首版调研](ROUTING_OPTIMIZATION.zh.md)保留此前的选型依据；本文描述当前新增实现。

## 范围与现有证据

下游候选严格来自 `app/bedrock_catalog.json`，按精确型号 ID 关联 `app/routing_rankings.json`。现有目录为 124 个模型；2026-11-24 将公开评测映射从 **25 个扩展到 60 个**：57 个文本型号、3 个向量型号。其中 51 个型号的执行配置已映射，另 9 个有精确型号成绩但配置仍待核验，不能自动执行。账户、生命周期、任务分项和配额仍会继续过滤；有映射不代表当前端点可用。

新增覆盖 Nova、Llama、Gemma、Claude 4.5、Devstral、MiniMax、Qwen 等型号。另有 14 个型号只具备核验过的外部资料和参考价，11 个是未独立评测的容量变体，29 个需要专用任务或模态评测，10 个未找到接入范围内的公开成绩。逐型号状态和来源见[覆盖记录](../validation/routing_mappings/coverage.json)；这些类别互斥，不将参考资料计作评测。

默认分类器也使用目录内的 Bedrock GPT-6 Sol US inference profile、low 档。已有 Strands/Jev/Liquid/H2O 可以作为分类器对照；无论选哪一个分类器，下游目标池都来自同一个 Bedrock 目录。

当前没有新增真实业务结果，校准文件明确为 `uncalibrated`。系统可以运行基于榜单分项的初始策略，不能把这个状态解释为业务质量已校准、生产延迟有保证或已经节约费用。

## 一次分类请求，七个结构化属性

分类器接收原始任务和固定候选标签，不接收待选型号、价格或预设答案：

| 字段 | 含义 | 如何用于选型 |
| --- | --- | --- |
| `workflow` | 客服、代码、核算、向量、文本，以及视觉、图像、视频、语音、重排 | 模态过滤、选择对应能力分项 |
| `difficulty` | simple / moderate / complex | 调整均衡档的取舍 |
| `stronger_model_benefit` | low / medium / high | 与难度共同决定是否值得优先能力 |
| `latency` | normal / sensitive | 有同端点业务测量时，纳入延迟维度 |
| `tools` | 是否需要实际调用外部工具 | 检查已核实的工具能力 |
| `structured_output` | 是否要求供应商强制 schema 输出 | 检查已核实的结构化输出能力 |
| `languages` | en / zh / en_zh / other | 选择对应语言的检索成绩 |

写 webhook 代码不等于需要分类器调用外部工具；提示中明确区分。解析时要求七项完整且全部来自固定标签，缺字段、非法值和调用失败不会被补成默认答案。

Bedrock 使用一次 Converse 请求；System One 分类器使用一次包含七个问题的请求。一次网络请求不代表一次模型前向计算，多问题模型的实际推理耗时仍需测量。

## Bedrock 候选与配置

先按账户、区域、生命周期、输入/输出模态筛选，再检查模型允许/排除列表和参考费用限制。指定了工具、结构化输出或上下文要求时，缺少能力证明的模型不进入候选。当前目录缺少这些详细字段，因此相关严格请求可能返回 `no_route`。

账户已授权不代表有推理配额。AWS Service Quotas 的账户实际配额按 **区域＋精确 invoke_id** 绑定；已核实为零的端点在选型前排除，直接目标执行也会复核。当前已核验 48 个型号的配额；Kimi K3 和 GLM 5.3 的 US 跨区域 Token 配额为 0。其余未映射项保留未知，不借用 Mantle 或 global 端点的配额。

正配额不代表实时剩余额度或端点健康。零配额记录保持排除，配额恢复后需要重新采集并发布目录；刷新会改变目录版本，使旧路由凭据失效。只更新配额、不刷新模型列表和评测：

```bash
uv run python -m scripts.refresh_routing_catalog --quotas-only
```

采集在部署环境执行，API 不需要新增 Service Quotas 权限或网络出口。之后按[网关发布步骤](MODEL_GATEWAY.zh.md#ecs-后端)构建并部署 API 镜像。映射缺失或采集失败时不会覆盖已有记录；完整模型目录刷新也会采集已映射配额。映射仅验证了当前 `us-east-1`，其他区域需先核对。

当前执行演示接收文本，支持文本 Converse 和 Titan/Cohere 文本向量。其余类别仍在目录中展示；如果没有该类别的可比评测，返回具体排除原因，不借用文本分数选图像/视频模型。

候选单位为 **型号＋测试配置＋请求参数**。当前大多数型号只有一个已测档位。每个候选有稳定 `configuration_id`，执行沿用对应的 `request_fields`。

同一型号的其他批次或档位保存在 `configurations`，每条均有自己的来源、数据集版本、请求参数、价格来源和任务分项。配置 ID 绑定型号、推理配置、采样参数及数据集版本。配置待核验的例子包括 Fable 5 的公开评测含 Opus 回退、GPT-OSS 的默认 effort 未明确、Qwen 3 32B 使用额外 `/think` 提示；不能把这些成绩直接套用到当前 Bedrock 请求。

## 评测批次与数据刷新

LiveBench 已将旧榜单文件迁移到 `/v1/`。本次接入 2024-06-24、2024-11-25、2025-04-02、2025-04-25、2025-05-30、2026-01-08、2026-06-25 七个批次，每条成绩保留批次。旧批次未定义 Agentic Coding 时，代码类别使用该批次实际存在的 Coding 分项；已定义却未完成的分项仍为未知，不补零。

`routing_preferences.benchmark_release` 默认 `auto`：在通过限制的候选中，先选择覆盖独立型号最多的可比组，同数时取较新批次，再在组内进行成本/能力选择。也可指定日期；指定批次没有候选时返回 `no_route`，不会暗中换批次。不同来源、数据集或分项集合不会进入同一个能力比较池。此选项用于文本评测，向量继续使用对应 MTEB 数据集。

工作台「更多约束与运行模式 → 文本评测批次」提供对应选择。例如 2025-04-02 有 Nova / Gemma / Llama，2026-01-08 有 Claude 4.5 / Devstral / MiniMax 等。模型页可搜索、按证据状态筛选并展开各批次成绩。已有文本成绩不证明视觉、语音等其他模态能力。

```bash
uv run python -m scripts.refresh_routing_rankings \
  --archive-dir validation/routing_mappings/new-source-snapshot
```

映射由 `app/routing_mappings.json` 和 `app/routing_registry.json` 的显式 ID 定义，不使用模糊名称匹配或剥离容量后缀。脚本保留公开 CSV、分类定义、官方配置 YAML 和 OpenRouter 公开型号 API 的原文及 SHA-256；取不到必需成绩或检测到参考别名版本变化时不覆盖已有榜单。

价格优先使用对应 LiveBench 配置的参考价，缺失时仅使用精确型号的 OpenRouter 参考价，保留来源和采集时间。OpenRouter 的 canonical slug 被固定校验；例如 `mistralai/mistral-large` 已指向 2407，因此没有把该报价关联给 Bedrock 2402。未知价格保持未知；这些价格均不是 Bedrock 实际报价。刷新后需构建并发布 API，新榜单版本会使旧路由凭据失效。

## 程序如何选择

选择过程不再请求决策模型，也不通过提示词要求它做价格运算。

首先应用显式限制：

- `max_reference_cost_usd`：用输入估计和实际输出上限，按参考平价估算；恰好等于限制可通过，未知价格不能通过。
- `min_success_rate`：要求匹配业务队列的成功率 **95% Wilson 下界** 达标。没有业务数据时不能用榜单分数替代。
- `max_latency_ms`：要求匹配业务队列测得的 P95 达标。没有测量时返回无候选；历史 P95 也不是对未来单次请求的保证。
- `min_context_tokens`：要求目录中已核实的上下文容量达标。

通过限制后，按同一类别的可比能力证据与费用形成非劣候选集合。能力代理采用“相对于候选最佳值的最大分项短板”：

```text
quality_regret(m) = max_k [ best_score(k) - score(m, k) ]
```

客服和文本使用 Language/IF；代码使用 Coding/Agentic Coding；核算使用 Reasoning/Mathematics/Data Analysis；向量按需求使用 en、zh 或两者。不会平均不同任务的榜单总分。

三档程序策略：

| 请求档位 | 程序行为 |
| --- | --- |
| low | 在非劣候选中选择费用最低的配置，能力短板用于同价比较 |
| medium | 将能力短板和费用分别按当前非劣池范围归一化，最小化其中最大的偏离；有可比延迟且任务敏感时加入延迟 |
| high | 优先选择能力短板最小的配置，再比较费用 |

显式 low/high 不被分类结果覆盖。medium 遇到“简单＋更强模型收益低”采用 low，遇到“复杂＋收益高”采用 high，其余保持 medium。响应同时保留请求档位、有效档位和原因。

这是透明的初始取舍规则，不是 OpenRouter 的内部权重，也不是已训练的最优策略。用户指定的成功率和延迟要求独立于三档策略，不会被自动放宽。

有业务证据时，能力维度替换为校准集的成功率 Wilson 下界，费用和延迟使用该队列的测量；同一轮不混用业务成功率和榜单分数。有价格的候选不与未知价格候选混作“最便宜”比较；全部价格未知且没有费用限制时仅按能力选取，明确标记 `unknown_quality_only`。

## 业务校准入口

工具不调用模型，也不会生成或补齐业务结果：

```bash
# 导出当前 Bedrock 型号与已测配置，供真实业务测评使用
.venv/bin/python -m scripts.calibrate_routing \
  --export-configurations --output /tmp/bedrock-configurations.json

# observations.json 必须来自真实、独立标注的任务测评
.venv/bin/python -m scripts.calibrate_routing \
  --observations /path/to/observations.json \
  --minimum-samples 30 --ttl-days 30 \
  --output /tmp/routing-calibration-candidate.json
```

输入包含目录和评测版本，以及逐次观察：

```json
{
  "catalog_version": "<目录版本>",
  "ranking_version": "<评测版本>",
  "observations": [{
    "case_id": "coding-case-001",
    "split": "calibration",
    "configuration_id": "<导出的配置 ID>",
    "profile": {
      "workflow": "coding", "difficulty": "moderate",
      "stronger_model_benefit": "medium", "latency": "normal",
      "tools": "no", "structured_output": "no", "languages": "zh"
    },
    "input_tokens": 1000,
    "max_output_tokens": 4096,
    "passed": true,
    "latency_ms": 1200,
    "total_cost_usd": "0.005",
    "cost_scope": "reference_usd"
  }]
}
```

示例仅展示字段，不是实测结果；单条记录不能通过校准工具的样本量检查。

按任务类型、难度、语言、能力要求、输入长度档和输出上限分队列。每个队列都必须有独立 calibration/validation 两组；两组覆盖同样配置，每组内所有配置覆盖相同任务，包括失败。重复任务/配置、跨组泄漏、缺失覆盖、样本不足和混合账单/参考价格都会被拒绝。

`passed` 应来自独立业务验收，而不是“接口成功”或路由器自己的解释。`total_cost_usd` 应覆盖该测评路径中的失败、重试及约定的路由摊销；工具无法从一个总额字段证明所有账单已被纳入，采集方必须统一计费口径。

校准集提供运行时排序数据，验证集仅用于报告，不能据验证集反复调阈值。对每种策略、固定强模型和固定便宜模型，应另报验证集质量、端到端延迟与每成功任务费用。每组 30 个任务只是导入下限，不能据此保证小幅质量差异已获得统计证明。

输出是候选校准文件；审阅真实测评结果后，将其安装为 `app/routing_calibration.json`。文件绑定目录/评测版本、SHA-256 校验值和有效期。版本不匹配或过期时不再声称业务校准有效；若请求设定业务质量/延迟硬要求，则返回无候选。

## API 与执行

`GET /api/routing/catalog` 新增 `classified_routing`：策略版本、校准版本、七个分类字段及按类别的可用型号/配置清单。

`POST /api/routing/auto`：

```json
{
  "task": {
    "text": "编写具有验签、幂等和并发保护的支付 webhook",
    "routing_preferences": {"cost_tier": "medium"}
  },
  "provider": "bedrock",
  "mode": "live",
  "catalog_version": "<目录版本>",
  "ranking_version": "<评测版本>",
  "policy_version": "bedrock-classified-v1"
}
```

响应包含两个可审计步骤：分类模型输出和 `deterministic_policy` 选型。返回实际分类输入、七项属性、配置候选、排除原因、非劣集合、选择原因、成本口径和版本。无候选是 `status=no_route`，不会改选另一类别。

成功 live 选型返回 15 分钟有效、绑定当前用户的 `route_token`。`POST /api/routing/auto/execute` 只接收该 token；从已有共享存储恢复任务和分类，再检查版本、候选及所选配置。客户端不能通过替换 profile/model_id 来改变这条路径的选择。回放不生成执行 token。

页面默认“分类后按策略选型”和 Bedrock 分类器，也保留原始、增强证据、模型直接均衡选型作对照。Bedrock 单独分类不需要启动本地 GPU；本地分类器对照按顺序调用。

## 尚未包含

实时端点报价、缓存读写、准确目标 tokenizer、供应商动态负载分配、会话粘性、受控重试与自动升级仍需后续接入。当前引用价和输入粗估不是真实花费上限；生产成本与性能收益需要真实分类器及下游模型的配对实测证明。
