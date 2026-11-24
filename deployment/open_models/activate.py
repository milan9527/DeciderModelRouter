"""Historical switch-loading deployment, superseded by install_resident.sh.

This script targets the old experimental GPU; do not use for the current service.
Run with the existing private Docker-control bridge after staging and smoke tests.

No original text/SFT/vision container or shared vLLM instance is stopped.
The only replaced container is the temporary installer from this deployment.
"""

import json

SOURCE = "open-decision-benchmark-20261029"
TARGET = "decider-open-models-20261029"
IMAGE = "632930644527.dkr.ecr.us-east-1.amazonaws.com/strands-decider-demo-model:20261009-gpu1"
HOST_PATH = "/opt/decider-cache/open-model-benchmark-20261029"

# docker() and execute() are supplied by the private deployment bridge.
assert execute(SOURCE, ["test", "-f", "/bench/five-runtime-ready"]) == 0  # noqa: F821
assert execute(SOURCE, [  # noqa: F821
    "python", "-c",
    "import json; rows=json.load(open('/bench/five-smoke.json')); "
    "assert len(rows)==8 and all(r['result']['status']=='ok' for r in rows)",
]) == 0
docker("POST", "/containers/" + SOURCE + "/stop?t=15")  # noqa: F821
container = docker("POST", "/containers/create?name=" + TARGET, {  # noqa: F821
    "Image": IMAGE, "User": "0",
    "Entrypoint": ["/bench/h2o-env/bin/python", "/bench/five_gateway.py"],
    "Env": ["HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "PYTHONUNBUFFERED=1"],
    "Healthcheck": {
        "Test": ["CMD", "/bench/h2o-env/bin/python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18124/health',timeout=3)"],
        "Interval": 30_000_000_000, "Timeout": 5_000_000_000,
        "StartPeriod": 20_000_000_000, "Retries": 3,
    },
    "HostConfig": {
        "Binds": [HOST_PATH + ":/bench"], "NetworkMode": "host",
        "Memory": 8 * 1024**3,
        "RestartPolicy": {"Name": "unless-stopped"},
        "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
        "DeviceRequests": [{"Driver": "nvidia", "Count": 1, "Capabilities": [["gpu"]]}],
    },
})
docker("POST", "/containers/" + TARGET + "/start")  # noqa: F821
print(json.dumps({"container": TARGET, "id": container["Id"], "restart": "unless-stopped"}))
