# v17 · JevBench 公开集实测与榜单口径

[PPTX](./Strands-Decider-v17-JevBench公开集实测与榜单口径-34页-20261016.pptx) · [PDF](./Strands-Decider-v17-JevBench公开集实测与榜单口径-34页-20261016.pdf) · [新增页预览](./updated-overview.png) · [讲稿与来源](./讲稿与来源.md)

基于 v16，新增两页，共 **34 页**：

- **P7：Decider JevBench 实测。** 原版 v19、g6.xlarge / L4，231 道旧版公开题答对 168 道（72.73%）。私网客户端 P50 / P95 为 68.3 / 403.7 ms，调用失败为零；展示三档难度和工具选择、意图识别、长规则、时序数值的表现。
- **P8：榜单分数的计算边界。** 本次按自托管规则估算速度分 85.62；按 EC2 按需价、平均请求耗时及持续有请求假设估算成本分 56.52（$0.02815/千次）。当前完整发布集为 300 公开 + 1,200 封闭题，本次缺少完整评测，正式能力分、综合分及排名暂无。

原有 32 页的正文、图片和讲稿保持不变，页码顺延；末页选项顺序诊断的 **internal only** 标记保留在 P34。此前支付优化实验继续保留。前端模型路由已经简化为原始流程，历史实验记录没有删除。

新增页取自已保存的真实测试，不重跑模型、不混入 vLLM 数据。72.73% 是公开题正确率，不能直接与 Jev 的榜单综合分 71.49 比较。

## 来源与复核

- [JevBench 实测报告](../../../validation/jevbench/README.md)
- [榜单公式与分项估算](../../../validation/jevbench/leaderboard-score.md)
- [实测汇总](./sources/summary.json) · [分层与部署信息](./sources/analysis.json) · [评分结果](./sources/leaderboard-score.json)
- [构建清单](./build-manifest.json) · [渲染核对](./render-audit.json)

构建：`scripts/build_presentation_v17.py`。核对：`scripts/verify_presentation_v17.py`，检查数值来源、原页保留、渲染文字、页面边界及 internal only 页。
