# DeciderModelRouter

独立的 Amazon Bedrock 模型网关：GPU 决策模型识别任务，后端根据同批次评测、能力约束、参考费用和账户配额选择目标模型；前端展示分类、候选、执行与性能。

生产服务使用专用 **CloudFront + OAC + 私有 S3、ECS Fargate、DynamoDB、g5.xlarge GPU 和 VPC**。自建用户认证，不使用 Cognito；基础设施通过 AWS SDK 创建，不使用 CloudFormation。[打开在线工作台](https://d3eipg2g3l3an3.cloudfront.net/)，当前状态和资源见 [部署记录](deployment/DEPLOYED.md)。

- **会话与历史**：按账户隔离，跨登录和容器重启保存请求、阶段事件、输出、Token、费用、错误及人工验收，可检索、复用任务和导出。
- **性能**：按时间与会话筛选，显示路由 P50/P95、完整执行数、目标模型对比和已知费用；回放不计入性能，未知用量不按零费用处理。
- **自由输入与 35 个模板**：问候、日常对话、客服、代码、翻译、推理及媒体任务；模板只填写示例，不参与匹配。GPU 读取实际请求、附件类型和输出要求。
- **多模态**：图片/视频理解、音频转写、英语语音生成、图片编辑和文档重排；附件及生成结果私有保存，可从历史播放、预览和下载。当前区域无可用纯文生图或视频生成型号，页面明确提示。媒体按能力选型，质量与完整费用仍待测。
- **端到端效果评测**：87 个用例、25 类场景，支持自定义任务；实际运行分类、选模与目标模型，对比低价/高能力策略，保存输出、质量、耗时、费用和缺口。明确答案自动校验，开放答案区分用户/助手评审，代码用隔离功能测试；未测与未知价格不补零。[方法](docs/effectiveness-evaluation.zh.md) · [完整场景](docs/effectiveness-scenarios.zh.md) · [16384-token 全量复测](validation/independent_gateway/full-effects-16384-2026-11-25/README.zh.md) · [2048-token 原报告](validation/independent_gateway/full-effects-2026-11-25/README.zh.md)。
- **分类诊断**：保留 Decider / Liquid / H2O 七项分类参考一致率。24 个有标签的开发样本，每次最多选 16 个；分类标签一致率不等于业务正确率。
- **整体验收（主指标）**：统一以全部计划场景为分母，错误路由计入未通过；待评、未测单列并留在分母。最新全量批次 Decider **47/87（54.0%）**、Liquid **58/87（66.7%）**、H2O **57/87（65.5%）**，分别为44、55、54个答案通过加3次预设边界正确拦截。原“44/50”等仅作已评分答案的辅助指标，历史输出与评审保留。[口径与验证](validation/independent_gateway/overall-metrics-2026-11-25/README.zh.md)。
- **评测诊断**：“任务类型判断正确”检查用途标签是否匹配；完整答案不合格、输出未完成和调用错误分开统计。新效果评测默认输出上限为 16384 tokens，历史结果显示当时预算。[指标与预算调整](validation/independent_gateway/metrics-budget-2026-11-25/README.zh.md)。
- **失败原因与优化结论**：区分多模态内容质量、格式要求、能力未接通及通用路由误判；记录3项能力边界用例的指标归类问题，明确尚未修复。[完整分析与改进顺序](validation/independent_gateway/overall-metrics-2026-11-25/failure-analysis.zh.md)。
- **账户管理**：管理员创建、禁用、恢复和重置用户；用户修改密码；密码使用 scrypt，认证会话保存在专用数据库。
- **目标模型证据**：124 个 Bedrock 型号，60 个有公开评测映射，其中 9 个配置待核验。配置未核验的不参与自动路由，业务校准仍为 `uncalibrated`。

[独立服务架构、开发与部署](docs/INDEPENDENT_SERVICE.zh.md) · [路由机制与评测标准](docs/BEDROCK_CLASSIFIED_ROUTING.zh.md) · [独立服务验证](validation/independent_gateway/README.zh.md) · [评测映射来源](validation/routing_mappings/README.zh.md)

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

## 验证与项目历史

```bash
.venv/bin/python -m pytest -q
# 浏览器验证额外需要 Playwright 和 Chromium
uv pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/check_gateway_browser.py --output .deployment/browser-check
```

浏览器脚本使用合成服务，只验证交互与集成，不生成模型性能结论。真实 AWS 验证另存于 [validation/independent_gateway](validation/independent_gateway/README.zh.md)。

项目从 Strands Decider 实验代码演进而来。旧支付、视觉和多模型对比说明保留在 [LEGACY.md](LEGACY.md)；独立生产入口为 `app.gateway_main:app`，只加载模型网关及账户功能。
