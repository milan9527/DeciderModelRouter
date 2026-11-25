import io
import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app import routing
from app.gateway_media import MediaStore
from app.gateway_store import Records
from app.media_execution import native
from app.media_types import MediaOptions, MediaRef
from app.routing_media import choose_media, resolve_workflow
from app.routing_profile import RequestProfile
from app.routing_selector import load_calibration


def profile(workflow="general_text"):
    return RequestProfile(
        workflow=workflow, difficulty="simple", stronger_model_benefit="low",
        latency="normal", tools="no", structured_output="no", languages="en",
    )


def selection(task, workflow):
    return choose_media(
        task, profile(workflow), routing.load_catalog(), routing.load_rankings(),
        load_calibration(), routing.exclusion,
    )


@pytest.mark.parametrize("text", ["hi", "早上好", "How are you?", "帮我想个名字", "解释什么是彩虹"])
def test_free_text_needs_no_template_to_select_a_bedrock_model(text):
    result = routing.auto_selection(
        routing.Task(text=text), profile(), routing.load_catalog(),
        routing.load_rankings(), load_calibration(),
    )
    assert result["selected_candidate_id"] is not None
    assert result["effective_tier"] == "low"


@pytest.mark.parametrize("kind,workflow", [
    ("image", "vision"), ("video", "video_understanding"), ("audio", "audio_understanding"),
])
def test_actual_attachment_requires_compatible_model_and_adapter(kind, workflow):
    task = routing.Task(text="Describe this", attachments=[MediaRef(id="a" * 32, kind=kind)])
    actual, reason = resolve_workflow(task, profile())
    assert actual == workflow and reason
    result = selection(task, actual)
    assert result["selected_candidate_id"] and result["evidence_mode"] == "capability_only"
    required = "SPEECH" if kind == "audio" else kind.upper()
    assert all(required in r["model"]["input_modalities"] for r in result["candidates"])
    assert all(r["cost_evidence"]["expected_usd"] is None for r in result["candidates"])
    assert all(r["task_scores"] == {} for r in result["candidates"])


def test_media_cost_or_quality_constraints_are_not_silently_ignored():
    task = routing.Task(
        text="Describe this", attachments=[MediaRef(id="a" * 32, kind="image")],
        routing_preferences={"max_reference_cost_usd": 0},
    )
    assert selection(task, "vision")["selected_candidate_id"] is None


def test_typed_greeting_does_not_force_audio_and_explicit_output_is_preserved():
    task = routing.Task(text="hi")
    assert resolve_workflow(task, profile()) == ("general_text", None)
    task.output_modality = "audio"
    assert resolve_workflow(task, profile())[0] == "speech"


def test_uploaded_media_does_not_override_an_assessed_generation_request():
    task = routing.Task(text="Reply aloud", attachments=[MediaRef(id="b" * 32, kind="audio")])
    assert resolve_workflow(task, profile("speech")) == ("speech", None)
    task.output_modality = "text"
    assert resolve_workflow(task, profile("speech"))[0] == "audio_understanding"


def test_editing_selects_the_requested_operation_and_missing_input_is_explicit():
    task = routing.Task(
        text="Remove the background", output_modality="image",
        media_options=MediaOptions(image_operation="remove_background"),
    )
    result = selection(task, "image")
    assert result["selected_candidate_id"] and not result["execution_ready"]
    assert "原图" in result["execution_notice"]
    assert {r["model"]["id"] for r in result["candidates"]} == {
        "stability.stable-image-remove-background-v1:0",
    }


def test_video_generation_does_not_substitute_video_understanding():
    result = selection(routing.Task(text="Make a video", output_modality="video"), "video")
    assert result["selected_candidate_id"] is None
    assert "视频生成" in result["reason"]


def test_private_media_is_account_scoped_immutable_and_never_stored_in_history_json(tmp_path, monkeypatch):
    monkeypatch.delenv("GATEWAY_MEDIA_BUCKET", raising=False)
    monkeypatch.delenv("GATEWAY_DATA_TABLE", raising=False)
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "records.db"))
    monkeypatch.setenv("GATEWAY_MEDIA_DIR", str(tmp_path / "media"))
    store = MediaStore(Records())
    raw = b"\x89PNG\r\n\x1a\nprivate-image-payload"
    item = store.put("alice", raw, "image/png")
    assert store.read("alice", item["id"])[1] == raw
    assert raw.decode("latin-1") not in json.dumps(item)
    with pytest.raises(HTTPException) as e:
        store.read("bob", item["id"])
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        store.resolve("alice", [MediaRef(id=item["id"], kind="audio")])
    assert e.value.status_code == 422
    with pytest.raises(HTTPException):
        store.put("alice", b"<script>alert(1)</script>", "image/png")
    path = store.local / store.key("alice", item["id"])
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_bytes(raw + b"tampered")
    with pytest.raises(HTTPException) as e:
        store.read("alice", item["id"])
    assert e.value.status_code == 502


def test_converse_sends_actual_video_bytes_and_truncation_is_not_success():
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"content": [{"text": "Partial"}]}},
        "stopReason": "max_tokens", "usage": {"inputTokens": 5, "outputTokens": 10},
    }
    task = routing.Task(text="Explain the video")
    result = native(client, {"adapter": "converse_media", "model": {"invoke_id": "nova"}}, task,
                    [({"kind": "video", "format": "mp4"}, b"actual-video")])
    assert not result["complete"]
    content = client.converse.call_args.kwargs["messages"][0]["content"]
    assert content[1]["video"]["source"]["bytes"] == b"actual-video"
    assert "image" not in content[1]


def test_filtered_image_is_not_reported_as_a_generated_artifact():
    client = MagicMock()
    client.invoke_model.return_value = {
        "body": io.BytesIO(json.dumps({"images": [None], "finish_reasons": ["Filter reason: output image"]}).encode()),
    }
    task = routing.Task(
        text="Remove background", media_options=MediaOptions(image_operation="remove_background"),
    )
    with pytest.raises(HTTPException) as e:
        native(client, {"adapter": "stability_image", "model": {"invoke_id": "stability"}}, task,
               [({"format": "png"}, b"image")])
    assert e.value.status_code == 422
