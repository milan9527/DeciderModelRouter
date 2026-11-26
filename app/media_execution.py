"""Bounded native Bedrock requests for validated private media references."""

import base64
import json
import secrets
import time

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.bedrock_limits import output_limit
from app.routing_balance import preferences
from app.routing_media import readiness


def invoke_json(client, model_id, body):
    response = client.invoke_model(
        modelId=model_id, contentType="application/json", accept="application/json",
        body=json.dumps(body),
    )
    try:
        return json.loads(response["body"].read())
    finally:
        response["body"].close()


def native(client, row, task, inputs):
    aid, mid = row["adapter"], row["model"]["invoke_id"]
    budget = preferences(task).max_output_tokens
    if aid == "pegasus_video":
        output = invoke_json(client, mid, {
            "inputPrompt": task.text,
            "mediaSource": {"base64String": base64.b64encode(inputs[0][1]).decode()},
            "maxOutputTokens": max(512, budget),
        })
        return {"text": output["message"], "complete": bool(output["message"]),
                "stop_reason": output.get("stopReason", "completed"),
                "usage": output.get("usage")}
    if aid == "converse_media":
        content = [{"text": task.text}]
        for metadata, raw in inputs:
            kind = metadata["kind"]
            content.append({kind: {"format": metadata["format"], "source": {"bytes": raw}}})
        benchmark = row.get("benchmark", {})
        kwargs = {
            "modelId": mid, "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {**benchmark.get("inference_config", {}),
                                "maxTokens": output_limit(row["model"], budget)},
        }
        if fields := benchmark.get("request_fields"):
            kwargs["additionalModelRequestFields"] = fields
        response = client.converse(**kwargs)
        text = "\n".join(x["text"] for x in response["output"]["message"]["content"] if "text" in x)
        return {
            "text": text, "usage": response.get("usage"),
            "stop_reason": response.get("stopReason"),
            "complete": bool(text.strip()) and response.get("stopReason") == "end_turn",
        }
    if aid == "voxtral_audio":
        metadata, raw = inputs[0]
        output = invoke_json(client, mid, {
            "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(raw).decode(), "format": metadata["format"],
                }},
                {"type": "text", "text": task.text},
            ]}],
            "max_tokens": budget,
        })
        choice = output["choices"][0]
        text = choice["message"]["content"]
        stop = choice.get("finish_reason", choice.get("stop_reason"))
        return {
            "text": text, "complete": bool(text) and stop in ("stop", "end_turn"),
            "stop_reason": stop,
            "usage": {
                "input_tokens": output.get("usage", {}).get("prompt_tokens"),
                "output_tokens": output.get("usage", {}).get("completion_tokens"),
            },
        }
    if aid in {"stability_image", "amazon_image"}:
        if aid == "stability_image":
            operation = task.media_options.image_operation
            body = {"image": base64.b64encode(inputs[0][1]).decode(), "output_format": "png"}
            if operation not in {"remove_background", "upscale", "erase"}:
                body.update(prompt=task.text, seed=task.media_options.seed)
            if operation in {"inpaint", "erase"}:
                body["mask"] = base64.b64encode(inputs[1][1]).decode()
            if operation == "outpaint":
                body["left"] = task.media_options.outpaint_pixels
            if operation in {"recolor", "replace"}:
                body["select_prompt" if operation == "recolor" else "search_prompt"] = task.media_options.edit_target
            if operation == "style_transfer":
                body["init_image"] = body.pop("image")
                body["style_image"] = base64.b64encode(inputs[1][1]).decode()
        else:
            body = {
                "taskType": "TEXT_IMAGE", "textToImageParams": {"text": task.text},
                "imageGenerationConfig": {
                    "numberOfImages": 1, "height": 512, "width": 512,
                    "seed": task.media_options.seed,
                },
            }
        output = invoke_json(client, mid, body)
        encoded = output.get("images") or []
        if not encoded or not encoded[0]:
            raise HTTPException(422, "图像模型未返回图片，内容可能被过滤；未自动重试。")
        raw = base64.b64decode(encoded[0], validate=True)
        return {
            "text": "图片已生成，可预览和下载。", "complete": True, "usage": None,
            "_artifacts": [(raw, "image/png")], "stop_reason": "completed",
        }
    if aid == "cohere_rerank":
        output = invoke_json(client, mid, {
            "api_version": 2, "query": task.text,
            "documents": task.media_options.documents,
            "top_n": len(task.media_options.documents),
        })
        results = output["results"]
        return {
            "text": "\n".join(
                f"{i + 1}. [{r['index']}] {task.media_options.documents[r['index']]} "
                f"({r['relevance_score']:.4f})" for i, r in enumerate(results)
            ),
            "rerank_results": results, "complete": True, "usage": None,
        }
    if aid == "cohere_image_embedding":
        metadata, raw = inputs[0]
        output = invoke_json(client, mid, {
            "images": [f"data:{metadata['content_type']};base64," + base64.b64encode(raw).decode()],
            "input_type": "image", "embedding_types": ["float"],
        })
        vector = output["embeddings"]["float"][0]
        return {
            "text": f"已生成 {len(vector)} 维图片向量，预览前 12 维。",
            "dimensions": len(vector), "vector_preview": vector[:12],
            "complete": True, "usage": None,
        }
    raise HTTPException(422, "此型号的专用执行适配器尚未配置。")


async def execute_media(request, client, row, task, workflow):
    if missing := readiness(task, workflow):
        raise HTTPException(422, missing)
    media = request.app.state.media
    user = request.state.user["username"]
    inputs = [
        await run_in_threadpool(media.read, user, ref.id) for ref in task.attachments
    ]
    if row["adapter"] == "sonic_speech":
        from app.media_speech import speak

        result = await speak(
            row["model"]["invoke_id"], task.text, inputs,
            task.media_options.voice, preferences(task).max_output_tokens,
        )
    elif row["adapter"] == "nova_video":
        if not media.bucket:
            raise HTTPException(503, "异步视频生成需要专用 S3 媒体存储。")
        job_id = secrets.token_hex(16)
        prefix = media.key(user, job_id) + "/"
        result = await run_in_threadpool(
            client.start_async_invoke,
            modelId=row["model"]["invoke_id"],
            clientRequestToken=job_id,
            modelInput={
                "taskType": "TEXT_VIDEO", "textToVideoParams": {"text": task.text},
                "videoGenerationConfig": {
                    "durationSeconds": 6, "fps": 24, "dimension": "1280x720",
                    "seed": task.media_options.seed,
                },
            },
            outputDataConfig={"s3OutputDataConfig": {"s3Uri": f"s3://{media.bucket}/{prefix}"}},
        )
        job = {
            "id": job_id, "status": "running", "invocation_arn": result["invocationArn"],
            "prefix": prefix, "created_at": int(time.time()),
            "expires_at": int(time.time()) + 180 * 86400,
            "url": f"/api/gateway/media/jobs/{job_id}", "artifacts": [],
        }
        await run_in_threadpool(media.records.put, "USER#" + user, "MEDIAJOB#" + job_id, job)
        return {"text": "视频生成已提交，正在处理中。", "complete": False, "media_job": {
            k: job[k] for k in ("id", "status", "url", "artifacts")
        }, "usage": None, "stop_reason": "async_pending"}
    else:
        result = await run_in_threadpool(native, client, row, task, inputs)
    artifacts = [
        await run_in_threadpool(media.put, user, raw, mime, generated=True)
        for raw, mime in result.pop("_artifacts", [])
    ]
    return {**result, "artifacts": artifacts, "adapter": row["adapter"], "media_cost_known": False}
