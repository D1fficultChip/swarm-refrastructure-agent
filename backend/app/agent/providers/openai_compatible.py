import json
import os
from time import perf_counter

import httpx

from ..prompt import SYSTEM_INSTRUCTION
from ..policy_prompt import POLICY_SYSTEM,OPTIMIZED_MODE,SNAPSHOT_MODE
from ..prompt_metrics import measure_prompt
from ..decision_snapshot import compact
from .base import ModelError, ModelProvider, ModelReply
from .schema import response_schema


class OpenAICompatibleProvider(ModelProvider):
    def __init__(self, config, *, transport=None):
        self.config, self.transport = config, transport
        self.name = config.model_name

    def build_request(self, context, tool_schemas):
        optimized=context.get("policy_version")=="5.1"
        action_schema=response_schema(tool_schemas,optimized=optimized)
        response_format={"type":self.config.structured_output}
        if self.config.structured_output=="json_schema":
            response_format["json_schema"]={"name":"agent_action","strict":True,"schema":action_schema}
        system=(POLICY_SYSTEM+(SNAPSHOT_MODE if context.get("profile")=="snapshot" else OPTIMIZED_MODE)) if optimized else SYSTEM_INSTRUCTION+"\nAgentAction JSON schema:\n"+json.dumps(action_schema)
        # Optimized typed parameters already occur in response_format. Send only
        # short tool purposes here, avoiding a duplicate copy of their schemas.
        descriptions=[{"name":t["name"],"description":t["description"]} for t in tool_schemas] if optimized else tool_schemas
        if optimized and self.config.structured_output!="json_schema":
            system+="\nAction schema: "+compact(action_schema)
        body={"model":self.config.model_name,"temperature":self.config.model_temperature,
            "max_tokens":self.config.model_max_output_tokens,"response_format":response_format,
            "messages":[{"role":"system","content":system},
                        {"role":"user","content":compact({"context":context,"tools":descriptions})}]}
        if self.config.enable_thinking is not None:
            body["enable_thinking"]=self.config.enable_thinking
        return body,measure_prompt(system,descriptions,action_schema,context,body)

    def generate_action(self, context, tool_schemas, *, timeout_seconds, max_attempts):
        key = os.environ.get("MODEL_API_KEY", "").strip()
        if not key:
            raise ModelError("MODEL_API_KEY_MISSING", attempts=0)
        body,prompt_metrics=self.build_request(context,tool_schemas)
        end = perf_counter() + timeout_seconds
        attempts = 0
        code = "MODEL_TIMEOUT"
        with httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False) as client:
            for _ in range(min(max_attempts, self.config.model_max_retries + 1)):
                remaining = end - perf_counter()
                if remaining <= 0:
                    break
                attempts += 1
                try:
                    response = client.post(self.config.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + key}, json=body,
                        timeout=min(remaining, self.config.model_timeout_seconds))
                except httpx.TimeoutException:
                    code = "MODEL_TIMEOUT"
                    continue
                except httpx.HTTPError:
                    code = "MODEL_CONNECTION_ERROR"
                    continue
                if response.status_code in {429, 500, 502, 503, 504}:
                    code = f"MODEL_HTTP_{response.status_code}"
                    continue
                if response.status_code != 200:
                    raise ModelError(f"MODEL_HTTP_{response.status_code}", attempts)
                try:
                    payload = response.json()
                    content = payload["choices"][0]["message"]["content"]
                    # reasoning_content and the raw HTTP response are never retained.
                    usage = {k: v for k, v in payload.get("usage", {}).items() if isinstance(v, int) and not isinstance(v, bool)}
                    return ModelReply(action=content, model_name=payload.get("model", self.name), attempts=attempts, token_usage=usage,
                        prompt_metrics=prompt_metrics.model_dump())
                except (ValueError, KeyError, IndexError, TypeError):
                    raise ModelError("MODEL_RESPONSE_INVALID", attempts) from None
        raise ModelError(code, attempts)
