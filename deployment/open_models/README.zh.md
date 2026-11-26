# 独立网关的常驻GPU服务

当前DeciderModelRouter使用独立`g5.xlarge`、A10G、实例`i-01180cf02d12e6e31`。服务名为`dmr-decider.service`、`dmr-h2o.service`、`dmr-liquid.service`和`dmr-gateway.service`。Liquid与H2O经私网18124网关访问，内部端口仍为18801/18803；Decider使用8100。此服务通过AWS SDK和SSM维护，不使用CloudFormation。

Liquid使用原生Transformers BF16，第二阶段可编译加速，第一阶段保持eager。`liquid_server.py`新增可选的`--selection-adapter <目录> --adapter-sha256 <完整摘要>`，须与`liquid_adapter.py`同目录部署。目录包含`adapter.json`和`adapter.safetensors`；启动核验摘要、基座版本、模块与张量形状。适配权重只在第二阶段的LiveBench文本选模请求启用；第一阶段、多模态及未启用适配的历史输入使用原始权重。健康接口和逐次响应均报告权重版本，前端显示本轮是否使用微调。

当前适配权重、训练数据与验证范围见[公开验证材料](../../validation/independent_gateway/anonymous-evidence-2026-11-26/README.zh.md)。证据表位于共享`state`中，网关允许最多64,000字符；每题最多256个候选。正常请求仍为两次GPU调用，不增加逐候选推理或质量重选。

部署采用先预热18804临时副本、验证后暂时切换私网转发、更新18801正式服务、再切回的顺序。保留原systemd单元和网关源码供回滚。训练与副本验证复用现有GPU，临时释放H2O显存，结束后恢复；不新建EC2。当前资源和发布结果以[部署记录](../DEPLOYED.md)为准。

```bash
systemctl status dmr-decider dmr-h2o dmr-liquid dmr-gateway
nvidia-smi
```

## 历史：原共用L4部署

以下记录描述旧项目的L4主机。`install_resident.sh`是该旧主机的专用安装器，不用于当前独立g5服务。

[English](README.md) · [业务流程与实测](../../docs/FIVE_MODELS.md)

复用 `us-east-1` 现有按需 **g6.xlarge `i-0ae7eb0abcb815af3`**，单张 NVIDIA L4（系统显示 23,034 MiB）、4 vCPU、16 GiB 内存。Decider、Liquid 和 H2O 同时驻留，前端按模型依次请求，单并发比较决策速度和实际任务完成情况。正常调用无需重新加载模型。

| 服务 | 运行环境 / 精度 | 端口 | 主要限额 |
| --- | --- | --- | --- |
| `decider-vllm.service` | vLLM 0.31.0，v19 BF16 | 8100，Bearer 鉴权 | 显存比例 0.26；引擎 / 问题并发 1；HTTP 准入 32；主存 6 GiB |
| `decider-h2o.service` | 官方补丁 vLLM 0.30.0，4B 在线 FP8 | shim 18803；引擎 18802 | 显存比例 0.34；上下文 4096；并发 1；eager；主存 8 GiB |
| `decider-liquid.service` | 官方 Transformers `system_one`，2B BF16 | 本机 18801 | 推理并发 1；主存 4 GiB |
| `decider-comparison-gateway.service` | CPU FastAPI | 私网 18124 | 每模型并发 1；主存 512 MiB |

Liquid/H2O 独立使用 `/bench/h2o-env`：Python 3.12、torch 2.13.0+cu130、Transformers 5.20.2。Decider 保留原环境。**H2O 的补丁不能安装进 Decider 的 vLLM 环境。** 官方补丁固定到 `b05cc29ab813895947954c268c4ec41ca0da1306`，用于其按请求设置的双向注意力；原版 vLLM 不能直接替代。权重版本及 SHA-256 见 [models.json](../../validation/open_decision_models/models.json)。本轮 H2O FP8 的性能与结果不沿用此前独占 GPU 的 BF16 数据。

