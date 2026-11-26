# 最终上线功能验证

2026-11-26，独立服务 **ECS revision 29**、路由 `gpu-evidence-quality-priority-v4`、质量约束 `quality-priority-v2`。本目录是部署后的功能诊断，**不加入56项质量回归分母，也不是三个决策模型的准确率对照**。

## 正式域名与模块

[最终汇总](final/summary.json)记录21项接口断言、8个网关请求和3个AgentCore任务。登录、退出后会话撤销、未登录历史拒绝、目录/会话/历史/性能/评测/Agent场景/账户读取均通过；8个相同请求ID的重复提交返回409，没有再次执行推理。账户创建/改密/跨用户权限及故障分支另由软件测试覆盖，本批没有修改真实用户账户。

[AWS核验](../deployment.json)：ECS一任务稳定运行，镜像 `sha256:dcec050d21fa47e6e540931d4d71d92eabf2793b9577fc3d161debed7b9fef25`；CloudFront刷新 `I2KF50C6CJ604VQGL7Q608ZYB1` 完成。根地址HTTPS直接200，无跳转；S3匿名访问403，OAC存在，五个前端文件逐字节匹配。继续使用已有g5.xlarge/A10G及AgentCore平台V2/应用版本3。

浏览器打开工作台、模型、Agent、性能、评测、账户页面，并执行一次能力优先选模。40/56与48/56、公开评测来源、Sol依据、质量校验及MiniMax证据不足原因均可见；390px手机布局无横向溢出，无JavaScript错误。[浏览器记录](../browser/summary.json) · [评测摘要](../browser/latest-validation.png) · [质量依据](../browser/quality-policy.png) · [候选证据](../browser/candidate-evidence.png)。截图仅保留被检验元素，避免发布个人历史列表。

## 三种GPU决策服务与产线回答

同一个复杂产线问题，前三条仅选模，第四条执行目标模型。首次选模均接收能力/权限/预算过滤后的全部106个配置。

| 请求 | 最终型号 | GPU调用 | 需求评估 | 全部选模调用 | 路由合计 |
| --- | --- | ---: | ---: | ---: | ---: |
| Liquid仅选模 | GPT‑6.2 Sol | 2 | 201ms | 986ms | 1776ms |
| H2O仅选模 | GPT‑6.1 Terra | 2 | 646ms | 3903ms | 4969ms |
| Decider仅选模 | GPT‑6.2 Astra | 3 | 272ms | 1545ms | 2000ms |
| Liquid流式执行 | GPT‑6.2 Sol默认档 | 2 | 132ms | 985ms | 1296ms |

以上每组只有一次部署诊断，含线上网络和建池，不能据此估计P95或比较模型准确率。决策调用正常为2；Decider首次选Haiku 4.5，缺少本次可比质量依据被拒，随后GPU在合格集合重选Astra，[真实纠错审计](final/quality-repair-check.json.gz)。没有CPU指定赢家，三条仅选模请求没有调用目标。

第四条目标生成 **10,550.887ms**，目标首字 **2157.970ms**，后端总计 **12,313.740ms**；66个文本增量、1327输出Token、正常结束。答案正确区分供应商/产线/温度混杂，按800件原批追溯隔离，复验保留质量工程师审批；0/50单侧95%上限约5.8%、1.5%与5%比较每组约400的粗估明确列出假设。[请求](final/manufacturing-stream-request.json.gz) · [逐阶段/输出](final/manufacturing-stream.json.gz) · [哈希绑定非盲复核](final/manufacturing-review.json.gz)。

## 连续会话、缓存与Agent

普通两轮对话先记录DELTA-273/52，再将预算改为57；分别严格返回`{"recorded":true}`和`{"project":"DELTA-273","budget":57}`，第二轮沿用Fable 5.1并使用真实历史。

缓存诊断显式限定已支持缓存的Sonnet 4.6，仅检查缓存功能，不用于证明全池选模结果。首轮写入 **3843Token**，次轮Bedrock报告读取 **3843Token**、另写24Token；均流式返回指定标记。[写入](final/cache-write.json.gz) · [读取](final/cache-read.json.gz) · [性能读取](final/cache-performance.json.gz)。缓存指标来自服务返回字段，不估算折扣或费用节省。

三个任务在**同一AgentCore V2会话**连续运行，模型入口为本项目`model-router`。共5次目标调用，每次都先完成两次GPU路由并保存选中型号，再开始目标生成。

| 轮次 | 工具/历史行为 | 目标型号 | 验证 |
| --- | --- | --- | --- |
| 查询账目并算原始净额 | 3次工具、3次模型调用 | Fable 5.2 | 严格JSON `raw_net_cents:5000` |
| 依据上文事件去重 | 0次工具、1次模型调用，沿用 | Fable 5.2 | 严格JSON `dedup_net_cents:-7500` |
| 编写去重净额函数 | 0次工具、1次模型调用，切换 | Fable 5.1 | 裸Python格式及84项隔离功能检查通过 |

函数检查覆盖空输入、相同事件相同金额去重、相同事件金额冲突抛ValueError、负金额、大整数和不修改输入；固定随机种子，无网络/凭据、非root、只读且受资源限制的容器。[代码检查](final/agent-code-check.json.gz) · [首轮运行](final/agent-first.json.gz) · [第二轮](final/agent-followup.json.gz) · [第三轮](final/agent-code.json.gz)。这证明该轨迹可沿用及切换，不证明每次切换最优或所有Agent任务可靠。

## 初次失败与修复记录

1. 候选revision27漏打包`quality_feedback.json`，导致目录500。立即回滚revision26，12:57:13 UTC核验目录恢复200；[回滚记录](../rollback-verified.json)。修复`.dockerignore`后，Docker构建中实际加载目录，检查128型号、27模板及反馈校验和，[最终镜像检查](../image-check.json)。以后相同缺件会在构建时失败。
2. revision28已修复打包，但未增加配置级证据校验。首轮线上产线选择Sol最高推理档，产生67个增量后达到180秒总时限；[原始失败](initial/manufacturing-stream.json.gz)保留。最终revision29加入精确配置证据约束，正常完成的Sol默认档不覆盖这个失败。
3. [初次Agent诊断](initial/summary.json)第二轮提示要求“同租户”去重，但演示记录没有租户字段，计算器也不支持那个过滤表达式；回答包含限制说明，未按裸JSON完成。最终诊断改成该数据具备的事件去重，并保留最初输出。此修改仅修正部署测试任务，不计为Agent质量提升。

初次浏览器验证保存在[历史浏览器记录](../browser-initial/summary.json)。最终全部原始记录在[final](final/)，初次记录在[initial](initial/)；认证票据等已脱敏，人工原始任务审计和私有素材未公开。
