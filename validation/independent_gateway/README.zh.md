# 独立服务验证

本目录记录 DeciderModelRouter 独立服务的测试和 AWS 验证。测试桩、真实模型调用和公开评测映射使用不同口径。

最新 [2026-11-25 全面路由效果评测](full-effects-2026-11-25/README.zh.md)：87 个用例、25 类，主批次 213 次 GPU 分类、166 次 Bedrock 调用；另外有 2 次分类、6 次目标调用的输出预算补测。实际运行低价/高能力对照，公开逐项输出、质量、性能、费用和评分依据。16 个能力/评测数据缺口、待评和未知价格明确保留。488 项软件测试与正式域名浏览器验证通过，ECS revision 6 已发布。

此前 [通用与多模态验收](general-media-2026-11-25/README.zh.md)：458 项软件测试；正式域名 10 次分类、8 次目标调用，包含 `hi`、图片/视频/音频理解、语音、图片编辑和重排。链路检查通过；独立语义检查发现一条图片颜色回答错误，已记录人工验收不通过。以下保留首版历史结果。

## 本地验证

- **440 项软件测试通过**，一个既有 Starlette 弃用提示。覆盖 SQLite / DynamoDB 事务、用户隔离、分页、保存失败时停止推理、重复提交、限流错误、密码/禁用会话失效、评测差异和独立部署约束。
- [首版镜像冒烟](image-smoke.json)：首版 `ff059` ARM64 镜像在只读文件系统运行，通过登录、20 个模板、回放保存、账户角色和旧接口未加载检查；真实模型调用为 0。
- [最终浏览器验收](browser-2026-11-24-c/verification.json)：独立前端、API、GPU 测试桩三进程联调，6 次合成 GPU 请求、0 次真实模型调用，无 JavaScript 错误。验证历史跨刷新/重新登录保留、账户隔离、会话改名、人工验收、20 个模板、分类评测和手机布局。
- [构建与测试记录](local-verification.json)保存最终 API 源文件校验和与 ECR 镜像摘要。最终 `2f29179` 镜像增加评测部署配置持久化，440 项测试通过，并由下述 AWS 真实调用验证。

`browser-2026-11-24-a` 保留中途验收截图：脚本曾使用随复选框状态改变的定位器，第二次点击时找不到目标；修正定位器后 b、c 两轮通过。浏览器合成响应的时延及一致率只用于检查界面，不属于真实模型成绩。

## AWS 真实结果（2026-11-24）

[专用资源与最终镜像](aws-deployment.json)、[三模型健康状态](gpu-health.json)、[GPU 认证与旧服务禁用检查](gpu-security.json)。新 `g5.xlarge` / A10G 24GB 位于 `us-east-1f`，没有公网 IP；G6 多可用区容量不足后使用 G5。私有镜像创建不重启原实例。

[路由与浏览器验收](aws-smoke-2026-11-24/verification.json)通过：两次 GPU 分类、一次 Bedrock 调用；重复提交返回 409，不产生额外推理。Gemma 3 4B 返回完整输出，21 输入 / 31 输出 Token，目标 601.442ms、后端总计 1329.034ms，参考费用 $0.00000239。两条记录存入 DynamoDB，刷新后仍可读，五个线上前端文件与源码一致，无 JavaScript 错误。

[48 条真实评测记录](aws-evaluation-2026-11-24/evaluation.json)：每个模型使用相同的 16 题，全部调用完成，均无失败。HTTP 分位数包含 API 决策排队与校验；16 条样本的最近秩 P95 等于该批最大值。

| 决策模型 | 七项一致 / 16 | 一致率 | HTTP P50 | HTTP P95 | 部署配置 |
| --- | ---: | ---: | ---: | ---: | --- |
| Decider 2B | 8 | 50.00% | 343.046ms | 1044.849ms | BF16 / vLLM 0.31；服务未返回部署标签，记录保留 null |
| Liquid d1 2B | 9 | 56.25% | 75.519ms | 124.895ms | BF16 / Transformers |
| H2O Lightning 4B | 7 | 43.75% | 509.621ms | 732.447ms | FP8 W8A16 / Marlin / vLLM 0.30 |

本批 Liquid 延迟最低、参考一致率最高；该结论只适用于这 16 条可见开发样本。逐字段分数和分歧保存于原始记录，未用于自动训练、改标签或替换业务校准。

本次验收共 **50 次应用 GPU 请求、1 次 Bedrock 请求**，不包含模型启动时的自检和预热。H2O 默认 CUTLASS FP8 路径在 A10G 上失败，显式使用 Marlin 后通过官方双向注意力探针（最大概率差 0.0151）与真实任务；新运行配置随结果入库并在展开详情中展示。

[完整线上验收](aws-evaluation-2026-11-24/verification.json)通过。[ECS 替换证据](aws-evaluation-2026-11-24/restart-persistence.json)记录替换前后不同的 Task ARN、相同最终镜像摘要、同一登录以及逐字段一致的运行与评测记录；[账户隔离](aws-evaluation-2026-11-24/isolation.json)验证跨用户 404、普通用户管理接口 403、禁用会话 401，临时测试用户已禁用。[账户界面补充检查](aws-evaluation-2026-11-24/accounts-ui.json)等待用户表加载后重新截图，确认管理员和禁用账户均显示，未增加模型调用。[AWS 隔离](aws-evaluation-2026-11-24/aws-isolation.json)验证专用 VPC/GPU、DynamoDB TTL/PITR、S3 匿名拒绝及原服务未变更。

## 真实验证方法

部署完成后依次执行：

```bash
# 2 次 GPU 分类，1 次 Bedrock 目标调用
python scripts/check_gateway_live.py \
  --url https://DEDICATED_DISTRIBUTION.cloudfront.net \
  --credentials .deployment/RELEASE/admin-credentials.json \
  --benchmark-release 2025-04-02 \
  --output validation/independent_gateway/aws-smoke-UNIQUE

# 16 个任务 × 3 个分类器，48 次 GPU 尝试、0 次 Bedrock 调用
# 随后替换本项目 ECS Task，验证认证与历史记录仍可读取
python scripts/check_independent_live.py \
  --resources .deployment/INDEPENDENT/resources.json \
  --gpu .deployment/INDEPENDENT/gpu.json \
  --credentials .deployment/RELEASE/admin-credentials.json \
  --smoke-report validation/independent_gateway/aws-smoke-UNIQUE/verification.json \
  --output validation/independent_gateway/aws-evaluation-UNIQUE
```

第二个脚本只允许 `decider-model-router` 集群，核对新 GPU 与原 GPU 不同。保存评测原始预期/实际值、失败、时延、账户隔离 HTTP 状态、替换前后 Task ARN、数据库记录一致性、S3 匿名拒绝和旧服务未变更证据。临时普通测试账户在测试后禁用；认证凭据和 Cookie 不写入此目录。

## 评测解释

16 个任务及其七项参考标签随模板版本冻结，是可见的开发参考集。全字段一致要求七项全部相同；单字段分数分别计算，调用失败保留在分母中。三模型共用任务和标签，不根据模型输出修改答案。

HTTP P50/P95 包含 API 决策调用的排队与校验，逐样本另存 GPU HTTP 往返。小样本统计仅描述本批次；不推导吞吐量、并发容量或业务正确率。分类评测不调用 Bedrock，目标质量证据仍来自单独的公开评测映射与业务验收，业务校准仍为 `uncalibrated`。
