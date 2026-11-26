# 基线重放口径

基线使用同一评测后端，替换为提交 da2bfdb 的 v5 `joint_payload` 输入构造器，并调用未启用选模适配器的原生产Liquid。不是原revision30整镜像重放。

共享后端仍保存了新的 `selection.evidence_presentation` 和部分 v6 元数据；这些通用字段不能用来判断基线是否匿名。每条记录 `route.gpu_calls[1].payload` 是实际发给GPU的v5输入，保留名称及旧证据格式。未改写原始记录；以该请求原文、输入哈希和 `baseline_joint.py` 为准。三组68个独立任务第一阶段输入及答案全部一致。

基线延迟测量与训练有重叠，不用于因果加速对比。
