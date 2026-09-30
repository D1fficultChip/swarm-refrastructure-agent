"""Trace hygiene: no credentials, raw provider bodies or hidden reasoning."""
import os
import re


def redact(value):
    if isinstance(value, str):
        key = os.environ.get("MODEL_API_KEY")
        if key:
            value = value.replace(key, "[REDACTED]")
        value = re.sub(r"sk-[A-Za-z0-9._-]+", "[REDACTED]", value)
        return re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in {"authorization", "api_key", "model_api_key", "reasoning_content", "hidden_reasoning"}
                else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
