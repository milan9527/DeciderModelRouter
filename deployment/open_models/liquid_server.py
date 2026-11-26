"""Pinned Liquid implementation; one CUDA call at a time, no CPU fallback."""

import argparse
import json
import os
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=18801)
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--warmup-inputs", type=Path)
    parser.add_argument("--compile-cache", type=Path)
    args = parser.parse_args()
    if args.compile and not args.warmup_inputs:
        parser.error("--compile requires representative --warmup-inputs before readiness")
    if args.compile:
        # Set these before importing Torch, including for its compiler workers.
        # A late parent-process config override did not reproduce the isolated
        # precision-preserving probe in the resident HTTP service.
        os.environ["TORCHINDUCTOR_EMULATE_PRECISION_CASTS"] = "1"
        os.environ["TORCHINDUCTOR_COMPILE_THREADS"] = "2"
        if args.compile_cache:
            os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(args.compile_cache)
    import torch
    from transformers import AutoModel

    torch.set_num_threads(2)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA required")
    model = AutoModel.from_pretrained(
        args.model, trust_remote_code=True, dtype=torch.bfloat16, local_files_only=True,
    ).to("cuda").eval()
    startup = time.perf_counter()
    engine = "eager_bf16"
    if args.compile:
        from torch._inductor import config

        # Fusion normally removes intermediate BF16 rounding. Preserve it so
        # close routing scores do not change merely because kernels are fused.
        if not config.emulate_precision_casts:
            raise RuntimeError("The pinned compiler did not enable precision preservation")
        engine = "compiled_selection_eager_assessment_bf16"
    warmup = {"action": {"type": "choice", "instructions": "Which mode is requested?",
                        "criteria": {"cool": "Cooling", "heat": "Heating"}}}
    for _ in range(3):
        model.system_one("The user requests cooling.", warmup)
        torch.cuda.synchronize()
    switched = []
    if args.compile:
        eager = {name: part.forward for name, part in model.named_modules()}
        model.compile()
        switched = [(part, eager[name], part.forward) for name, part in model.named_modules()
                    if part.forward != eager[name]]
        if not switched:
            raise RuntimeError("The pinned Liquid compiler did not wrap any model forwards")

    def select_engine(payload):
        # The initial seven-attribute assessment retains eager BF16 numerics.
        # The measured optimization applies only to the subsequent model choice.
        compiled = args.compile and set(payload["questions"]) == {"action"}
        for part, eager_forward, compiled_forward in switched:
            part.forward = compiled_forward if compiled else eager_forward
        return compiled

    warmup_count = 3
    if args.warmup_inputs:
        for payload in json.loads(args.warmup_inputs.read_text()).values():
            select_engine(payload)
            model.system_one(payload["state"], payload["questions"])
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            warmup_count += 1
    startup_ms = round((time.perf_counter() - startup) * 1000, 3)
    torch.cuda.empty_cache()
    app = FastAPI()
    lock = threading.Lock()
    forward_count = 0
    original_slots = model.engine._slot_logits

    def counted_slots(*values, **kwargs):
        nonlocal forward_count
        forward_count += 1
        return original_slots(*values, **kwargs)

    model.engine._slot_logits = counted_slots

    @app.get("/health")
    def health():
        if args.compile and not config.emulate_precision_casts:
            raise HTTPException(503, "Liquid 工作线程的精度配置不一致")
        return {"status": "ok", "model": "LiquidAI/d1-2B", "device": "cuda", "dtype": "BF16",
                "decision_mode": "native_multi_question", "max_candidates": 256,
                "inference_engine": engine, "warmup_count": warmup_count,
                "warmup_ms": startup_ms, "new_shapes": "eager_cuda" if args.compile else "eager",
                "worker_precision_preserved": config.emulate_precision_casts if args.compile else None}

    @app.post("/v1/systemone")
    def decide(payload: dict):
        with lock, torch.inference_mode():
            started = time.perf_counter()
            before = forward_count
            torch.cuda.reset_peak_memory_stats()
            try:
                compiled = select_engine(payload)
                if compiled:
                    if not config.emulate_precision_casts:
                        raise RuntimeError("Liquid worker precision configuration mismatch")
                    # An unseen shape must not compile for a minute in a live
                    # request. It still executes on the same CUDA model.
                    with torch.compiler.set_stance("eager_on_recompile"):
                        result = model.system_one(payload["state"], payload["questions"])
                else:
                    result = model.system_one(payload["state"], payload["questions"])
                torch.cuda.synchronize()
                result = dict(result.model_dump() if hasattr(result, "model_dump") else result)
                peak = torch.cuda.max_memory_allocated()
            except (ValueError, KeyError, RuntimeError) as exc:
                raise HTTPException(422, "Liquid 无法处理这次请求") from exc
            finally:
                # Long evidence prompts create large temporary buffers. Release unused
                # allocator reservations so the other resident GPU services keep their budget.
                torch.cuda.empty_cache()
            result.update(model="LiquidAI/d1-2B", latency_ms=(time.perf_counter() - started) * 1000,
                          forward_passes=forward_count - before, process_peak_allocated_bytes=peak,
                          process_reserved_after_bytes=torch.cuda.memory_reserved(),
                          inference_engine="compiled_bf16_preserve_casts" if compiled else "eager_bf16")
            return result

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
