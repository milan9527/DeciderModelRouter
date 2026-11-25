"""Modality eligibility and executable adapters, separate from text benchmark scores."""

import hashlib
import json

from app.routing_balance import preferences

MEDIA_VERSION = "bedrock-media-v1"
MEDIA_WORKFLOWS = {
    "vision", "video_understanding", "audio_understanding", "image", "video", "speech", "rerank",
}
IMAGE_MODELS = {
    "remove_background": "stability.stable-image-remove-background-v1:0",
    "upscale": "stability.stable-fast-upscale-v1:0",
    "creative_upscale": "stability.stable-creative-upscale-v1:0",
    "conservative_upscale": "stability.stable-conservative-upscale-v1:0",
    "sketch": "stability.stable-image-control-sketch-v1:0",
    "structure": "stability.stable-image-control-structure-v1:0",
    "inpaint": "stability.stable-image-inpaint-v1:0",
}
SOURCE = "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html"


def resolve_workflow(task, profile):
    explicit = {
        "image": "image", "audio": "speech", "video": "video",
        "embedding": "embedding", "rerank": "rerank",
    }.get(task.output_modality)
    if explicit:
        return explicit, "使用请求明确指定的输出类型。"
    if task.output_modality == "auto" and profile.workflow in {"image", "video", "speech"}:
        return profile.workflow, None
    kinds = {item.kind for item in task.attachments}
    if kinds and profile.workflow not in {"embedding", "rerank"}:
        for kind, workflow in (
            ("video", "video_understanding"), ("audio", "audio_understanding"), ("image", "vision"),
        ):
            if kind in kinds:
                return workflow, "按实际上传文件选择理解能力；文字输入本身不表示语音任务。"
    if task.output_modality == "text" and profile.workflow in {"image", "video", "speech"}:
        return "general_text", "请求明确要求文字输出。"
    return profile.workflow, None


def adapter(model, task, workflow):
    mid = model["id"]
    if workflow == "vision":
        # These model families expose Bedrock Converse image content blocks.
        if mid.startswith(("amazon.nova-", "anthropic.", "google.gemma-", "meta.llama4-", "mistral.pixtral-")):
            return "converse_media"
    elif workflow == "video_understanding":
        if mid.startswith("amazon.nova-") and "VIDEO" in model["input_modalities"]:
            return "converse_media"
    elif workflow == "audio_understanding" and mid.startswith("mistral.voxtral-"):
        return "voxtral_audio"
    elif workflow == "speech" and mid == "amazon.nova-2-sonic-v1:0":
        return "sonic_speech"
    elif workflow == "image":
        if mid == IMAGE_MODELS.get(task.media_options.image_operation):
            return "stability_image"
        if task.media_options.image_operation == "generate" and mid in {
            "amazon.nova-canvas-v1:0", "amazon.titan-image-generator-v2:0",
        }:
            return "amazon_image"
    elif workflow == "video" and mid.startswith("amazon.nova-reel-"):
        return "nova_video"
    elif workflow == "rerank" and mid == "cohere.rerank-v3-5:0":
        return "cohere_rerank"
    elif workflow == "embedding" and task.attachments and mid == "cohere.embed-v4:0":
        if all(a.kind == "image" for a in task.attachments):
            return "cohere_image_embedding"
    return None


def required_input(workflow):
    return {"vision": "image", "video_understanding": "video", "audio_understanding": "audio"}.get(workflow)


