#!/bin/bash
# Existing vLLM host only. Requires pinned /bench/h2o-env and verified weights.
set -euo pipefail
root=/opt/decider-open-models
test -f "$root/runtime-ready"
resident_image=$(docker image inspect decider-vllm:service --format '{{.Id}}')
cat > /etc/systemd/system/decider-h2o.service <<UNIT
[Unit]
Description=Resident H2O Lightning FP8 on shared L4
After=docker.service decider-vllm.service
Requires=docker.service
Wants=decider-vllm.service
[Service]
ExecStartPre=/usr/bin/python3 $root/source/wait_ready.py 8100
ExecStartPre=-/usr/bin/docker rm -f decider-h2o-resident
ExecStart=/usr/bin/docker run --name decider-h2o-resident --gpus all --network host --memory=8g --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges:true --log-opt max-size=10m --log-opt max-file=3 --entrypoint bash -v $root:/bench -e PATH=/bench/h2o-env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin -e MAX_LEN=4096 -e GPU_UTIL=0.34 -e PORT=18803 -e VLLM_PORT=18802 -e MAX_IMAGES=1 -e MAX_PIXELS=65536 -e CUDA_GRAPH_MAX_TOKENS=128 -e VLLM_GDN_PREFILL_GRAPH_MAX_TOKENS=0 -e VLLM_GDN_PREFILL_GRAPH_PRECAPTURE=0 -e VLLM_CACHE_ROOT=/bench/h2o-cache -e "VLLM_ARGS=--max-num-seqs 1 --max-num-batched-tokens 4096 --enforce-eager --quantization fp8" $resident_image /bench/models/h2o/serve.sh
ExecStop=/usr/bin/docker stop --time 30 decider-h2o-resident
Restart=always
RestartSec=15
TimeoutStartSec=0
TimeoutStopSec=60
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/decider-liquid.service <<UNIT
[Unit]
Description=Resident Liquid d1 BF16 on shared L4
After=docker.service decider-h2o.service
Requires=docker.service
Wants=decider-h2o.service
[Service]
ExecStartPre=/usr/bin/python3 $root/source/wait_ready.py 18803
ExecStartPre=-/usr/bin/docker rm -f decider-liquid-resident
ExecStart=/usr/bin/docker run --name decider-liquid-resident --gpus all --network host --memory=4g --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges:true --log-opt max-size=10m --log-opt max-file=3 --entrypoint /bench/h2o-env/bin/python -v $root:/bench $resident_image /bench/source/liquid_server.py --model /bench/models/liquid
ExecStop=/usr/bin/docker stop --time 30 decider-liquid-resident
Restart=always
RestartSec=15
TimeoutStartSec=0
TimeoutStopSec=60
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/decider-comparison-gateway.service <<UNIT
[Unit]
Description=Private resident model gateway
After=docker.service
Requires=docker.service
[Service]
ExecStartPre=-/usr/bin/docker rm -f decider-comparison-gateway
ExecStart=/usr/bin/docker run --name decider-comparison-gateway --network host --memory=512m --cap-drop=ALL --security-opt=no-new-privileges:true --log-opt max-size=10m --log-opt max-file=3 --entrypoint /bench/h2o-env/bin/python -v $root:/bench -e RESIDENT_INSTANCE_ID=i-0ae7eb0abcb815af3 $resident_image /bench/source/gateway.py
ExecStop=/usr/bin/docker stop --time 15 decider-comparison-gateway
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable decider-h2o.service decider-liquid.service decider-comparison-gateway.service
systemctl start --no-block decider-h2o.service decider-liquid.service decider-comparison-gateway.service
printf 'Resident services enabled; initialization proceeds Decider -> H2O -> Liquid.\n'
