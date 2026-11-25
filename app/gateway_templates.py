"""Versioned, editable task templates and explicit classification reference labels."""

import hashlib
import json
from pathlib import Path


def load_templates():
    raw = (Path(__file__).parent / "gateway_templates.json").read_bytes()
    return {"version": hashlib.sha256(raw).hexdigest()[:16], "items": json.loads(raw)}


def evaluation_cases():
    return [row for row in load_reference()["items"] if row.get("expected_profile")]


def load_reference():
    raw = (Path(__file__).parent / "gateway_reference_templates.json").read_bytes()
    return {"version": hashlib.sha256(raw).hexdigest()[:16], "items": json.loads(raw)}
