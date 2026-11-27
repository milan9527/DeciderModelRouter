# Bedrock临时错误退避与恢复

日期：2026-11-27。针对明确的限流、暂时不可用、模型未就绪和内部错误，网关增加同端点退避重试，持续失败才重新选模。此轮修改执行恢复与审计，没有训练新权重，也没有重新测量104项质量通过率或Liquid决策延迟。

## 行为与边界

| 情况 | 处理 |
|---|---|
| `ThrottlingException`、`ServiceUnavailableException`、`ModelNotReadyException`、`InternalServerException` | 未产生内容时允许恢复 |
| 同一端点第一次临时失败 | 等待后沿用原参数和上下文重试；不增加GPU决策 |
| 同一端点第二次失败 | 冷却该端点，将剩余合格候选交给GPU；原能力、预算和允许/排除约束保持生效 |
| 次数与时限 | 每端点最多2次，最多3个不同端点、6次目标调用；等待、换模和执行共用180秒预算 |
| 第1–5次失败后的等待 | 抖动范围依次0.5–1、1–2、2–4、4–8、4–8秒；有效的`Retry-After`秒数或HTTP日期作为下限 |
| 剩余预算不足 | 若等待后不足1秒尝试预算，保留原错误并停止；等待结束后再次检查预算 |
| 参数/权限/内容过滤、超时、未知网络错误 | 不自动重放 |
| 已有文本、推理或工具输出片段 | 不自动重放，保留部分输出 |
| 原生媒体/异步任务 | 不自动重试或提交第二个生成作业 |
| 用户关闭恢复或取消请求 | 关闭后仅调用一次；取消立即终止等待及后续恢复 |

持续失败按用户和端点冷却至少60秒，较长的`Retry-After`延长冷却，到期恢复候选资格。首次失败重试成功不会留下错误冷却，也不删除其他并发请求的故障状态。等待期间释放Bedrock并发槽，SDK内部重试仍关闭，避免叠加重试。

历史与页面分别记录原模型重试次数和换模次数；每次尝试保存AWS错误码、RequestId、上游HTTP状态、Retry-After、计划退避、恢复计划和部分输出状态。成功结果费用只包括有已知用量的调用，失败调用费用未知。详细协议见[会话路由文档](../../../docs/SESSION_ROUTING.zh.md#沿用模型与回退的边界)。

## 软件与浏览器验证

- **704项软件测试通过**，包括新增23项故障恢复测试；唯一警告是既有Starlette/httpx弃用提示。[完整输出](pytest.txt)
- 故障注入覆盖四种可恢复错误、同端点恢复、流式首内容前恢复、推理/工具片段后禁止重放、六次调用上限、GPU候选池限制、Retry-After超过预算、取消、等待后预算复查、永久错误、原生媒体及非法/HTTP日期头。
- **三进程合成浏览器验证通过**：第一次调用注入`ServiceUnavailableException`，退避后原端点成功；页面与持久化历史显示重试1次、换模0次。登录、会话、权限隔离、回放、评测、证据筛选与手机溢出检查通过，无浏览器脚本错误。[检查清单](browser/verification.json) · [页面记录](browser/recovery-ui.json) · [历史记录](browser/recovery-history.json.gz) · [流式记录](browser/synthetic-run.json.gz)
- 修复浏览器测试桩跨进程缺少匿名候选映射，以及模板上限、决策耗时标签和公开证据的过期断言；合成GPU只用于集成检查，不证明选模质量。
- Ruff与两份修改的JavaScript语法检查通过。Python3.12 ARM64镜像内实际目录构建通过：128个型号、27个模板。[镜像构建](image-build.txt) · [82份应用/前端文件哈希](source-sha256.json)

[桌面恢复截图](browser/desktop.png) · [手机截图](browser/mobile.png)

复现软件与浏览器检查：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.check_gateway_browser --output artifacts/recovery-browser
```

浏览器服务和响应均为合成数据，不调用真实模型，其等待时间不作为Liquid性能指标。104项质量成绩及决策模型两阶段延迟仍属于[revision33的原线上批次](../online-general-2026-11-27/README.zh.md)；本次验证不能推出线上故障恢复率或回答正确率。

## 线上发布与正常调用核验

发布到既有ECS服务 **revision34**，Python3.12 ARM64镜像固定为 `sha256:fa6d13e6ec1d87cf8d6a970dbc5666c3ed7a735398f75f64fa35993edb3cd774`。服务稳定1/1；与revision33逐项核对IAM角色、容器配置、环境变量、密钥引用及运行平台，仅API镜像改变。沿用GPU与AgentCore V2，没有修改Liquid权重。

7个S3前端对象的发布字节哈希验证通过，CloudFront刷新 `IDNLOBXR49FPDDEPAHQSU20FGB` 完成；正式根域名200且无跳转，页面内容与本地一致，S3匿名读取403。82份应用/前端源文件与构建时一致，原104项评测的240个归档文件哈希全部保持一致。[发布记录](release.json) · [部署核验](deployment-verification.json)

通过正式CloudFront域名执行一次真实正常请求，要求仅输出17×23的数字。Liquid完成两次决策，选中 `openai.gpt-6.1-sol`，完整返回 **391**；重试0次、换模0次，持久化历史一致。目录公开恢复上限2/3/6，性能接口包含独立重试计数，三个修改的公网前端资源与本地逐字节一致。

[线上核验结果](live/verification.json) · [请求](live/request.json) · [事件流](live/events.ndjson.gz) · [历史](live/history.json.gz)。公开事件及历史移除了执行票据，未保存登录凭据。此次没有在线上注入Bedrock故障；真实正常调用与受控恢复测试分别报告，不能据此声称线上临时故障全部可恢复。
