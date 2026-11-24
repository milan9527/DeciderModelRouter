# 模型网关联调验证

2026-11-24 的本地联调使用测试桩，之后按用户要求实际发布 AWS。两批证据分别记录；本目录不提供 GPU 性能基准或 AWS 实际账单结论。

## 本地联调

本节全部模型调用使用本地测试桩，真实模型调用次数为 0。

- [最终验证结果](browser-final-2026-11-24/verification.json)：三个独立进程验证静态前端代理、FastAPI 和 GPU HTTP 协议。
- [桌面界面](browser-final-2026-11-24/desktop.png)、[候选依据](browser-final-2026-11-24/candidates.png)、[性能明细](browser-final-2026-11-24/timings.png)、[会话性能](browser-final-2026-11-24/performance.png)、[移动界面](browser-final-2026-11-24/mobile.png)。
- [合成事件记录](browser-final-2026-11-24/synthetic-run.json)：逐阶段事件、实际本地联调墙钟耗时；GPU 自报时间与输出为合成数据，执行凭据已移除。
- `browser-2026-11-24/` 是首次通过的中间版本；最终界面与结果以 `browser-final-2026-11-24/` 为准。

全量测试 **374 passed**；网关相关测试 20 项，覆盖认证、CSRF、GPU 单次调用、私有地址隔离、身份校验、过期版本、失败与回放边界、无候选、输出截断及断连取消。现有 Starlette/httpx 测试客户端弃用提示一项。

Ruff、JavaScript 语法和 ECS 生成模板一致性检查通过。测试进程已清理；完成本批本地验证时，新网关尚未发布到 AWS。

## 直接 SDK 发布

网关发布改为 `deployment/deploy_gateway.py`，直接访问 ECS、ECR、S3 和 CloudFront；不访问 CloudFormation，也不使用旧栈发布脚本。架构确定为 S3 + CloudFront + OAC 前端、ECS 后端、独立 GPU 服务。本轮添加到旧模板中的网关配置已移除。

[AWS 只读预检记录](direct-release-preflight-2026-11-24.json)基于发布前的 ECS revision 31：核对现有资源、OAC、S3 隐私和版本控制、API 禁止缓存，并将当时已部署镜像解析到 digest，生成网关配置预览。该预检使用旧镜像；新版网关镜像当时尚未构建或发布，预检的 AWS 写操作和模型调用均为 0。

新增 16 项发布测试通过，与原网关测试一同执行共 36 项通过，覆盖 Task 继承、不可变镜像、只读预检、错误目标、ECS 回滚、限定上传范围、S3 版本核验及失败记录。

## AWS 实际发布

[部署资源与结果](aws-deployment-2026-11-24.json)：新建 ARM64 API 镜像并推送 ECR，以不可变 digest 更新 ECS revision **32**；发布四个 S3 网关文件并刷新 CloudFront。指定的新 Task 稳定运行，S3 版本及 OAC 检查通过。发布前全量回归 **390 passed**，实际镜像离线启动、目录读取与策略回放通过。

GPU 复用现有 `i-0ae7eb0abcb815af3` / `g6.xlarge`，从停止状态启动；补齐与内核驱动匹配的 NVIDIA 615.71.09 用户态计算库和工具。未创建新 EC2，未使用 CloudFormation。在线入口：<https://d3i7ogfx5ml7dc.cloudfront.net/gateway/index.html>。

最终 H2O、Decider、Liquid 的独立模型健康检查与 ECS 到各服务的连接检查均通过，原有四个 systemd 服务运行，实例保持常驻。本批真实分类调用使用默认 H2O。

## AWS 真实请求验收

[验收结果](aws-live-2026-11-24/verification.json)通过。两次网关 H2O 分类请求、一次 Bedrock 目标执行；模型启动预热不计入网关请求次数。两个请求均完成七项画像并选中 `openai.gpt-6-luna`，没有使用测试桩或回放。

| 样本 | GPU HTTP 往返 | 路由总耗时 | Bedrock 执行 | 后端总耗时 |
| --- | ---: | ---: | ---: | ---: |
| HTTP 仅路由 | 885.893 ms | 898.221 ms | 未执行 | — |
| 浏览器路由并执行 | 600.831 ms | 635.553 ms | 1,284.118 ms | 1,958.303 ms |

浏览器样本返回完整输出，目标用量为 19 输入 / 100 输出 Token，按目录参考价计算 **$0.0000519**。这是两条部署冒烟样本，不足以说明吞吐、P95 或业务准确率；参考费用不含 GPU，也不是 AWS 实际账单。

