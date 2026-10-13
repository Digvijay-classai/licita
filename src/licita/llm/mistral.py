"""Mistral AI (La Plateforme): chat completions with JSON-schema output, batch jobs, OCR, model aliases.

Plain REST over httpx, so the request shape is visible and every path is testable offline with a mock transport.

  MISTRAL_API_KEY       required (never read from files in the repo; empty or whitespace is an error)
  LICITA_MISTRAL_URL    optional, defaults to https://api.mistral.ai
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import time
from collections.abc import Iterator

import httpx

from licita.llm.base import (
    BatchLine,
    BatchState,
    BatchStatus,
    LLMError,
    Result,
    RetryPolicy,
    Task,
    json_schema_format,
    parse_batch_line,
    parse_completion,
    send,
)

DEFAULT_URL = "https://api.mistral.ai"
DEFAULT_MODELS = {
    Task.EXTRACT: "mistral-small-latest",
    Task.LINT: "mistral-medium-latest",
    Task.MEMO: "mistral-medium-latest",
    Task.CHAT: "mistral-large-latest",
    Task.OCR: "mistral-ocr-latest",
}
CHAT_ENDPOINT = "/v1/chat/completions"


def api_key() -> str:
    key = os.environ.get("MISTRAL_API_KEY")
    if key is None:
        raise LLMError("MISTRAL_API_KEY is not set",
                       "live Mistral calls need an API key (console.mistral.ai → API keys)",
                       "export MISTRAL_API_KEY=... in this shell, or set LICITA_LLM_PROVIDER=local for a "
                       "self-hosted model, or run `make demo` (cached outputs, no key)")
    if not key.strip():
        raise LLMError("MISTRAL_API_KEY is empty", "the variable is set but blank",
                       "export the real key, or `unset MISTRAL_API_KEY` to use a key-free mode")
    return key.strip()


class MistralProvider:
    name = "mistral"

    def __init__(self, models: dict[Task, str] | None = None, http: httpx.Client | None = None,
                 policy: RetryPolicy | None = None, base_url: str | None = None):
        self.models = {**DEFAULT_MODELS, **(models or {})}
        self.base_url = (base_url or os.environ.get("LICITA_MISTRAL_URL") or DEFAULT_URL).rstrip("/")
        self._http = http
        self.policy = policy or RetryPolicy()
        self._aliases: dict[str, str] | None = None

    # ------------------------------------------------------------ plumbing
    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=httpx.Timeout(120, connect=15),
                                      headers={"Authorization": f"Bearer {api_key()}"})
        return self._http

    def _call(self, method: str, path: str, idempotent: bool = True, **kwargs) -> tuple[httpx.Response, int]:
        r, attempts = send(self.http, method, f"{self.base_url}{path}", self.policy, "the Mistral API",
                           idempotent=idempotent, **kwargs)
        if r.status_code in (401, 403):
            raise LLMError(f"Mistral rejected the API key (HTTP {r.status_code})", r.text[:200],
                           "create or rotate a key at console.mistral.ai and export MISTRAL_API_KEY again")
        if r.status_code >= 400:
            raise LLMError(f"Mistral API error on {method} {path} (HTTP {r.status_code})", r.text[:300],
                           "check the model name and request; HTTP 429/5xx were already retried with backoff")
        return r, attempts

    # ------------------------------------------------------------ models
    def model_for(self, task: Task) -> str:
        return self.models[task]

    def resolve_model(self, model: str) -> str:
        """Map an alias such as `mistral-small-latest` to the dated model it points at today.

        Results are stored under the dated id, so a later alias move can never mix two models in one row key.
        """
        if self._aliases is None:
            r, _ = self._call("GET", "/v1/models")
            self._aliases = {}
            for m in r.json().get("data", []):
                for alias in m.get("aliases") or []:
                    # several cards can list the same alias; the one whose id differs from the alias wins
                    if alias != m["id"]:
                        self._aliases[alias] = m["id"]
        return self._aliases.get(model, model)

    # ------------------------------------------------------------ chat
    def request_body(self, messages: list[dict], model: str, schema: dict | None = None, *,
                     max_tokens: int = 4000, temperature: float = 0.0) -> dict:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if schema is not None:
            body["response_format"] = json_schema_format(schema)
        return body

    def complete(self, messages: list[dict], model: str, schema: dict | None = None, *,
                 max_tokens: int = 4000, temperature: float = 0.0) -> Result:
        t0 = time.monotonic()
        r, attempts = self._call("POST", CHAT_ENDPOINT, json=self.request_body(
            messages, model, schema, max_tokens=max_tokens, temperature=temperature))
        return parse_completion(r.json(), latency_s=time.monotonic() - t0, attempts=attempts)

    # ------------------------------------------------------------ batch
    def submit_batch(self, requests: list[tuple[str, dict]], model: str, metadata: dict | None = None) -> BatchStatus:
        """Upload a JSONL of {custom_id, body} lines and start one job (one model per job)."""
        if not requests:
            raise ValueError("empty batch")
        ids = [cid for cid, _ in requests]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate custom_id in batch")
        jsonl = "\n".join(json.dumps({"custom_id": cid, "body": body}, ensure_ascii=False) for cid, body in requests)
        r, _ = self._call("POST", "/v1/files", idempotent=False, data={"purpose": "batch"},
                          files={"file": ("batch.jsonl", jsonl.encode(), "application/jsonl")})
        file_id = r.json()["id"]
        r, _ = self._call("POST", "/v1/batch/jobs", idempotent=False, json={
            "input_files": [file_id], "model": model, "endpoint": CHAT_ENDPOINT,
            "metadata": {k: str(v) for k, v in (metadata or {}).items()}})
        return self._status(r.json())

    def batch_status(self, job_id: str) -> BatchStatus:
        r, _ = self._call("GET", f"/v1/batch/jobs/{job_id}")
        return self._status(r.json())

    @staticmethod
    def _status(job: dict) -> BatchStatus:
        return BatchStatus(job_id=job["id"], state=BatchState(job["status"]),
                           total=job.get("total_requests") or 0, succeeded=job.get("succeeded_requests") or 0,
                           failed=job.get("failed_requests") or 0, output_file=job.get("output_file"),
                           error_file=job.get("error_file"), input_files=tuple(job.get("input_files") or ()))

    def batch_lines(self, status: BatchStatus) -> Iterator[BatchLine]:
        """Every line of the output file, then the error file. Unparseable lines become error lines."""
        for file_id in (status.output_file, status.error_file):
            if not file_id:
                continue
            r, _ = self._call("GET", f"/v1/files/{file_id}/content")
            for raw in r.text.splitlines():
                if not raw.strip():
                    continue
                try:
                    yield parse_batch_line(raw)
                except (json.JSONDecodeError, AttributeError) as e:
                    yield BatchLine("", None, None, f"unparseable batch line: {e}")

    def cleanup(self, status: BatchStatus) -> None:
        """Delete uploaded input and result files once collected (retention: keep nothing we don't need)."""
        for file_id in (*status.input_files, status.output_file, status.error_file):
            if file_id:
                with contextlib.suppress(LLMError):   # best effort: a file already gone needs no deleting
                    self._call("DELETE", f"/v1/files/{file_id}")

    # ------------------------------------------------------------ OCR
    def ocr(self, pdf_bytes: bytes, model: str | None = None) -> tuple[str, int]:
        """Text of a PDF as markdown, one block per page. Bytes are sent inline (no server-side URL fetch)."""
        doc = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode()
        r, _ = self._call("POST", "/v1/ocr", json={"model": model or self.models[Task.OCR],
                                                    "document": {"type": "document_url", "document_url": doc}})
        pages = r.json().get("pages") or []
        return "\n\n".join(p.get("markdown", "") for p in pages), len(pages)
