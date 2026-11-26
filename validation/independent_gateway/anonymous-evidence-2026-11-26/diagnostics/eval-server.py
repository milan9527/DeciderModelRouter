"""Private, temporary adapter evaluation. Never exposed by the production gateway."""
import hashlib
import json
import threading
import time
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI
from safetensors.torch import load_file
from torch import nn
from transformers import AutoModel

torch.set_num_threads(2)
root = Path("/training")
metadata = json.loads((root / "adapter.json").read_text())
weights = load_file(root / "adapter.safetensors", device="cuda")
digest = hashlib.sha256((root / "adapter.safetensors").read_bytes()).hexdigest()
model = AutoModel.from_pretrained(
    "/bench/models/liquid", trust_remote_code=True, local_files_only=True, dtype=torch.bfloat16
).to("cuda").eval()
engine = model.engine


class LowRank(nn.Module):
    def __init__(self, base, name):
        super().__init__()
        self.base = base
        self.enabled = False
        self.register_buffer("A", weights[name + ".A"])
        self.register_buffer("B", weights[name + ".B"])

    @property
    def weight(self):
        return self.base.weight

    @property
    def bias(self):
        return self.base.bias

    def forward(self, value):
        output = self.base(value)
        if self.enabled:
            output = output + (
                torch.nn.functional.linear(torch.nn.functional.linear(value.float(), self.A), self.B)
                * metadata["scale"]
            ).to(output.dtype)
        return output


adapters = []
for name in metadata["modules"]:
    parent, attr = name.rsplit(".", 1)
    layer = LowRank(model.get_submodule(name), name)
    setattr(model.get_submodule(parent), attr, layer)
    adapters.append(layer)
app = FastAPI()
lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok", "adapter_sha256": digest, "experimental": True}


@app.post("/v1/systemone")
def decide(payload: dict):
    with lock, torch.inference_mode():
        state = json.loads(payload["state"]) if payload["state"].startswith("{") else {}
        enabled = (set(payload["questions"]) == {"action"} and
                   bool(state.get("primary_benchmark")) and payload.get("adapter", True))
        for layer in adapters:
            layer.enabled = enabled
        start = time.perf_counter()
        result = model.system_one(payload["state"], payload["questions"])
        torch.cuda.synchronize()
        result = dict(result.model_dump() if hasattr(result, "model_dump") else result)
        result.update(model="LiquidAI/d1-2B", adapter_enabled=enabled, adapter_sha256=digest,
                      latency_ms=(time.perf_counter() - start) * 1000)
        torch.cuda.empty_cache()
        return result


uvicorn.run(app, host="127.0.0.1", port=18804, access_log=False)
