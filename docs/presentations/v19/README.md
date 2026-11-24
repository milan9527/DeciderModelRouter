# v19 · 领域微调过程与实测结果

[PPTX](./Strands-Decider-v19-领域微调过程与实测结果-39页-20261020.pptx) · [PDF](./Strands-Decider-v19-领域微调过程与实测结果-39页-20261020.pdf) · [新增页预览](./updated-overview.png) · [讲稿与来源](./讲稿与来源.md)

基于最新 v18，新增 **5 页，共 39 页**。在场景验证与优化建议之后、vLLM 部署之前，加入已完成的俄罗斯方块领域微调实验；保留原有支付、路由、三模型性能、JevBench 和成本对比。

| 页码 | 内容 |
| --- | --- |
| P24 | 从引擎标签、数据分区到训练、独立测试与完整对局 |
| P25 | 真实训练题：候选指标、严格支配标签和换序增强 |
| P26 | 冻结基座，续训 LoRA 与 pointer head；L4 完整训练约 64 分钟 |
| P27 | 原版 / 微调版独立测试：准确率 60.35% → 99.95%，换序落点一致率 35.44% → 99.85% |
| P28 | 完整对局：微调版 10/10 达到 50 块，数字 / 自然语言平均消行 12.2 / 14.2 |

封面、优化章节衔接、选型总结和资料页同步补充微调内容。**P39 保留原有 internal only 标记**；本次未推送 GitHub。

两张原流程图按投影阅读需要横向重排为可编辑 PPT 图形，原 PNG / SVG 完整保留于 [图表来源](./sources/docs/diagrams/)。新增页不恢复此前删除的页底说明；详细边界和来源放在讲稿与备注。

来源：[微调全过程](https://github.com/milan9527/StrandsDeciderTetrisFinetuning/blob/main/docs/TETRIS_FINETUNING_WALKTHROUGH.zh.md) · [实验结果](https://github.com/milan9527/StrandsDeciderTetrisFinetuning/blob/main/finetuning/RESULTS.md) · [示例仓库](https://github.com/milan9527/StrandsDeciderTetrisFinetuning)。

本版引用 2026-10-16 已完成的 GPU 实验，没有重新训练或调用线上模型。2,068 题准确率只覆盖引擎支配比较题，完整对局为五个新种子和 50 块上限；微调版仍有 34/500 次指标劣选，不能外推为支付、路由或通用模型的新成绩。

构建：`scripts/build_presentation_v19.py`。检查：`scripts/verify_presentation_v19.py`。复核清单：[构建来源](./build-manifest.json) · [渲染与数据核验](./render-audit.json)。
