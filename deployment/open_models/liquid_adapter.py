"""Optional, pinned public-evidence adapter; the first assessment stays on base weights."""

import hashlib
import json
from pathlib import Path

BASE_REVISION = "cfe5760ff5d5998f91d9ac56bd4b2dbd96996d41"


def selection_input(payload):
    if set(payload.get("questions", {})) != {"action"}:
        return False
    try:
        state = json.loads(payload["state"])
    except (ValueError, KeyError, TypeError):
        return False
    return (
        isinstance(state, dict)
        and isinstance(state.get("candidates"), dict)
        and bool(state["candidates"])
        and isinstance(state.get("primary_benchmark"), dict)
        and state["primary_benchmark"].get("source") == "livebench"
    )


class SelectionAdapter:
    def __init__(self, model, folder, expected_sha256):
        import torch
        from safetensors.torch import load_file
        from torch import nn

        folder = Path(folder)
        weights_path = folder / "adapter.safetensors"
        digest = hashlib.sha256(weights_path.read_bytes()).hexdigest()
        if len(expected_sha256) != 64 or digest != expected_sha256:
            raise ValueError("Unexpected Liquid adapter digest")
        metadata = json.loads((folder / "adapter.json").read_text())
        if metadata["base_revision"] != BASE_REVISION or metadata["selection_only"] is not True:
            raise ValueError("Unexpected Liquid adapter base or scope")
        if metadata["rank"] != 8 or metadata["scale"] != 2:
            raise ValueError("Unexpected Liquid adapter shape")
        names = metadata["modules"]
        if not names or len(names) != len(set(names)):
            raise ValueError("Invalid adapter module list")
        weights = load_file(weights_path, device="cuda")
        if set(weights) != {name + "." + key for name in names for key in ("A", "B")}:
            raise ValueError("Unexpected adapter tensors")

        class LowRank(nn.Module):
            def __init__(self, base, name):
                super().__init__()
                self.base = base
                self.enabled = False
                a, b = weights[name + ".A"], weights[name + ".B"]
                if a.shape != (8, base.in_features) or b.shape != (base.out_features, 8):
                    raise ValueError("Adapter does not match the projection")
                if a.dtype != torch.float32 or b.dtype != torch.float32:
                    raise ValueError("Unexpected adapter precision")
                if not bool(torch.isfinite(a).all() and torch.isfinite(b).all()):
                    raise ValueError("Nonfinite adapter tensor")
                self.register_buffer("A", a)
                self.register_buffer("B", b)

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
                    output = output + (update * 2.0).to(output.dtype)
                return output

        self.layers = []
        for name in names:
            module = model.get_submodule(name)
            if not isinstance(module, nn.Linear) or "vision" in name or name.rsplit(".", 1)[-1] not in {
                "q_proj", "v_proj", "o_proj"
            }:
                raise ValueError("Adapter can only wrap text attention projections")
            parent, attr = name.rsplit(".", 1)
            layer = LowRank(module, name)
            setattr(model.get_submodule(parent), attr, layer)
            self.layers.append(layer)
        self.metadata = {"version": "public-evidence-lora-v1", "sha256": digest,
                         "base_revision": BASE_REVISION, "active": False,
                         "scope": "text_selection_only"}

    def select(self, payload):
        active = selection_input(payload)
        for layer in self.layers:
            layer.enabled = active
        self.metadata = {**self.metadata, "active": active}
        return active
