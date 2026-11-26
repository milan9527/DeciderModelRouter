"""Native text embeddings, preserving each provider's documented input schema."""

import math

from app.media_execution import invoke_json


def body_for(model_id, text, *, document=False):
    if model_id.startswith("amazon.titan-embed"):
        return {"inputText": text}
    if model_id.startswith("cohere.embed"):
        return {"texts": [text], "input_type": "search_document" if document else "search_query",
                **({"embedding_types": ["float"]} if "v4" in model_id else {})}
    if model_id.startswith("amazon.nova-2-multimodal-embeddings"):
        return {"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": {
            "embeddingPurpose": "GENERIC_INDEX" if document else "GENERIC_RETRIEVAL",
            "embeddingDimension": 3072, "text": {"truncationMode": "END", "value": text}}}
    if model_id.startswith("twelvelabs.marengo-embed-3"):
        return {"inputType": "text", "text": {"inputText": text}}
    if model_id.startswith("twelvelabs.marengo"):
        return {"inputType": "text", "inputText": text}
    raise ValueError("Unsupported text embedding adapter")


def vector_from(value):
    if isinstance(value, list):
        if value and all(type(x) in (int, float) and math.isfinite(x) for x in value):
            return value
        for item in value:
            if (vector := vector_from(item)) is not None:
                return vector
    if isinstance(value, dict):
        for key in ("embedding", "embeddings", "float", "data"):
            if key in value and (vector := vector_from(value[key])) is not None:
                return vector
    return None


def embed(client, model, text, *, document=False):
    output = invoke_json(client, model["invoke_id"], body_for(model["id"], text, document=document))
    vector = vector_from(output)
    if vector is None:
        raise ValueError("No finite embedding vector in the native response")
    return vector, output
