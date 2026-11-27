# DeciderModelRouter

独立的 Amazon Bedrock 模型网关。默认使用 **Liquid d1-2B**，先通过一次GPU调用评估任务需求，再通过一次GPU调用依据公开评测、价格和运行延迟选择目标配置；后端校验能力、预算与配额，前端展示决策、执行和历史。

[打开在线工作台](https://d3eipg2g3l3an3.cloudfront.net/) · [最终线上测试](#最终线上测试2026-11-27) · [分类场景通过率](#分类场景通过率) · [延迟与费用](#性能与费用) · [微调与优化](#当前微调与优化) · [本地开发](#本地开发) · [历史记录](docs/INDEPENDENT_GATEWAY_HISTORY.zh.md)

## 最终线上测试（2026-11-27）

通过正式CloudFront入口测试 **ECS revision33、真实AgentCore V2**，共104项：原72项开发回归，加32项首次执行前冻结的通用任务。测试期间部署、目录、路由和价格版本保持一致，沿用已部署微调权重。

| 测试范围 | 严格通过 | 内容正确 | 执行完成 |
|---|---:|---:|---:|
| **全部计划项** | **100/104（96.15%）** | **102/104（98.08%）** | **103/104** |
| 原72项开发回归 | 68/72（94.44%） | 70/72（97.22%） | 71/72 |
| 新增32项通用任务 | 32/32（100%） | 32/32（100%） | 32/32 |

严格通过要求执行完整、内容正确并遵守格式；失败和未测项均保留在分母。新增通用任务覆盖知识解释、摘要改写、翻译语言、约束写作、日常规划、数学推理、结构化处理及真实线上多轮，每类4项。

### 分类场景通过率

| 分类 | 严格通过 | 内容正确 |
|---|---:|---:|
| 新增通用任务：8类，每类4项 | 32/32 | 32/32 |
| 通用文本与历史 | 13/13 | 13/13 |
| 代码 | 7/7 | 7/7 |
| 检索 | 未测（1项） | 未测（1项） |
| 多模态：4图片、1转写、1视频 | 6/6 | 6/6 |
| 原12行业业务 | 10/12 | 12/12 |
| 复杂产线分析 | 0/1 | 0/1 |
| 能力优先专项 | 12/12 | 12/12 |
| 既有16行业任务 | 16/16 | 16/16 |
| 线上AgentCore V2：两行业各两轮 | 4/4 | 4/4 |

7道代码题的 **973项隔离功能检查全部通过**。三项严格未通过和一项未测保留如下：

- **保险、能源各1项格式失败**：数值20000和105.6正确，但输出多加“元”，违反裸数字要求。
- **产线分析1项超时**：选择Sol6.1 max后约137秒仍无完整答案，未重试或切换模型。
- **检索1项质量未测**：接口成功生成向量，但仅返回12维预览，无法复算完整检索排序。

### 性能与费用

106次选模均调用GPU两次，实际回退0次。下表采用同一线上批次的测量，路由时间不含目标生成。

| 计时范围 | 有效计时样本 | P50 | P95 |
|---|---:|---:|---:|
| ECS记录的完整路由 | 106 | 900ms | 1622ms |
| 目标模型生成（成功调用） | 105 | 1733ms | 24333ms |
| 普通任务公网HTTP全程 | 100 | 3787ms | 32518ms |

P50为中位数，P95为95分位，数值四舍五入到毫秒；均为对应阶段全程耗时，不是首字延迟，各分位数不能直接相加。路由和目标统计包含Agent内部调用；公网HTTP仅统计100个普通任务，包含产线超时。超时调用没有完整目标生成计时，不进入105个成功调用的目标延迟分布，约137秒的失败另列于上文。

105份成功执行输出中104份具备已知参考费用，合计 **$0.91504637**；失败调用和缺失用量不按免费计算，合计不含GPU、网络等基础设施费用，也不是实际账单或成本节省证明。普通请求并发上限2，Agent随后执行，本批次不用于推导高并发容量。

**评测边界**：上述结果衡量网关及下游模型的整体任务完成质量，不是Liquid独立选模正确率或最佳模型命中率。旧题属于开发回归，新题是人工构造的小规模首次测试；开放题由助手按冻结标准非盲复核，并绑定原始回答哈希。未做全部候选的同题穷举对照，不能据此证明通用准确率或成本最优。

[完整线上报告](validation/independent_gateway/online-general-2026-11-27/README.zh.md) · [逐项结论](validation/independent_gateway/online-general-2026-11-27/cases.zh.md) · [新增通用任务完整回答](validation/independent_gateway/online-general-2026-11-27/general-cases.zh.md) · [机器可读汇总](validation/independent_gateway/online-general-2026-11-27/summary.json)

## 当前微调与优化

### 两阶段GPU决策与匿名证据

当前策略为 `gpu-evidence-anonymous-table-v6`。第一阶段评估七项任务需求；第二阶段从全部满足运行约束的配置中选模。能力优先、均衡、成本优先作为任务选项交给GPU，默认均衡；程序负责接口、权限、配额、上下文和显式预算等硬约束，不增加质量Top-K、质量淘汰或额外质量重选。

第二阶段用 `m000` 等匿名编号和共享证据表提供候选，服务器映射回精确执行配置。文本质量采用LiveBench相关分项，同批次完整相关分项另提供辅助均值；多模态使用对应专项依据。原始分数、测试与执行档位、费用、延迟及未知值一并保留，不跨批次混算，不用业务回归成绩补分。

### Liquid LoRA选模微调

部署使用2026-11-26训练的固定选模适配器，仅作用于**第二阶段、具有LiveBench主要批次证据的文本选模**。第一阶段需求判断和多模态仍使用原始权重；Decider、H2O可作为其他决策模型切换，但未加载这份适配器。

| 项目 | 实际配置 |
|---|---|
| 基座与参数 | 冻结Liquid d1-2B的BF16基座，在11个文本注意力 `o_proj` 层训练LoRA；rank 8、缩放系数2，附加A/B参数为float32 |
| 训练数据 | 7个已见路由上下文生成160条训练、32条开发验证；候选数量12/24/36/48，随机重编号、打乱顺序并使用合成价格 |
| 训练目标 | 同批次LiveBench相关均值减去费用惩罚，转成软标签；能力／均衡／成本偏好的费用权重分别为0／8／20，强模型收益高时减半 |
| 训练过程 | AdamW，学习率0.0002，两轮共320步，约466秒；按开发集交叉熵选择第240步权重 |
| 上线校验 | 启动核验适配器SHA-256、基座版本、模块和张量；每次响应记录权重版本与是否启用微调 |

这些训练样本是7个上下文的变体，**不是192个独立业务任务**。训练拟合自定义质量—价格规则，未使用业务答案或人工答题评分；开发集首选标签符合26/32不代表业务正确率。匿名化和微调后仍存在候选顺序敏感性，详见[历史诊断与对照](docs/INDEPENDENT_GATEWAY_HISTORY.zh.md#匿名证据与lora价格更新前72项回归)。端到端结果同时受输入格式、运行依据和下游输出影响，不能全部归因于微调。

[训练说明、权重与复现](validation/independent_gateway/anonymous-evidence-2026-11-26/training/README.zh.md) · [训练代码](validation/independent_gateway/anonymous-evidence-2026-11-26/training/train.py) · [完整选模机制](docs/PUBLIC_BENCHMARK_EVIDENCE.zh.md)。基座和衍生权重适用附带商业规模条件的[LFM Open License v1.0](validation/independent_gateway/anonymous-evidence-2026-11-26/training/BASE_MODEL_LICENSE.txt)，独立于网关代码许可。

### 推理、价格与可观测性

- **推理路径**：Liquid在现有A10G上使用原生Transformers BF16，第二阶段启用编译加速，第一阶段保持eager；正常请求仍为两次GPU调用。当前性能以本页线上实测为准。
- **价格覆盖**：发布快照中84个文本型号、106个合格配置均可估价；103个配置采用精确匹配的AWS单价，3个保留明确标注的外部参考价。合并Bedrock与FoundationModels两个官方价目表，区分区域、Global、长上下文和缓存费率；缺少实际媒体用量时总费保持未知。[价格规则与维护](validation/independent_gateway/pricing-2026-11-26/README.zh.md)
- **Agent首轮决策**：从第一次目标调用起，GPU接收完整任务、实际工具说明和系统约束；工具结果与用户要求分开。选型保存后再调用目标模型，后续每步继续路由。
- **可观测性**：工作台、历史与Agent步骤展示候选证据、选择概率、第二名差距、权重及快照版本。选择概率表示模型倾向，未经业务成功率校准。性能按路由、目标生成、缓存、费用和回退分别统计。

## 功能与部署

生产服务使用专用 **CloudFront + OAC + 私有S3、ECS Fargate、DynamoDB、g5.xlarge GPU和VPC**。自建用户认证，基础设施通过AWS SDK创建。当前资源和部署说明见[部署记录](deployment/DEPLOYED.md)。

[![独立模型网关AWS部署架构](docs/diagrams/decider-model-router-aws.zh.png)](docs/diagrams/decider-model-router-aws.zh.svg)

[服务架构与请求时序](docs/INDEPENDENT_SERVICE.zh.md) · [决策流程](docs/BEDROCK_CLASSIFIED_ROUTING.zh.md) · [下载SVG](docs/diagrams/decider-model-router-aws.zh.svg) · [下载高清PNG](docs/diagrams/decider-model-router-aws.zh.png)

- **行业Agent**：12个行业、每个4轮任务，支持自由追问。Strands Agent运行于AgentCore Runtime V2，经本项目模型入口调用原生工具和目标模型；业务记录明确标为合成数据。[行业说明](docs/INDUSTRY_AGENTS.zh.md)
- **会话与历史**：按账户隔离并持久保存，支持完整成功历史、轮次裁剪、并发保护及私有附件复用；每轮由GPU决定沿用或切换配置。[会话路由与API](docs/SESSION_ROUTING.zh.md)
- **缓存、流式与回退**：支持已核验型号的Bedrock原生缓存和流式输出；对尚未输出的可回退错误重新选模，含首选最多尝试三个端点；部分输出或超时不自动重试。
- **自由输入与模板**：27个模板包含6个简单通用场景、12个行业任务及检索、多模态示例。模板仅填写输入，不参与分类匹配，支持任意自由任务。
- **多模态**：图片／视频理解、音频转写、英语语音生成、图片编辑和文档重排；私有保存附件和产物。当前区域无可用纯文生图或视频生成型号，页面展示能力缺口。
- **评测与账户**：保存输出、评分、耗时、费用及缺口，支持自定义任务、人工验收和导出；管理员管理账户，密码采用scrypt。软件测试、分类标签一致率和业务通过率分别报告。[评测方法](docs/effectiveness-evaluation.zh.md)

## 本地开发

```bash
uv sync --python 3.12 --extra dev --extra speech
export AUTH_COOKIE_SECURE=false
export AUTH_DB_PATH=.data/auth.sqlite3
export GATEWAY_DB_PATH=.data/gateway.sqlite3
.venv/bin/python -m scripts.manage_users create admin --admin
# 终端一：独立 API
SERVE_FRONTEND=false .venv/bin/uvicorn app.gateway_main:app --host 127.0.0.1 --port 8000
# 终端二：前端与同源 API 代理
.venv/bin/python -m scripts.serve_gateway --api http://127.0.0.1:8000
```

打开 `http://127.0.0.1:5173/`。真实推理需要配置 GPU 服务地址及服务密钥；Bedrock 使用后端 AWS 凭据。未配置模型服务时可登录、管理会话和查看流程回放。详细环境变量见 [.env.example](.env.example)。

## 验证与文档

```bash
.venv/bin/python -m pytest -q
# 浏览器验证额外需要 Playwright 和 Chromium
uv pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python -m scripts.check_gateway_browser --output .deployment/browser-check
```

浏览器脚本使用合成服务，只验证交互与集成，不生成模型性能结论。真实 AWS 验证另存于 [validation/independent_gateway](validation/independent_gateway/README.zh.md)。

独立生产入口为 `app.gateway_main:app`。旧批次通过率、微调对照、策略实验、发布及功能验收集中在[独立模型网关历史记录](docs/INDEPENDENT_GATEWAY_HISTORY.zh.md)；旧支付、视觉和多模型对比见[LEGACY.md](LEGACY.md)，更早演示资料见[项目历史](docs/HISTORY.md)。