def eligible(model, task, workflow, exclusion):
    reason = exclusion(model, task, None)
    if reason:
        return reason
    outputs = {"image": "IMAGE", "video": "VIDEO", "speech": "SPEECH", "embedding": "EMBEDDING"}
    expected = outputs.get(workflow, "TEXT")
    if workflow != "rerank" and expected not in model["output_modalities"]:
        return "输出模态不匹配"
    if workflow == "rerank" and "rerank" not in model["id"]:
        return "不支持重排"
    if workflow != "rerank" and "rerank" in model["id"]:
        return "重排模型不用于当前任务"
    inputs = {"SPEECH" if a.kind == "audio" else a.kind.upper() for a in task.attachments}
    if kind := required_input(workflow):
        inputs.add("SPEECH" if kind == "audio" else kind.upper())
    if workflow == "image" and task.media_options.image_operation != "generate":
        inputs.add("IMAGE")
    if not task.attachments or task.text:
        # Speech models may support the text prompt within their native event protocol.
        if workflow != "speech":
            inputs.add("TEXT")
    available = set(model["input_modalities"])
    if "AUDIO" in available:
        available.add("SPEECH")
    if not inputs <= available:
        return "输入模态不匹配"
    if not adapter(model, task, workflow):
        return "目录包含此能力，但当前网关尚无该型号的专用执行适配器"
    return None


def readiness(task, workflow):
    kinds = [item.kind for item in task.attachments]
    needed = required_input(workflow)
    if needed and needed not in kinds:
        return f"请上传{ {'image': '图片', 'video': '视频', 'audio': '音频'}[needed]}后执行。"
    if workflow == "image" and task.media_options.image_operation != "generate" and not kinds:
        return "此图片编辑操作需要上传原图。"
    if workflow == "image" and task.media_options.image_operation == "inpaint" and kinds.count("image") != 2:
        return "局部重绘需要两张图片：原图和同尺寸黑白遮罩。"
    if workflow == "image" and task.media_options.image_operation != "inpaint" and len(kinds) > 1:
        return "此图片操作每次处理一张原图。"
    if workflow == "embedding" and len(kinds) > 1:
        return "图片向量化每次处理一张图片。"
    if workflow == "video" and kinds:
        return "当前视频生成适配器接受文字描述，请移除附件。"
    if workflow in {"audio_understanding", "speech", "video_understanding"} and len(kinds) > 1:
        return "当前音频或视频任务每次处理一个文件。"
    if workflow == "rerank" and not task.media_options.documents:
        return "请提供需要重排的候选文档。"
    return None


