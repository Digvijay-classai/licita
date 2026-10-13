import json
from pathlib import Path

import httpx
import pytest

from licita.llm import LLMError, Task, get_provider, model_overrides
from licita.llm.base import BatchState, RetryPolicy, parse_batch_line, parse_completion
from licita.llm.local import LocalProvider
from licita.llm.mistral import MistralProvider, api_key

FIX = Path(__file__).parent / "fixtures" / "mistral_batch"


def completion(content, finish="stop", model="mistral-small-2603"):
    return {"id": "x", "model": model, "choices": [{"index": 0, "finish_reason": finish,
            "message": {"role": "assistant", "content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class Fake:
    """Records requests and replays canned responses per (method, path)."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        key = (request.method, request.url.path)
        out = self.routes[key]
        if isinstance(out, list):
            out = out.pop(0)
        return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)


def provider(routes, attempts=3):
    fake = Fake(routes)
    p = MistralProvider(http=httpx.Client(transport=httpx.MockTransport(fake)),
                        policy=RetryPolicy(attempts=attempts, sleep=lambda s: None))
    return p, fake


# ---------------------------------------------------------------- keys and config
def test_missing_key_explains_three_ways_out():
    with pytest.raises(LLMError) as e:
        api_key()
    assert "MISTRAL_API_KEY" in e.value.problem and "make demo" in e.value.fix and "local" in e.value.fix


def test_blank_key_is_an_error_not_a_silent_fallback(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "   ")
    with pytest.raises(LLMError, match="empty"):
        api_key()


def test_key_is_sent_as_bearer(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-test")
    assert MistralProvider().http.headers["authorization"] == "Bearer sk-test"


def test_model_precedence_env_over_profile_over_default(monkeypatch, tmp_path):
    prof = tmp_path / "p.toml"
    prof.write_text('[buyers.A]\nname="A"\nnif="Q0000001A"\n[models]\nextract = "mistral-medium-latest"\nlint = "x"\n')
    monkeypatch.setenv("LICITA_PROFILE", str(prof))
    monkeypatch.setenv("LICITA_MODEL_LINT", "mistral-large-latest")
    assert model_overrides() == {Task.EXTRACT: "mistral-medium-latest", Task.LINT: "mistral-large-latest"}
    p = get_provider()
    assert p.name == "mistral" and p.model_for(Task.EXTRACT) == "mistral-medium-latest"
    assert p.model_for(Task.CHAT) == "mistral-large-latest" and p.model_for(Task.LINT) == "mistral-large-latest"


def test_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setenv("LICITA_LLM_PROVIDER", "some-other-vendor")
    with pytest.raises(Exception, match="unknown LLM provider"):
        get_provider()


# ---------------------------------------------------------------- chat
def test_complete_sends_json_schema_and_parses_usage():
    p, fake = provider({("POST", "/v1/chat/completions"): completion('{"a": 1}')})
    r = p.complete([{"role": "user", "content": "hi"}], "mistral-small-2603", {"type": "object"}, max_tokens=50)
    body = json.loads(fake.seen[0].content)
    assert body["response_format"]["json_schema"]["strict"] is True and body["max_tokens"] == 50
    assert r.json() == {"a": 1} and (r.input_tokens, r.output_tokens) == (10, 5) and r.attempts == 1


def test_429_is_retried_honouring_retry_after():
    waits = []
    fake = Fake({("POST", "/v1/chat/completions"): [httpx.Response(429, headers={"retry-after": "7"}),
                                                    httpx.Response(503), completion("{}")]})
    p = MistralProvider(http=httpx.Client(transport=httpx.MockTransport(fake)),
                        policy=RetryPolicy(attempts=5, sleep=waits.append))
    r = p.complete([], "m")
    assert r.attempts == 3 and waits[0] == 7.0 and len(waits) == 2


def test_401_says_rotate_the_key():
    p, _ = provider({("POST", "/v1/chat/completions"): httpx.Response(401, text="Unauthorized")})
    with pytest.raises(LLMError, match="rejected the API key"):
        p.complete([], "m")


def test_persistent_5xx_surfaces_after_retries():
    p, fake = provider({("POST", "/v1/chat/completions"): httpx.Response(500, text="boom")}, attempts=2)
    with pytest.raises(LLMError, match="HTTP 500"):
        p.complete([], "m")
    assert len(fake.seen) == 2


def test_chunked_content_is_joined():
    r = parse_completion({"choices": [{"finish_reason": "stop", "message": {"content": [
        {"type": "text", "text": '{"a"'}, {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": ": 1}"}]}}]})
    assert r.json() == {"a": 1}


def test_resolve_model_pins_aliases_to_dated_ids():
    p, fake = provider({("GET", "/v1/models"): {"data": [
        {"id": "mistral-small-2603", "aliases": ["mistral-small-latest"]},
        {"id": "mistral-small-latest", "aliases": ["mistral-small-latest"]}]}})
    assert p.resolve_model("mistral-small-latest") == "mistral-small-2603"
    assert p.resolve_model("mistral-small-2603") == "mistral-small-2603"
    assert len(fake.seen) == 1     # cached


# ---------------------------------------------------------------- batch
def test_submit_batch_uploads_jsonl_then_creates_job():
    p, fake = provider({("POST", "/v1/files"): {"id": "file-in"},
                        ("POST", "/v1/batch/jobs"): {"id": "job-1", "status": "QUEUED", "total_requests": 2,
                                                     "input_files": ["file-in"]}})
    st = p.submit_batch([("tre:1/2024", {"messages": []}), ("tre:2/2024", {"messages": []})], "mistral-small-2603",
                        {"scope": "pliegos"})
    upload, job = fake.seen
    assert b'"custom_id": "tre:1/2024"' in upload.content and b'name="purpose"' in upload.content
    spec = json.loads(job.content)
    assert spec == {"input_files": ["file-in"], "model": "mistral-small-2603", "endpoint": "/v1/chat/completions",
                    "metadata": {"scope": "pliegos"}}
    assert st.job_id == "job-1" and st.state is BatchState.QUEUED and not st.state.terminal


def test_submit_batch_rejects_duplicates_and_empty():
    p, _ = provider({})
    with pytest.raises(ValueError, match="duplicate"):
        p.submit_batch([("a", {}), ("a", {})], "m")
    with pytest.raises(ValueError, match="empty"):
        p.submit_batch([], "m")


def test_batch_lines_reads_output_then_error_file_and_tolerates_junk():
    p, _ = provider({("GET", "/v1/files/out/content"): httpx.Response(200, text=(FIX / "output.jsonl").read_text()),
                     ("GET", "/v1/files/err/content"): httpx.Response(200, text=(FIX / "error.jsonl").read_text())})
    from licita.llm.base import BatchStatus
    lines = list(p.batch_lines(BatchStatus("j", BatchState.SUCCESS, output_file="out", error_file="err")))
    assert [ln.custom_id for ln in lines] == ["tre:3/2024", "tre:1/2024", "tre:2/2024", "tre:1/2024", "tre:999/2024",
                                              "", "tre:5/2024", "tre:4/2024"]
    assert lines[5].error.startswith("unparseable") and lines[7].error == "Internal server error"


def test_error_lines_with_http_status_and_no_error_field():
    ln = parse_batch_line('{"custom_id": "a", "response": {"status_code": 422, "body": {"msg": "bad schema"}}}')
    assert ln.body is None and "bad schema" in ln.error


def test_cleanup_deletes_every_file_and_ignores_already_gone():
    p, fake = provider({("DELETE", "/v1/files/in"): {}, ("DELETE", "/v1/files/out"): httpx.Response(404),
                        ("DELETE", "/v1/files/err"): {}})
    from licita.llm.base import BatchStatus
    p.cleanup(BatchStatus("j", BatchState.SUCCESS, output_file="out", error_file="err", input_files=("in",)))
    assert sorted(r.url.path for r in fake.seen) == ["/v1/files/err", "/v1/files/in", "/v1/files/out"]


def test_ocr_sends_bytes_inline_not_a_url():
    p, fake = provider({("POST", "/v1/ocr"): {"pages": [{"markdown": "# Res 1"}, {"markdown": "texto"}]}})
    text, pages = p.ocr(b"%PDF-1.4 x")
    doc = json.loads(fake.seen[0].content)["document"]
    assert doc["document_url"].startswith("data:application/pdf;base64,") and pages == 2 and "texto" in text


# ---------------------------------------------------------------- local
def test_local_provider_is_openai_compatible_and_explains_a_down_server(monkeypatch):
    monkeypatch.setenv("LICITA_LLM_BASE_URL", "http://gpu:8000/v1")
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=completion('{"ok": true}', model="mistralai/Mistral-Small-3.2-24B-Instruct-2506"))
    lp = LocalProvider(http=httpx.Client(transport=httpx.MockTransport(handler)),
                       policy=RetryPolicy(attempts=1, sleep=lambda s: None))
    assert lp.complete([], lp.model_for(Task.EXTRACT), {"type": "object"}).json() == {"ok": True}
    assert str(seen[0].url) == "http://gpu:8000/v1/chat/completions"
    with pytest.raises(LLMError, match="no local model for ocr"):
        lp.model_for(Task.OCR)

    def down(req):
        raise httpx.ConnectError("refused")
    lp2 = LocalProvider(http=httpx.Client(transport=httpx.MockTransport(down)),
                        policy=RetryPolicy(attempts=1, sleep=lambda s: None))
    with pytest.raises(LLMError) as e:
        lp2.complete([], "m")
    assert "curl http://gpu:8000/v1/models" in e.value.fix


def test_job_creation_is_not_retried_after_a_server_error():
    """A 5xx on job creation may still have created the job: retrying would pay for it twice."""
    p, fake = provider({("POST", "/v1/files"): {"id": "f"},
                        ("POST", "/v1/batch/jobs"): [httpx.Response(502), {"id": "job-2", "status": "QUEUED"}]})
    with pytest.raises(LLMError, match="HTTP 502"):
        p.submit_batch([("a", {})], "m")
    assert [r.url.path for r in fake.seen].count("/v1/batch/jobs") == 1


def test_job_creation_is_retried_on_429():
    p, fake = provider({("POST", "/v1/files"): {"id": "f"},
                        ("POST", "/v1/batch/jobs"): [httpx.Response(429), {"id": "job-3", "status": "QUEUED"}]})
    assert p.submit_batch([("a", {})], "m").job_id == "job-3"
