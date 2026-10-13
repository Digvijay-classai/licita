"""Model access. Mistral is the only hosted provider; `local` serves Mistral open weights on your own hardware.

  LICITA_LLM_PROVIDER   mistral (default) | local
  LICITA_MODEL_<TASK>   per-task model override, e.g. LICITA_MODEL_EXTRACT=mistral-medium-latest

Model precedence: environment variable > profile [models] > provider default. `licita config` prints the result.
"""
from __future__ import annotations

import os

from licita.errors import LicitaError
from licita.llm.base import (
    BatchCapable,
    BatchLine,
    BatchState,
    BatchStatus,
    LLMError,
    Provider,
    Result,
    Task,
    json_schema_format,
)

__all__ = ["BatchCapable", "BatchLine", "BatchState", "BatchStatus", "LLMError", "Provider", "Result", "Task",
           "get_provider", "json_schema_format", "model_overrides", "provider_name"]

PROVIDERS = ("mistral", "local")


def provider_name() -> str:
    name = (os.environ.get("LICITA_LLM_PROVIDER") or "mistral").strip().lower()
    if name not in PROVIDERS:
        raise LicitaError(f"unknown LLM provider {name!r}", "LICITA_LLM_PROVIDER accepts mistral or local",
                          "unset it (Mistral) or set it to local for a self-hosted OpenAI-compatible server")
    return name


def model_overrides() -> dict[Task, str]:
    from licita.profile import load_profile
    out: dict[Task, str] = {}
    for task, model in load_profile().models.items():
        try:
            out[Task(task)] = model
        except ValueError as e:
            raise LicitaError(f"unknown task {task!r} in profile [models]",
                              f"tasks are {', '.join(t.value for t in Task)}", "rename the key") from e
    for task in Task:
        if env := os.environ.get(f"LICITA_MODEL_{task.name}"):
            out[task] = env
    return out


def get_provider(name: str | None = None) -> Provider:
    name = name or provider_name()
    if name == "mistral":
        from licita.llm.mistral import MistralProvider
        return MistralProvider(models=model_overrides())
    from licita.llm.local import LocalProvider
    return LocalProvider(models=model_overrides())
