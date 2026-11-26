# Liquid公开评测选模适配器

本目录的 `adapter.safetensors` 是项目于2026-11-26新增的LoRA衍生权重，非LiquidAI原始发布，不包含基座权重。作者：DeciderModelRouter项目贡献者。基座：LiquidAI/d1-2B，固定提交 `cfe5760ff5d5998f91d9ac56bd4b2dbd96996d41`。

基座及本衍生适配权重适用 [LFM Open License v1.0](BASE_MODEL_LICENSE.txt)，不是网关代码的Apache许可。许可包含商业规模条件，使用、修改、分发应遵循该文件。此README作为新增衍生权重的修改说明。

## 安装和校验

权重SHA-256：`d9eeae6c4cda5d29b91f3f355d658377714cf5e6080ff6b83fae4d8dabfa048e`。将 `adapter.json` 和 `adapter.safetensors` 放入同一目录；按[GPU服务说明](../../../../deployment/open_models/README.zh.md)在Liquid命令增加 `--selection-adapter <目录> --adapter-sha256 <上述摘要>`。启动校验摘要、基座声明、模块/张量名称、形状、类型及数值有限性，失败拒绝启动。第一阶段与多模态不启用适配器。

`adapter.json`列出11个实际o_proj模块，rank8、scale2；训练代码虽然匹配q_proj/v_proj/o_proj名称，此基座实际仅匹配这11个o_proj，不能描述成三种投影均已训练。基座BF16冻结，训练A/B为float32。环境见上级 `gpu-environment.json`：Python3.12.3、PyTorch2.13.0+cu130、Transformers5.20.2。

## 复现训练

在上述基座和依赖环境中，固定基座目录为`/bench/models/liquid`；将本目录训练材料复制到`/training`，解压 `training-data.json.gz` 到 `/training/training-data.json`，执行 `python /training/train.py`。脚本固定随机种子、学习率、320步和32条开发验证，保存验证交叉熵最小的适配器。实际选中step240，而非最后step320。异构GPU或软件版本可能有数值差异；线上加载发布权重时必须核对摘要。

160/32条为7个已见路由上下文的候选排列及合成价格变体，不是192个独立业务领域样本。26/32是对训练启发式首选标签的符合数。它不是实际答案准确率；业务结果、剩余顺序偏差和编译差异见[完整报告](../README.zh.md)。
