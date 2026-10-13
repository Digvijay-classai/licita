"""Provider-neutral types: one completion result, one batch status, two small protocols.

Every provider speaks the chat-completions shape (Mistral's API and OpenAI-compatible servers such as vLLM
or Ollama), so parsing lives here once.
"""
from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

import httpx

from licita.errors import LicitaError


class Task(StrEnum):
    EXTRACT = "extract"        # structured extraction from rulings (high volume, batchable)
    LINT = "lint"              # clause review against tribunal doctrine
    MEMO = "memo"              # memo narrative
    CHAT = "chat"              # "Preguntar" agent
    OCR = "ocr"                # scanned PDF to text


class LLMError(LicitaError):
    pass


@dataclass(frozen=True)
class Result:
    text: str
    finish_reason: str | None
    model: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float | None = None
    attempts: int = 1

    def json(self) -> dict:
        """The completion parsed as JSON. Raises ValueError (not a provider error) when it is not JSON."""
        return json.loads(self.text)


def message_text(content: object) -> str:
    """Chat `message.content` is a string, or a list of chunks for some models; keep only text chunks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type", "text") == "text")
    return ""


def parse_completion(body: dict, latency_s: float | None = None, attempts: int = 1) -> Result:
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("completion has no choices")
    choice = choices[0]
    usage = body.get("usage") or {}
    return Result(text=message_text((choice.get("message") or {}).get("content")),
                  finish_reason=choice.get("finish_reason"), model=body.get("model"),
                  input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                  latency_s=latency_s, attempts=attempts)


def json_schema_format(schema: dict, name: str = "output") -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}


# -------------------------------------------------------------------- batches
class BatchState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT_EXCEEDED = "TIMEOUT_EXCEEDED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in (BatchState.SUCCESS, BatchState.FAILED, BatchState.TIMEOUT_EXCEEDED, BatchState.CANCELLED)


@dataclass(frozen=True)
class BatchStatus:
    job_id: str
    state: BatchState
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    output_file: str | None = None
    error_file: str | None = None
    input_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchLine:
    """One line of a batch output or error file. Exactly one of `body` / `error` is set."""
    custom_id: str
    status_code: int | None
    body: dict | None
    error: str | None


def parse_batch_line(line: str) -> BatchLine:
    rec = json.loads(line)
    resp = rec.get("response") or {}
    body = resp.get("body") if isinstance(resp, dict) else None
    code = resp.get("status_code") if isinstance(resp, dict) else None
    err = rec.get("error")
    if err is None and (code is not None and code >= 400):
        err = json.dumps(body)[:300] if body else f"HTTP {code}"
    if err is not None:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return BatchLine(str(rec.get("custom_id")), code, None, (msg or "error")[:300])
    return BatchLine(str(rec.get("custom_id")), code, body, None)


# -------------------------------------------------------------------- protocols
@runtime_checkable
class Provider(Protocol):
    name: str

    def model_for(self, task: Task) -> str: ...
    def resolve_model(self, model: str) -> str: ...
    def complete(self, messages: list[dict], model: str, schema: dict | None = None, *,
                 max_tokens: int = 4000, temperature: float = 0.0) -> Result: ...


@runtime_checkable
class BatchCapable(Protocol):
    def submit_batch(self, requests: list[tuple[str, dict]], model: str, metadata: dict | None = None) -> BatchStatus: ...
    def batch_status(self, job_id: str) -> BatchStatus: ...
    def batch_lines(self, status: BatchStatus) -> Iterator[BatchLine]: ...
    def cleanup(self, status: BatchStatus) -> None: ...


# -------------------------------------------------------------------- HTTP with retries
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class RetryPolicy:
    attempts: int = 5
    base_s: float = 1.0
    max_s: float = 60.0
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.max_s)
            except ValueError:
                pass
        return min(self.max_s, self.base_s * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)


def send(http: httpx.Client, method: str, url: str, policy: RetryPolicy, where: str, *,
         idempotent: bool = True, **kwargs) -> tuple[httpx.Response, int]:
    """One request with backoff. Returns (response, attempts used).

    Idempotent calls retry on 429, 5xx and transport errors. Calls that create something billable (a file
    upload, a batch job) retry only on 429, which guarantees nothing was created: after a 5xx or a dropped
    connection the job may exist, and a blind retry would start and pay for a second one.
    """
    retry_status = RETRY_STATUS if idempotent else {429}
    for attempt in range(1, policy.attempts + 1):
        try:
            r = http.request(method, url, **kwargs)
        except httpx.TransportError as e:
            if attempt == policy.attempts or not idempotent:
                raise LLMError(f"cannot reach {where}", f"{type(e).__name__}: {e}",
                               "check the network or the server URL, then rerun (finished work is kept)") from e
            policy.sleep(policy.delay(attempt, None))
            continue
        if r.status_code in retry_status and attempt < policy.attempts:
            policy.sleep(policy.delay(attempt, r.headers.get("retry-after")))
            continue
        return r, attempt
    raise AssertionError("unreachable")
