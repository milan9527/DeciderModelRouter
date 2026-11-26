"""Run the real catalog builder inside the release image, without AWS or GPU calls."""

import json
from types import SimpleNamespace

from app.gateway import catalog
from app.gateway_model_pool import MODEL_ROUTING_VERSION
from app.quality_feedback import load_feedback
from app.routing_quality import VERSION

service = SimpleNamespace(public_config=lambda: {})
request = SimpleNamespace(app=SimpleNamespace(
    state=SimpleNamespace(gateway_decision=service, bedrock=None)))
result = catalog(request)
assert result["models"] and result["examples"] and result["categories"]
assert result["model_routing"]["version"] == MODEL_ROUTING_VERSION
assert result["model_routing"]["quality_policy_version"] == VERSION
assert result["model_routing"]["quality_feedback_version"] == load_feedback()["version"]
print(json.dumps({
    "catalog_build": "passed",
    "models": len(result["models"]),
    "templates": len(result["examples"]),
    "routing_version": MODEL_ROUTING_VERSION,
    "quality_feedback_version": load_feedback()["version"],
}))
