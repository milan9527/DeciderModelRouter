import asyncio
import importlib.util
import sys
from pathlib import Path

import app.gateway_joint_decision as current
from scripts import run_latency_quality as runner

root = Path(__file__).parent
spec = importlib.util.spec_from_file_location("original_selection", root / "baseline_joint.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
current.joint_payload = baseline.joint_payload
runner.MODEL_ROUTING_VERSION = "gpu-evidence-livebench-preference-v5"
asyncio.run(runner.main())
