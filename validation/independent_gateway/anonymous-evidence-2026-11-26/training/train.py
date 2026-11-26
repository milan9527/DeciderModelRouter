"""Bounded adapter experiment; frozen weights and no production model mutation."""
import importlib
import json
import math
import random
import time
from pathlib import Path

import torch
from safetensors.torch import save_file
from torch import nn
from transformers import AutoModel

ROOT = Path("/training")
torch.manual_seed(64119)
torch.set_num_threads(2)
started = time.monotonic()
model = AutoModel.from_pretrained(
    "/bench/models/liquid", trust_remote_code=True, local_files_only=True, dtype=torch.bfloat16
).to("cuda").eval()
engine = model.engine
native_prompt = importlib.import_module(engine.__class__.__module__.rsplit(".", 1)[0] + ".prompt")
data = json.loads((ROOT / "training-data.json").read_text())
for param in model.parameters():
    param.requires_grad_(False)


class LowRank(nn.Module):
    def __init__(self, base, rank=8):
        super().__init__()
        self.base = base
        self.enabled = True
        self.A = nn.Parameter(torch.empty(rank, base.in_features, device="cuda", dtype=torch.float32))
        self.B = nn.Parameter(torch.zeros(base.out_features, rank, device="cuda", dtype=torch.float32))
        nn.init.kaiming_uniform_(self.A, a=math.sqrt(5))
        self.scale = 2.0

    @property
    def weight(self):
        return self.base.weight

    @property
    def bias(self):
        return self.base.bias

    def forward(self, value):
        output = self.base(value)
        if self.enabled:
            update = torch.nn.functional.linear(
                torch.nn.functional.linear(value.float(), self.A), self.B)
            output = output + (update * self.scale).to(output.dtype)
        return output


adapters = {}
for name, module in list(model.named_modules()):
    if isinstance(module, nn.Linear) and "vision" not in name and name.rsplit(".", 1)[-1] in {
        "q_proj", "v_proj", "o_proj"
    }:
        parent, attr = name.rsplit(".", 1)
        wrapped = LowRank(module)
        setattr(model.get_submodule(parent), attr, wrapped)
        adapters[name] = wrapped
assert adapters
print(json.dumps({"event": "loaded", "modules": list(adapters),
                  "parameters": sum(p.numel() for p in model.parameters() if p.requires_grad)}), flush=True)
model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})


def scores(example):
    payload = example["payload"]
    q = engine.ask(payload["questions"]["action"])
    sequences = engine._sequences([(payload["state"], [q], [])])
    assert len(sequences) == 1
    length = len(sequences[0][2])
    logits, _ = engine._slot_logits(sequences, [0], [(0, 0, q, length - 1)], None)
    groups = native_prompt.readout_ids(engine.tokenizer, q)
    values = torch.stack([logits[0, ids].max() for ids in groups])
    temperature = engine.calibration.temperature(q) if engine.calibration is not None else 1
    return values / temperature, list(q.criteria)


def validate():
    model.eval()
    correct, losses = 0, []
    with torch.no_grad():
        for example in data["validation"]:
            values, keys = scores(example)
            target = torch.tensor([example["target_distribution"].get(k, 0) for k in keys], device="cuda")
            losses.append(float(-(target * values.log_softmax(-1)).sum()))
            selected = keys[int(values.argmax())]
            best = max(example["target_distribution"].values())
            correct += example["target_distribution"].get(selected, 0) >= best - 1e-6
    return {"correct": correct, "total": len(data["validation"]),
            "loss": sum(losses) / len(losses)}


history = [{"step": 0, **validate()}]
best = history[0]["loss"]
print(json.dumps({"event": "validation", **history[-1]}), flush=True)
optimizer = torch.optim.AdamW(
    [p for p in model.parameters() if p.requires_grad], lr=0.0002, weight_decay=0.01, foreach=False)
rng = random.Random(64119)
step = 0
for epoch in range(2):
    examples = list(data["train"])
    rng.shuffle(examples)
    for example in examples:
        if time.monotonic() - started > 1000:
            break
        model.train()
        optimizer.zero_grad(set_to_none=True)
        values, keys = scores(example)
        target = torch.tensor([example["target_distribution"].get(k, 0) for k in keys], device="cuda")
        loss = -(target * values.log_softmax(-1)).sum()
        if not torch.isfinite(loss):
            raise RuntimeError("Nonfinite adapter loss")
        loss.backward()
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
        optimizer.step()
        step += 1
        if step % 10 == 0:
            print(json.dumps({"event": "train", "step": step, "loss": float(loss.detach()),
                              "seconds": time.monotonic()-started,
                              "gpu_peak_bytes": torch.cuda.max_memory_allocated()}), flush=True)
        if step % 40 == 0:
            record = {"step": step, **validate()}
            history.append(record)
            print(json.dumps({"event": "validation", **record}), flush=True)
            if record["loss"] < best:
                best = record["loss"]
                save_file({name + "." + key: getattr(layer, key).detach().cpu().contiguous()
                           for name, layer in adapters.items() for key in ("A", "B")},
                          ROOT / "adapter.safetensors")
                (ROOT / "adapter.json").write_text(json.dumps({
                    "base_revision": "cfe5760ff5d5998f91d9ac56bd4b2dbd96996d41",
                    "rank": 8, "scale": 2, "modules": list(adapters), "best_step": step,
                    "selection_only": True, "training": "counterfactual-cost-public-score-distillation",
                }))
            (ROOT / "training-result.json").write_text(json.dumps({
                "history": history, "steps": step, "elapsed_seconds": time.monotonic()-started,
                "best_loss": best, "scope": "development validation, not business answer accuracy"}))
    if time.monotonic() - started > 1000:
        break
print(json.dumps({"event": "complete", "steps": step, "history": history}), flush=True)
