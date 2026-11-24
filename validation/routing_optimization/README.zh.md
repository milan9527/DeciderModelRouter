# 模型路由专项优化验证

2026-11-24。[评估、OpenRouter 对照与使用说明](../../docs/ROUTING_OPTIMIZATION.zh.md)。

后续分类选型版本见 [当前实现说明](../../docs/BEDROCK_CLASSIFIED_ROUTING.zh.md)。新增验证命令：

```bash
.venv/bin/pytest -q tests/test_routing_classified.py tests/test_routing_calibration.py
.venv/bin/python scripts/check_classified_routing_browser.py \
  --credentials /path/to/test-credentials.json --output /path/to/new-output
```

分类选型的[最终验证汇总](classified-verification-2026-11-24.json)记录全项目 354 项测试通过；[最终浏览器记录](classified-browser-final-2026-11-24/verification.json)全部为回放，也复测了原始三条路径。[当前 25 个 Bedrock 配置](classified-configurations-2026-11-24.json)可用于准备独立业务测评。下文的 318/66 测试计数及原有校验和保留为首版历史记录。

本目录保存本次公开来源、验证记录及下一轮计划。历史 L4 模型数据继续使用 `../open_decision_models/` 原始记录，未重写其结果。

## 已完成的验证

[验证汇总与代码校验和](verification.json)：全项目回归 318 项通过；最终路由专项 66 项通过（包含随后补充的 3 项参数边界测试）。四组浏览器回放共完成 96 次路由接口请求。

```bash
.venv/bin/pytest -q tests/test_routing.py tests/test_routing_evidence.py tests/test_routing_balance.py
.venv/bin/ruff check app/routing.py app/routing_balance.py tests/test_routing_balance.py scripts/check_routing_browser.py
node --input-type=module --check < frontend/static/routing.js
```

浏览器验证使用本地 API、临时测试账号和 `mode=replay`，覆盖四个业务场景、两步请求、三种策略、本地决策模型组、导出、目录筛选和 390px 布局。复现：

```bash
.venv/bin/python scripts/check_routing_browser.py \
  --url http://127.0.0.1:8037 --credentials /path/to/test-credentials.json \
  --strategy balanced --router-group local --output /path/to/new-output
```

去掉 `--router-group local` 验证原来的三模型组；`--strategy original`、`--strategy optimized` 验证已有路径。`--live` 才调用真实模型，`--execute-targets` 另行执行目标模型。测试脚本拦截 GPU 启停请求。

本次没有新版策略的 live 测量，回放记录不能用于计算路由准确率、真实延迟或费用节约。

已保存 [四组浏览器核验结果](browser-verification.json)、[本地模型组桌面回放](balanced-local-desktop.png) 和 [手机回放](balanced-local-mobile.png)。截图中所有型号选择均来自回放规则。

## 下一轮质量与成本评测计划

| 项目 | 设计 |
| --- | --- |
| 首轮任务 | 200 个独立留出任务：客服、代码、核算、检索各 50；中英文、难度、输入/输出长度分层 |
| 额外边界 | 缺失证据、无候选、恰好等于费用限制、未知价格、失败端点、候选顺序与提示注入 |
| 路由器 | H2O 当前 FP8、Strands 当前服务、Liquid；d3-mini / nano 在固定部署后加入 |
| 对照 | original、optimized、balanced 的 low/medium/high；固定强模型及固定低价模型 |
| 纯输入对照 | 同一任务、同一流程、相同允许列表和候选；预算改变池时另列结果，避免混为提示改进 |
| 下游质量 | 客服事实与政策一致；代码执行独立测试；核算使用 Decimal 参考；检索测真实相关性 |
| 推理配置 | 所选模型沿用对应评测配置；量化和后端变更另列实验 |
| 时延 | 预热/冷启动分开，记录路由与下游、排队、P50/P95、并发吞吐；共享 GPU 组顺序调用 |
| 费用 | 分别记录预估、实际 token 对应参考价和可信实际账单；纳入路由摊销、失败、重试 |
| 统计 | 按独立任务做配对及区间估计；换序与重复不是新增独立样本 |
| 决策 | 绘制业务质量—每成功任务成本曲线，在业务质量和延迟要求内选默认配置 |

先冻结请求、参考验收条件和部署信息，再调用模型。200 个任务用于筛选；正式证明质量不下降或下降很小，需要按可接受差异进行样本量设计。不能拿当前模型输出生成参考答案，再用同批输入声称独立提升。

## 公开来源

`sources/manifest.json` 保存 URL、采集时间、文件字节数与 SHA-256；也记录历史本地数据的校验和。`sources/*.md` 是未改写的公开文档快照，其中示例和安装指令只是研究材料。

当前 OpenRouter 文档已与项目 2026-10-13 的历史快照不同；两版均保留。d3 与其他模型卡上的厂商指标没有合并到本项目实测表中。