- [HTTP 逐阶段原始记录](aws-live-2026-11-24/http-route.json)：首批事件在客户端约 253 ms 到达，完整路由在约 1,164 ms 到达，验证事件通过 CloudFront 分阶段送达。
- [浏览器真实请求与结果](aws-live-2026-11-24/browser-run.json)：导出已移除路由执行凭据。
- [桌面](aws-live-2026-11-24/desktop.png)、[性能明细](aws-live-2026-11-24/timings.png)、[390px 手机界面](aws-live-2026-11-24/mobile.png)。
- 登录、七项画像、四阶段完成、候选、会话计数、导出、退出登录及手机无横向溢出通过；无 JavaScript 错误，浏览器只请求同源地址。四个 CloudFront 文件与本地逐字节一致；未登录业务 API 返回 401，匿名 S3 访问返回 403。

复验使用 `scripts.check_gateway_live.py`，需已有账号文件及已就绪的 GPU；命令见[网关文档](../../docs/MODEL_GATEWAY.zh.md#验证)。复验会调用真实模型并产生用量。

## 代码场景限流修复与验收

[完整修复证据](quota-fix-2026-11-24.json)记录根因、失败样本、配额快照和最终 ECS revision **35** 发布。原代码请求选中已授权的 Kimi K3，但该账户的 US 跨区域 Token 配额 `L-51FF35D5` 实际为 **0**，与 `ThrottlingException` 一致；GLM 5.3 的对应额度也为 0。现在选模与执行都会排除这两个端点，页面保留可核查的配额依据。

修复过程中保留了两次额外问题，未覆盖或计作成功：

- [40 秒超时](aws-quota-fix-2026-11-24/browser-run.json)：配额过滤后选中 Sonnet 5，但旧客户端等待不足，返回 504；未收到上游用量。随后为目标生成设置独立 120 秒读取等待，分类客户端仍为 40 秒。
- [4096 Token 截断](aws-quota-fix-final-2026-11-24/browser-run.json)：全部输出预算用于推理，`max_tokens`、无最终答复。随后将代码示例显式预设为期望 8192、上限 16384，运行前可修改；费用约束仍按上限核算，API 默认值仍为 512 / 4096。

全量测试 **409 passed**，本地三进程浏览器联调和实际 ARM64 镜像离线检查通过。真实调用不自动重试、不降低评测推理配置；未启停 GPU，未使用 CloudFormation。运行时限流映射为 429，并提供重试间隔建议。

[最终真实验收](aws-code-budget-2026-11-24/verification.json)使用页面原始支付 Webhook 示例、均衡档和上述可见预算。H2O 分类后选中 `us.anthropic.claude-sonnet-5`，请求配置保持 adaptive / xhigh。返回处理函数和两个测试示例，`end_turn` 且存在最终文本；生成的测试未运行，不作为代码业务正确性评测。

| 样本 | GPU HTTP 往返 | 路由总耗时 | Bedrock 执行 | 后端总耗时 |
| --- | ---: | ---: | ---: | ---: |
| HTTP 仅路由 | 515.186 ms | 527.015 ms | 未执行 | — |
| 浏览器代码执行 | 510.612 ms | 521.695 ms | 82,486.917 ms | 83,238.824 ms |

目标返回 74 输入 / 8975 输出 Token，输出用量含推理，参考费用 **$0.134847**。这是一次完整代码响应的部署验收，不代表 P95 或任务成功率，参考价不是 AWS 账单，也不含 GPU。整轮修复合计 7 次网关 GPU 请求、4 次 Bedrock 目标尝试，包含原始限流复现、超时、截断和最终成功；失败用量未知时保留未知。

- [真实事件与输出](aws-code-budget-2026-11-24/browser-run.json)、[仅路由记录](aws-code-budget-2026-11-24/http-route.json)。
- [桌面](aws-code-budget-2026-11-24/desktop.png)、[零配额型号](aws-code-budget-2026-11-24/quota-models.png)、[候选与排除依据](aws-code-budget-2026-11-24/candidates.png)、[耗时](aws-code-budget-2026-11-24/timings.png)、[手机](aws-code-budget-2026-11-24/mobile.png)。
- 登录、四阶段完成、候选、配额原因、会话计数、导出、退出和手机无横向溢出均通过。四个公网前端资源与发布内容逐字节相同，无 JavaScript 错误。
