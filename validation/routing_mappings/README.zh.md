# Bedrock 评测映射扩展

2026-11-24。针对网关大量型号显示“暂无精确评测映射”的问题，补充真实公开成绩、修复数据源地址并开放评测批次选择。完整逐型号状态见 [coverage.json](coverage.json)。

## 覆盖范围

| 状态 | 型号数 | 含义 |
| --- | ---: | --- |
| 有评测、配置已映射 | 51 | 仍需通过账户、生命周期、任务分项和配额筛选 |
| 有评测、配置待核验 | 9 | 能查看来源，不能自动选用 |
| 只有型号资料和参考价 | 14 | 不把资料或价格转换成质量分 |
| 容量变体待独立评测 | 11 | 不继承基础型号成绩 |
| 待专用任务或模态评测 | 29 | 包括向量、图像、语音、重排等缺少对应成绩的型号 |
| 未找到接入范围内成绩 | 10 | 保留未知 |
| **合计** | **124** | 其中有精确评测映射的型号由 **25 → 60** |

新增 35 个精确型号映射，包含 Nova、Llama、Gemma、Claude 4.5、Devstral、MiniMax、Qwen 等系列。配置待核验的 9 个型号也计入“有映射”，但不会执行。不能把 60 解读为 60 个健康端点，或 60 个都有同一批次、同一任务的成绩。

## 可核查依据

- [公开来源存档清单](sources/manifest.json)：LiveBench 七个批次的原始 CSV/分类 JSON、官方模型配置 YAML、OpenRouter 公开型号 API 原文，逐份记录 URL 与 SHA-256。
- LiveBench 旧文件已迁移到 `https://livebench.ai/v1/`；刷新器修复旧地址，并保留每条成绩的数据集版本。未把 LiveBench 2.0 的其他任务分数混入现有类别。
- 基础映射见 `app/routing_mappings.json`，补充注册表见 `app/routing_registry.json`。全部为显式 ID 对应；未使用名称近似、容量后缀剥离或价格高低推测质量。
- 外部价格有独立来源。`mistralai/mistral-large` 当前指向 2407，已拒绝关联给 Bedrock 2402；其他参考别名绑定 canonical slug，未来版本漂移会使刷新失败。
- 48 个调用端点已有账户配额快照；继续排除零配额的 Kimi K3 和 GLM 5.3。API 无新增 Service Quotas 权限，配额采集在发布环境进行。

每次路由只使用一个可比批次。自动模式按独立型号覆盖数、再按批次新旧选择组，随后才比较能力和成本；用户可在更多约束中指定日期。例如 2025-04-02 可使用 Nova / Gemma / Llama。旧数据未包含 Agentic Coding 时，显示实际存在的 Coding 分项，不补造分数。

## 本地验证

**424 项测试通过**，覆盖精确 ID、缺失分数/价格、别名漂移、批次隔离、配置待核验禁止选用、显式批次无候选、配置 ID 绑定、采样参数传递，以及同名发布目录的缓存刷新隔离。此前输入兼容测试改为使用其原始榜单快照，历史捕获记录未改写。

[三进程浏览器验证](browser-final-2026-11-24/verification.json)通过：指定批次进入真实请求结构、Nova 分项可展开、Qwen 资料不计为评测、配置待核验可筛选、MTEB 分数按 nDCG@10 × 100 展示，以及登录、流式阶段、失败、导出与手机布局。该批使用本地合成响应，真实模型调用为 0。

[Nova 来源界面](browser-final-2026-11-24/mapping-nova.png)、[工作台](browser-final-2026-11-24/desktop.png)、[手机](browser-final-2026-11-24/mobile.png)。`browser-local-2026-11-24` 为此前通过的中间界面，保留原结果。

## 发布与真实验收

通过 AWS SDK 直接发布现有 ECS 与 S3/CloudFront，不使用 CloudFormation。ECS revision **36** 已稳定，任务 1/1 且健康；镜像摘要 `sha256:1c81c60024b5fef87a1a209f7b821bf7ecf3276867d2eb323df4ad635e7c147f`。原 `g6.xlarge` / L4 实例保持运行，本次未启停或新建实例。

四个前端文件在 S3、公网与本地一致，OAC 和四项 S3 公共访问阻止保持启用。发布核查发现同名输出目录导致 CloudFront 复用旧请求，发布器已改用每次唯一标识，并补做刷新 `I3O8BYY1RP3T2TYQKPN3H10YPF`（Completed）。原发布及补充记录分别保留。

[真实线上验收](aws-live-2026-11-24/verification.json)通过，指定 `2025-04-02` 批次后实际选择并执行新增映射：

| 模型 | 目标耗时 | 输入 / 输出 Token | 参考费用 | 完整性 |
| --- | ---: | ---: | ---: | --- |
| Gemma 3 4B | 529.354 ms | 21 / 32 | $0.00000244 | end_turn |
| Nova Micro | 566.802 ms | 22 / 59 | $0.00000903 | end_turn |

Gemma 浏览器请求后端总耗时 1,096.108 ms；Nova 通过限制允许型号进行补充验证，见 [Nova 原始结果](aws-live-2026-11-24/nova-run.json)。合计 **3 次真实 H2O 分类、2 次真实 Bedrock 执行**，无自动重试。样本用于验收映射和调用链路，不代表业务正确率、延迟分位数或 AWS 账单；参考价不含 GPU。

登录、流式阶段、候选、零配额排除、来源展开、导出、手机布局和退出登录均通过，无 JavaScript 错误。[线上来源界面](aws-live-2026-11-24/mapping-nova.png)、[桌面](aws-live-2026-11-24/desktop.png)、[手机](aws-live-2026-11-24/mobile.png)。版本、部署状态与完整覆盖保存在 [coverage.json](coverage.json)。
