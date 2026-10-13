"""Self-hosted open-weight models behind an OpenAI-compatible server (vLLM, Ollama, llama.cpp server).

The point is sovereignty: the same extraction code runs with no data leaving the customer's network.

  LICITA_LLM_BASE_URL   default http://localhost:8000/v1 (vLLM); Ollama is http://localhost:11434/v1
  LICITA_LOCAL_MODEL    default mistralai/Mistral-Small-3.2-24B-Instruct-2506 (Apache-2.0 weights)
  LICITA_LLM_API_KEY    optional bearer token if the server requires one

There is no hosted batch API here: callers run requests concurrently against the job table instead.
"""
from __future__ import annotations

import os
import time

import httpx

from licita.llm.base import LLMError, Result, RetryPolicy, Task, json_schema_format, parse_completion, send

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"


class LocalProvider:
    name = "local"

    def __init__(self, models: dict[Task, str] | None = None, http: httpx.Client | None = None,
                 policy: RetryPolicy | None = None, base_url: str | None = None):
        default = os.environ.get("LICITA_LOCAL_MODEL") or DEFAULT_MODEL
        self.models = {t: default for t in Task if t is not Task.OCR} | (models or {})
        self.base_url = (base_url or os.environ.get("LICITA_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        token = os.environ.get("LICITA_LLM_API_KEY")
        self.http = http or httpx.Client(timeout=httpx.Timeout(600, connect=10),
                                         headers={"Authorization": f"Bearer {token}"} if token else {})
        self.policy = policy or RetryPolicy(attempts=3)

    def model_for(self, task: Task) -> str:
        if task not in self.models:
            raise LLMError(f"no local model for {task.value}", "OCR has no OpenAI-compatible equivalent here",
                           "use the pdfplumber text (default) or LICITA_LLM_PROVIDER=mistral for OCR")
        return self.models[task]

    def resolve_model(self, model: str) -> str:
        return model   # open weights are already pinned by their repository id

    def complete(self, messages: list[dict], model: str, schema: dict | None = None, *,
                 max_tokens: int = 4000, temperature: float = 0.0) -> Result:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if schema is not None:
            body["response_format"] = json_schema_format(schema)
        t0 = time.monotonic()
        try:
            r, attempts = send(self.http, "POST", f"{self.base_url}/chat/completions", self.policy,
                               f"the local model server at {self.base_url}", json=body)
        except LLMError as e:
            raise LLMError(e.problem, e.cause, f"start the server (e.g. `docker compose --profile vllm up`) and "
                                               f"check: curl {self.base_url}/models") from e
        if r.status_code >= 400:
            raise LLMError(f"local model server error (HTTP {r.status_code})", r.text[:300],
                           f"check that {model!r} is served: curl {self.base_url}/models")
        return parse_completion(r.json(), latency_s=time.monotonic() - t0, attempts=attempts)
