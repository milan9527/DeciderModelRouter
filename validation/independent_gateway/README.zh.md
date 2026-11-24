# 独立服务验证

本目录记录 DeciderModelRouter 独立服务的测试和 AWS 验证。测试桩、真实模型调用和公开评测映射使用不同口径。

## 本地验证

- **439 项软件测试通过**，一个既有 Starlette 弃用提示。覆盖 SQLite / DynamoDB 事务、用户隔离、分页、保存失败时停止推理、重复提交、限流错误、密码/禁用会话失效、评测差异和独立部署约束。
- [镜像冒烟](image-smoke.json)：实际 ARM64 镜像在只读文件系统运行，通过登录、20 个模板、回放保存、账户角色和旧接口未加载检查；真实模型调用为 0。
- [最终浏览器验收](browser-2026-11-24-c/verification.json)：独立前端、API、GPU 测试桩三进程联调，6 次合成 GPU 请求、0 次真实模型调用，无 JavaScript 错误。验证历史跨刷新/重新登录保留、账户隔离、会话改名、人工验收、20 个模板、分类评测和手机布局。
- [构建与测试记录](local-verification.json)保存 API 源文件校验和与 ECR 镜像摘要。发布前源码与镜像构建输入一致。

`browser-2026-11-24-a` 保留中途验收截图：脚本曾使用随复选框状态改变的定位器，第二次点击时找不到目标；修正定位器后 b、c 两轮通过。浏览器合成响应的时延及一致率只用于检查界面，不属于真实模型成绩。

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