主机目录 `/opt/decider-open-models` 挂载为 `/bench`，包含 `h2o-env/`、`models/{liquid,h2o}/`、`source/`、缓存和 `runtime-ready`。71 个模型文件已逐个验证 SHA-256；原 Decider systemd 配置备份在主机 `rollback/` 下，vLLM 项目的安装脚本同步记录了新限额。

## 维护

`install_resident.sh` 适用于当前已准备好运行环境的主机，不承担从空白主机下载模型的工作。准备固定版本的权重、H2O 补丁、独立 Python 环境及 `runtime-ready`，把本目录的服务源码放到 `/opt/decider-open-models/source/` 后执行：

```bash
sudo bash /opt/decider-open-models/source/install_resident.sh
```

脚本固定当前本地 Docker 镜像摘要，配置 systemd 自启及进程故障重启。初始化顺序为 **Decider 就绪 → H2O 就绪 → Liquid**，避免多个 vLLM 同时分析剩余显存。主机重启后的加载需要时间；加载完成后持续复用进程。

12 次 GPU 烟测全部成功；烟测后显存为 **19,147 / 23,034 MiB**，主存约 **4.9 GiB 可用**，无 swap。这是本次观测，不是峰值保证。[烟测原始记录](../../docs/results/resident-gpu-smoke-2026-10-29.json)采用本机往返计时，业务对比另用 ECS 后端耗时。

模型网关应用通过 SDK 直接部署，继承现有 ECS 的 GPU 地址和安全组配置：

```bash
uv run python -m deployment.deploy_gateway \
  --resources .deployment/outputs.json \
  --image YOUR_PUSHED_ECR_IMAGE --provider h2o
```

不使用 CloudFormation；镜像构建与发布步骤见[模型网关](../../docs/MODEL_GATEWAY.zh.md)。18124 只对 API 安全组开放，Decider 沿用受鉴权的私网 8100。前端仍是私有 S3 + CloudFront OAC，沿用现有用户名登录。

`GET /health` 核验两个服务及模型身份，需检查 JSON 的 `status` / `models`；HTTP 200 本身不代表就绪。`POST /v1/systemone/liquid` 和 `/v1/systemone/h2o` 接收 `state` / `questions`，最多 16 个问题、每题 32 个候选。模型地址固定，不接受任意上游 URL，不自动切换或替代模型。

```bash
systemctl status decider-vllm decider-h2o decider-liquid decider-comparison-gateway
curl -fsS http://127.0.0.1:18124/health
nvidia-smi
docker logs --tail 50 decider-h2o-resident
```

### 2026-11-24 启动修复

原实例从停止状态启动后，GPU 容器提示 `libnvidia-ml.so.1` 缺失。检查确认 NVIDIA 内核模块 **615.71.09** 已加载，但用户态计算库和工具未安装。在 Ubuntu 24.04 上先模拟安装，确认只新增两个匹配版本的包，再安装：

```bash
sudo apt-get --simulate install --no-install-recommends \
  libnvidia-compute-615-server=615.71.09-0ubuntu0.24.04.2 \
  nvidia-utils-615-server=615.71.09-0ubuntu0.24.04.2
sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l \
  apt-get install -y --no-install-recommends \
  libnvidia-compute-615-server=615.71.09-0ubuntu0.24.04.2 \
  nvidia-utils-615-server=615.71.09-0ubuntu0.24.04.2
sudo ldconfig
nvidia-smi
sudo systemctl start --no-block \
  decider-vllm.service decider-h2o.service decider-liquid.service
```

以上版本对应此次主机状态；以后应先读取 `/proc/driver/nvidia/version`，不能直接套用版本号。此次未升级内核、未重建实例或模型环境。`systemctl active` 不能代替模型健康检查，需等待顺序加载完成后检查 `18124/health` 中各模型字段。

`activate.py` 仅保留旧 GPU `i-0daca64f56056aa1c` 分时加载部署的历史实现；当前应用已迁离其对比网关。该实例的原版/SFT 和图片服务另行保留。历史独占 GPU 高并发报告不能当作当前同卡单并发配置的测试结果。