def choose_media(task, profile, catalog, rankings, calibration, exclusion):
    workflow, policy = profile.workflow, preferences(task)
    rows, rejected = [], {}
    for model in sorted(catalog["models"], key=lambda m: m["id"]):
        mid = model["id"]
        reason = eligible(model, task, workflow, exclusion)
        if workflow == "speech" and profile.languages in {"zh", "en_zh"}:
            reason = "当前已接入的 Nova 2 Sonic 不支持中文语音。"
        if policy.allowed_models is not None and mid not in policy.allowed_models:
            reason = "不在本次模型允许列表"
        if mid in policy.excluded_models:
            reason = "在本次模型排除列表"
        if any(v is not None for v in (
            policy.max_reference_cost_usd, policy.min_success_rate,
            policy.max_latency_ms, policy.min_context_tokens,
        )):
            reason = "缺少此多模态请求的费用/业务成功率/延迟/上下文测量，无法核验指定硬约束"
        if reason:
            rejected[mid] = reason
            continue
        aid = adapter(model, task, workflow)
        cid = hashlib.sha256(json.dumps([MEDIA_VERSION, mid, aid, workflow]).encode()).hexdigest()[:24]
        rows.append({
            "candidate_id": cid, "model": model, "adapter": aid,
            "benchmark": {
                "source": "bedrock_capabilities", "source_url": SOURCE,
                "benchmark_configuration": f"{aid} · 官方模态支持，未做质量评测",
                "configuration_status": "verified", "metrics": {}, "request_fields": {},
            },
            "capability_evidence": {
                "input_modalities": model["input_modalities"],
                "output_modalities": model["output_modalities"],
                "adapter": aid, "quality_evaluated": False,
            },
            "task_scores": {}, "business_evidence": None, "p95_ms": None,
            "cost_evidence": {"expected_usd": None, "at_output_cap_usd": None},
        })
    missing = readiness(task, workflow)
    if not rows:
        reason = (
            "当前区域目录没有可调用的视频生成模型。可以上传视频进行理解；视频生成需先在 Bedrock 获得可用模型。"
            if workflow == "video" else
            "当前目录没有可调用的文生图模型。可上传原图并选择图片编辑操作。"
            if workflow == "image" and task.media_options.image_operation == "generate" else
            "当前已接入的 Nova 2 Sonic 不支持中文语音，请使用支持的语言。"
            if workflow == "speech" and profile.languages in {"zh", "en_zh"} else
            "没有同时满足输入/输出模态、账户可用性和执行适配器要求的模型。"
        )
    else:
        reason = missing or "按已核实的模态与执行接口选型；暂无可比质量或完整媒体费用测量，采用稳定的型号顺序。"
    return {
        "candidates": rows, "candidate_count": len(rows), "excluded": rejected,
        "available_model_count": len(rows), "catalog_model_count": len(catalog["models"]),
        "catalog_version": catalog["version"], "ranking_version": rankings["version"],
        "routing_version": MEDIA_VERSION, "calibration_version": calibration["version"],
        "profile": profile.model_dump(), "requested_tier": policy.cost_tier,
        "effective_tier": policy.cost_tier, "tier_reason": "媒体能力选型。",
        "evidence_mode": "capability_only", "selection_source": "deterministic_policy",
        "selected_candidate_id": rows[0]["candidate_id"] if rows else None,
        "selected_benchmark": rows[0]["benchmark"] if rows else None,
        "frontier": [r["candidate_id"] for r in rows], "reason": reason,
        "benchmark_group": None, "benchmark_groups": [], "cost_mode": "unknown_media_cost",
        "latency_used": False, "execution_ready": bool(rows) and missing is None,
        "execution_notice": missing, "media_version": MEDIA_VERSION,
        "cost_context": {
            "input_tokens": None, "input_token_source": "media_not_tokenized",
            "expected_output_tokens": policy.expected_output_tokens,
            "max_output_tokens": policy.max_output_tokens,
            "price_scope": "unknown_media_cost",
        },
    }


def capability_catalog(catalog, exclusion):
    from app.media_types import MediaOptions
    from app.routing import Task

    entries = []
    scenarios = [
        ("vision", "图片理解", "auto", "generate"),
        ("video_understanding", "视频理解", "auto", "generate"),
        ("audio_understanding", "音频转写与理解", "auto", "generate"),
        ("speech", "语音生成与回复", "audio", "generate"),
        ("image", "文字生成图片", "image", "generate"),
        *[("image", label, "image", operation) for operation, label in (
            ("remove_background", "移除图片背景"), ("upscale", "图片放大"),
            ("creative_upscale", "创意放大"), ("conservative_upscale", "保真放大"),
            ("sketch", "草图生成图片"), ("structure", "结构引导生成"),
            ("inpaint", "图片局部重绘"),
        )],
        ("video", "视频生成", "video", "generate"),
        ("rerank", "文档重排", "rerank", "generate"),
    ]
    for workflow, label, output, operation in scenarios:
        task = Task(text="capability inventory", output_modality=output,
                    media_options=MediaOptions(image_operation=operation))
        models = [m["id"] for m in catalog["models"] if eligible(m, task, workflow, exclusion) is None]
        entries.append({
            "workflow": workflow, "label": label, "output_modality": output,
            "image_operation": operation, "model_ids": models,
            "status": "available" if models else "unavailable",
            "quality_evaluated": False, "needs": readiness(task, workflow),
        })
    return {
        "version": MEDIA_VERSION, "scenarios": entries,
        "note": "可用表示目录、账户和执行接口匹配；不表示已完成质量评测或保证实时配额。",
        "upload_limits_mb": {"image": 3.5, "audio": 10, "video": 20},
    }
