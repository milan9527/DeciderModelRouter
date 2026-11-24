# Three resident decision models on one L4

[中文](README.zh.md) · Workflow behavior and evidence: [English](../../docs/FIVE_MODELS.en.md) / [中文](../../docs/FIVE_MODELS.md).

The existing On-Demand **g6.xlarge `i-0ae7eb0abcb815af3`** in `us-east-1` now hosts Decider, Liquid d1-2B and H2O-Lightning 4B simultaneously. It has one NVIDIA L4 (23,034 MiB reported memory), 4 vCPUs and 16 GiB RAM. Models stay loaded between calls. The comparison application sends one provider request at a time; no instance or model startup is part of normal measurement.

| Service | Runtime / precision | Port | Limits |
| --- | --- | --- | --- |
| `decider-vllm.service` | vLLM 0.31.0, Decider v19 BF16 | 8100, Bearer authentication | GPU fraction 0.26; sequences 1; active questions 1; pending HTTP 32; host RAM 6 GiB |
| `decider-h2o.service` | Patched vLLM 0.30.0, H2O 4B online FP8 | Shim 18803; engine 18802 | GPU fraction 0.34; context 4096; sequences 1; eager; host RAM 8 GiB |
| `decider-liquid.service` | Official Transformers `system_one`, d1-2B BF16 | Loopback 18801 | One active inference; host RAM 4 GiB |
| `decider-comparison-gateway.service` | CPU-only FastAPI | Private 18124 | One request per provider; host RAM 512 MiB |

Liquid and H2O use an isolated `/bench/h2o-env` (Python 3.12, torch 2.13.0+cu130, Transformers 5.20.2). Decider keeps its original environment. **Do not install H2O's overlay into Decider's vLLM environment.** The H2O overlay is pinned to `b05cc29ab813895947954c268c4ec41ca0da1306`; stock vLLM does not implement this checkpoint's per-request bidirectional attention behavior. Checkpoints and SHA-256 manifests are in [models.json](../../validation/open_decision_models/models.json). FP8 performance/quality must not be treated as the earlier exclusive-GPU BF16 results.

Host files reside at `/opt/decider-open-models`, mounted as `/bench`: `h2o-env/`, `models/{liquid,h2o}/`, `source/`, H2O compile cache and `runtime-ready`. All 71 checkpoint files were SHA-256 verified before starting services. The pre-change Decider systemd unit is retained under `rollback/` on the host. The Decider source deployment's [install.sh](https://github.com/milan9527/StrandsDeciderVLLM/blob/main/deployment/install.sh) also records its reduced limits.

## Install or restart on the prepared host

`install_resident.sh` is an instance-specific service installer, not a fresh-host model downloader. Prepare the pinned checkpoints, official H2O overlay, isolated environment and `runtime-ready` first. Copy this directory's gateway, Liquid server and readiness helper into `/opt/decider-open-models/source/`, then run:

```bash
sudo bash /opt/decider-open-models/source/install_resident.sh
```

The script pins the locally installed `decider-vllm:service` image digest and enables separate systemd units. Startup proceeds **Decider ready → H2O ready → Liquid**; `wait_ready.py` prevents overlapping vLLM memory profiling. Docker logs are bounded. Services restart on process failure. Startup can take minutes after a host restart; normal calls reuse resident processes.

The twelve initial GPU smoke calls succeeded. After those calls, combined GPU usage was **19,147 MiB / 23,034 MiB**; host memory had approximately **4.9 GiB available**, with no swap. These are observed values, not reservations or worst-case guarantees. See the [raw host smoke](../../docs/results/resident-gpu-smoke-2026-10-29.json); business measurements use ECS backend timing separately.

## Application connection

```bash
python deployment/deploy.py --api-only --tag YOUR_TAG \
  --open-models-url http://172.31.33.164:18124 \
  --open-models-security-group sg-051a9e10a77085738
```

CloudFormation configures API-to-GPU ingress on 18124 and `OPEN_MODELS_URL`. Port 18124 is private and only accepts the project's API security group. Decider continues using its existing private authenticated port 8100. S3 remains private behind CloudFront OAC; login is application-owned.

- `GET /health`: checks Liquid and H2O reachability and model identities; inspect `status` and `models`, not HTTP 200 alone.
- `POST /v1/systemone/liquid` or `/v1/systemone/h2o`: accepts typed `state` / `questions`, up to 16 questions and 32 candidates per question.
- Model URLs are fixed internally. Calls do not create model processes, reload weights, or fall back to another provider.

Diagnostics on the host:

```bash
systemctl status decider-vllm decider-h2o decider-liquid decider-comparison-gateway
curl -fsS http://127.0.0.1:18124/health
nvidia-smi
docker logs --tail 50 decider-h2o-resident
```

`activate.py` records the superseded switch-loading deployment on `i-0daca64f56056aa1c`. That host's original/SFT and vision services are separate. The current application no longer uses its old comparison gateway. Keep historical BF16 and concurrency reports separate from this single-concurrency, shared-GPU deployment.
