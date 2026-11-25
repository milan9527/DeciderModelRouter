"""One bounded spoken turn over Bedrock's native bidirectional Sonic API."""

import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import uuid
import wave

import boto3
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


def pcm_audio(inputs):
    if not inputs:
        return None
    metadata, raw = inputs[0]
    if metadata["format"] != "wav":
        raise HTTPException(422, "语音回复的录音输入需要 WAV：16kHz、16 位、单声道，最长 30 秒。")
    try:
        with wave.open(io.BytesIO(raw)) as recording:
            if (
                recording.getnchannels() != 1 or recording.getsampwidth() != 2
                or recording.getframerate() != 16000
                or recording.getnframes() > 16000 * 30
            ):
                raise ValueError("Unsupported audio")
            return recording.readframes(recording.getnframes())
    except (wave.Error, EOFError, ValueError) as exc:
        raise HTTPException(422, "需要 16kHz、16 位、单声道 WAV，最长 30 秒。") from exc


async def speak(model_id, text, inputs, voice, max_tokens):
    try:
        from aws_sdk_bedrock_runtime.aio.client import AsyncBedrockRuntimeClient
        from aws_sdk_bedrock_runtime.aio.config import AsyncBedrockRuntimeConfig
        from aws_sdk_bedrock_runtime.models import (
            BidirectionalInputPayloadPart,
            InvokeModelWithBidirectionalStreamInputChunk,
        )
        from smithy_aws_core.aio.identity.static import AsyncStaticCredentialsResolver
        from smithy_aws_core.identity import AWSCredentialsIdentity
        from smithy_core.retries import SimpleRetryStrategy
        from smithy_http.aio.crt import AWSCRTHTTPClient
    except ImportError as exc:
        raise HTTPException(503, "语音执行需要 Python 3.12 与 speech 依赖。") from exc

    pcm = pcm_audio(inputs)
    credentials = await run_in_threadpool(
        lambda: boto3.Session().get_credentials().get_frozen_credentials(),
    )
    transport = AWSCRTHTTPClient()
    config = await AsyncBedrockRuntimeConfig.resolve(
        region=os.getenv("AWS_REGION", "us-east-1"),
        transport=transport,
        retry_strategy=SimpleRetryStrategy(max_attempts=1),
        aws_credentials_identity_resolver=AsyncStaticCredentialsResolver(
            AWSCredentialsIdentity(
                access_key_id=credentials.access_key, secret_access_key=credentials.secret_key,
                session_token=credentials.token,
            ),
        ),
    )
    client = AsyncBedrockRuntimeClient(config)
    stream = None
    sender = None
    prompt = uuid.uuid4().hex
    audio_name = uuid.uuid4().hex
    audio_open = False
    audio = bytearray()
    transcript, speculative, user_transcript = [], [], []
    blocks = {}
    usage, stop = None, None
    audio_started = asyncio.Event()

    async def send(name, value):
        await stream.input_stream.send(InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=json.dumps({"event": {name: value}}).encode()),
        ))

    async def text_block(value, role, interactive):
        name = uuid.uuid4().hex
        await send("contentStart", {
            "promptName": prompt, "contentName": name, "type": "TEXT",
            "role": role, "interactive": interactive,
            "textInputConfiguration": {"mediaType": "text/plain"},
        })
        await send("textInput", {"promptName": prompt, "contentName": name, "content": value})
        await send("contentEnd", {"promptName": prompt, "contentName": name})

    async def feed_audio():
        nonlocal audio_open
        await send("contentStart", {
            "promptName": prompt, "contentName": audio_name, "type": "AUDIO",
            "role": "USER", "interactive": True,
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": 16000,
                "sampleSizeBits": 16, "channelCount": 1, "audioType": "SPEECH", "encoding": "base64",
            },
        })
        audio_open = True
        source = pcm or b""
        offset = 0
        while True:
            await send("audioInput", {
                "promptName": prompt, "contentName": audio_name,
                "content": base64.b64encode(
                    source[offset:offset + 1024] if offset < len(source) else b"\x00" * 1024,
                ).decode(),
            })
            audio_started.set()
            offset += 1024
            await asyncio.sleep(0.032)

    try:
        async with asyncio.timeout(110):
            stream = await client.invoke_model_with_bidirectional_stream(model_id=model_id)
            await send("sessionStart", {"inferenceConfiguration": {
                "maxTokens": min(max_tokens, 1024), "topP": 0.9, "temperature": 0.5,
            }})
            await send("promptStart", {
                "promptName": prompt, "textOutputConfiguration": {"mediaType": "text/plain"},
                "audioOutputConfiguration": {
                    "mediaType": "audio/lpcm", "sampleRateHertz": 24000, "sampleSizeBits": 16,
                    "channelCount": 1, "voiceId": voice, "encoding": "base64", "audioType": "SPEECH",
                },
            })
            await text_block(
                "You are a helpful voice assistant. Respond briefly to one user turn. "
                "When asked to read text aloud, read the requested text without extra commentary.",
                "SYSTEM", False,
            )
            if pcm is not None:
                await text_block(text, "SYSTEM", False)
            sender = asyncio.create_task(feed_audio())
            await audio_started.wait()
            if pcm is None:
                await text_block(text, "USER", True)
            _, output = await stream.await_output()
            while result := await output.receive():
                if not hasattr(result.value, "bytes_"):
                    raise HTTPException(502, "Bedrock 语音流返回错误，未自动重试。")
                event = json.loads(result.value.bytes_)["event"]
                logger.debug("Sonic output event types: %s", list(event))
                if "contentStart" in event:
                    block = event["contentStart"]
                    blocks[block["contentId"]] = (
                        block.get("role"), json.loads(block.get("additionalModelFields", "{}"))
                        .get("generationStage"),
                    )
                elif "textOutput" in event:
                    block = event["textOutput"]
                    role, stage = blocks.get(block["contentId"], (None, None))
                    if role == "USER":
                        user_transcript.append(block["content"])
                    elif role == "ASSISTANT":
                        (transcript if stage == "FINAL" else speculative).append(block["content"])
                elif "audioOutput" in event:
                    audio.extend(base64.b64decode(event["audioOutput"]["content"], validate=True))
                    if len(audio) > 12_000_000:
                        raise HTTPException(502, "语音响应超过当前长度上限。")
                elif "usageEvent" in event:
                    data = event["usageEvent"]
                    usage = {
                        "input_tokens": data.get("totalInputTokens"),
                        "output_tokens": data.get("totalOutputTokens"),
                        "speech_details": data.get("details"),
                    }
                elif "contentEnd" in event and audio:
                    data = event["contentEnd"]
                    role, stage = blocks.get(data["contentId"], (None, None))
                    if role == "ASSISTANT" and stage == "FINAL" and data.get("stopReason") == "END_TURN":
                        stop = "END_TURN"
                        break
                elif "completionEnd" in event and audio:
                    stop = event["completionEnd"].get("stopReason")
                    break
        if not audio:
            raise HTTPException(502, "语音模型未返回可播放音频。")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(bytes(audio))
        return {
            "text": "".join(transcript or speculative), "input_transcript": "".join(user_transcript),
            "usage": usage, "complete": stop == "END_TURN", "stop_reason": stop,
            "effective_max_output_tokens": min(max_tokens, 1024),
            "_artifacts": [(buffer.getvalue(), "audio/wav")],
        }
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(504, "语音生成超时，未自动重试。") from exc
    except Exception as exc:
        raise HTTPException(502, f"Bedrock 语音调用失败（{type(exc).__name__}），未自动重试。") from exc
    finally:
        if sender:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
        if stream:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(3):
                    if audio_open:
                        await send("contentEnd", {"promptName": prompt, "contentName": audio_name})
                    await send("promptEnd", {"promptName": prompt})
                    await send("sessionEnd", {})
                    await stream.input_stream.close()
                    if stream.output_stream:
                        while await stream.output_stream.receive():
                            pass
                    await stream.close()
            with contextlib.suppress(Exception):
                await stream.close()
        with contextlib.suppress(Exception):
            await client.close()
