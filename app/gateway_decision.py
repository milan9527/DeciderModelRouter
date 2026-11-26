"""HTTP boundary to independently deployed GPU classifiers; no GPU lifecycle operations."""

import asyncio
import math
import os
from time import perf_counter

import httpx
from fastapi import HTTPException

from app.routing import LOCAL_ROUTERS
from app.routing_profile import parse_profile


class GatewayDecisionService:
    def __init__(self, client):
        self.client = client
        local = os.getenv("OPEN_MODELS_URL", "").rstrip("/")
        decider = os.getenv(
            "GATEWAY_DECIDER_URL", os.getenv("STRANDS_DECIDER_URL", "http://127.0.0.1:8099")
        ).rstrip("/")
        self.services = {
            "decider": {
                "base": decider,
                "path": "/v1/systemone",
                "model": os.getenv("GATEWAY_DECIDER_MODEL")
                or (
                    "strands-decider-2B-hobson-v19-vllm"
                    if os.getenv("GPU_PROVIDER") == "vllm"
                    else os.getenv("DECIDER_MODEL", "StrandsAgents/strands-decider-2B-hobson-v19")
                ),
                "key": os.getenv("DECIDER_API_KEY", ""),
            },
            **{
                provider: {
                    "base": local,
                    "path": f"/v1/systemone/{provider}",
                    "model": model,
                    "key": os.getenv("OPEN_MODELS_API_KEY", ""),
                }
                for provider, model in LOCAL_ROUTERS.items()
            },
        }
        self.default_provider = os.getenv("GATEWAY_DECISION_PROVIDER", "liquid")
        if self.default_provider not in self.services:
            raise RuntimeError("GATEWAY_DECISION_PROVIDER must be decider, liquid or h2o")
        self.routing_mode = os.getenv("GATEWAY_DECISION_MODE", "two_stage")
        if self.routing_mode not in {"two_stage", "joint"}:
            raise RuntimeError("GATEWAY_DECISION_MODE must be two_stage or joint")
        self.slots = {provider: asyncio.Semaphore(2) for provider in self.services}

    def public_config(self):
        return {
            "default_provider": self.default_provider,
            "routing_mode": self.routing_mode,
            "providers": [
                {
                    "id": provider,
                    "model": config["model"],
                    "configured": config["base"].startswith(("http://", "https://")),
                }
                for provider, config in self.services.items()
            ],
        }

    def service(self, provider):
        config = self.services[provider]
        if not config["base"].startswith(("http://", "https://")):
            raise HTTPException(503, "此 GPU 决策服务尚未配置独立 HTTP 地址。")
        return config

    @staticmethod
    def headers(config):
        return {"Authorization": f"Bearer {config['key']}"} if config["key"] else {}

    async def health(self, provider):
        started = perf_counter()
        connected = False
        try:
            config = self.service(provider)
            response = await self.client.get(
                config["base"] + "/health", headers=self.headers(config), timeout=3
            )
            response.raise_for_status()
            data = response.json()
            connected = (
                data.get("models", {}).get(provider) is True
                if provider in LOCAL_ROUTERS
                else data.get("status") == "ok" and data.get("model") == config["model"]
            )
        except (HTTPException, httpx.HTTPError, ValueError, AttributeError, TypeError):
            pass
        return {
            "provider": provider,
            "connected": connected,
            "check_ms": round((perf_counter() - started) * 1000, 3),
            "message": "GPU 决策服务可连接" if connected else "GPU 决策服务未就绪或不可连接",
        }

    async def infer(self, body, request, payload, *, selection=False, joint=False):
        config = self.service(body.provider)
        started = perf_counter()
        try:
            await asyncio.wait_for(self.slots[body.provider].acquire(), timeout=5)
        except TimeoutError as exc:
            raise HTTPException(429, "GPU 决策队列已满，请稍后重试。") from exc
        invoked_at = perf_counter()
        try:
            async with asyncio.timeout(45):
                response = await self.client.post(
                    config["base"] + config["path"],
                    headers=self.headers(config),
                    json=payload,
                    timeout=httpx.Timeout(45, connect=5),
                )
            returned_at = perf_counter()
            if response.status_code in (422, 429):
                raise HTTPException(response.status_code, "GPU 决策服务拒绝请求或正在排队。")
            response.raise_for_status()
            result = response.json()
            if result.get("model") != config["model"]:
                raise ValueError("Unexpected classifier identity")
            if selection or joint:
                answer = result["answers"]["action"]["choice"]
                if answer not in payload["questions"]["action"]["criteria"]:
                    raise ValueError("GPU returned a model outside the supplied candidates")
                answers = {"action": {"choice": answer}}
                if joint:
                    from app.routing_profile import QUESTIONS
                    profile = parse_profile({"answers": {k: result["answers"][k] for k in QUESTIONS}})
                    answers.update({key: {"choice": value} for key, value in profile.model_dump().items()})
            else:
                profile = parse_profile(result)
                answers = {key: {"choice": value} for key, value in profile.model_dump().items()}
            usage = result.get("usage")
            if usage is not None and (
                not isinstance(usage, dict)
                or any(
                    type(usage.get(k)) is not int or usage[k] < 0
                    for k in ("input_tokens", "output_tokens")
                )
            ):
                raise ValueError("Invalid usage")
            latency = result.get("latency_ms")
            if latency is not None and (
                type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0
            ):
                raise ValueError("Invalid service timing")
            passes = result.get("forward_passes")
            if passes is not None and (type(passes) is not int or passes < 1):
                raise ValueError("Invalid forward-pass count")
            # A service-reported latency may include its own queue/tokenization. Never label it
            # CUDA kernel time, TTFT, or a second additive stage alongside the HTTP round trip.
            return {
                "model": result["model"],
                "answers": answers,
                "usage": usage,
                "deployment": result.get("deployment"),
                "timings": {
                    "execution": "batch",
                    "queue_ms": round((invoked_at - started) * 1000, 3),
                    "upstream_ms": round((returned_at - invoked_at) * 1000, 3),
                    "validation_ms": round((perf_counter() - returned_at) * 1000, 3),
                    "service_reported_ms": latency,
                    "backend_total_ms": round((perf_counter() - started) * 1000, 3),
                    "forward_passes": passes,
                },
            }
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise HTTPException(504, "GPU 决策服务超时，未切换模型。") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503, "GPU 决策服务调用失败，未切换模型。") from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(502, "GPU 决策服务的模型身份或候选判断结果无效。") from exc
        finally:
            self.slots[body.provider].release()

    async def select(self, body, request, pool):
        from app.gateway_model_decision import select_all

        return await select_all(self, body, request, pool)

    async def assess(self, body, request, payload, catalog, rankings, exclusion):
        from app.gateway_joint_decision import assess_and_select

        return await assess_and_select(self, body, request, payload, catalog, rankings, exclusion)
