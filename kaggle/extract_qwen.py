"""Licita: open-model baseline extraction on Kaggle GPUs (T4 x2), Qwen3-14B via Ollama.

Runs inside a Kaggle notebook. Self-contained on purpose: the prompt, the JSON schema and the jobs all
come from the dataset written by `python -m licita.rulings.offline export`, so this file holds no prompt
logic of its own and the baseline sees exactly what the Mistral run sees.

  input   /kaggle/input/<dataset>/manifest.json, jobs.parquet
  output  /kaggle/working/results.jsonl   (one line per ruling, appended as it goes; resumable)

LICITA_THINK=1 turns on Qwen3 reasoning; results are tagged "+think" so both variants can be compared.

One Ollama server per GPU, one request in flight per server. Output is constrained to the schema with
Ollama's `format`; thinking is off. Requests that would not fit the context window are recorded as
`too_long` rather than silently truncated.
"""
from __future__ import annotations

import glob
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import pandas as pd
import requests

MODEL = os.environ.get("LICITA_QWEN_MODEL", "qwen3:14b")
THINK = os.environ.get("LICITA_THINK", "0") == "1"   # Qwen3 reasoning before the JSON answer
TAG = f"ollama/{MODEL}" + ("+think" if THINK else "")  # model id stored with every result
NUM_CTX = 40960            # Qwen3 native window
MAX_OUTPUT = 8000          # same answer budget as the Mistral request
THINK_BUDGET = 8000        # extra room for reasoning tokens when THINK is on
CHARS_PER_TOKEN = 3.0      # conservative for Spanish legal text with the Qwen tokenizer
OUT = Path("/kaggle/working/results.jsonl")
LIMIT = int(os.environ.get("LICITA_LIMIT", "0")) or None   # set e.g. 5 for a smoke test


def find_input() -> Path:
    hits = glob.glob("/kaggle/input/**/manifest.json", recursive=True)
    if not hits:
        raise SystemExit("manifest.json not found: add the licita-rulings dataset to this notebook")
    return Path(hits[0]).parent


def gpu_count() -> int:
    out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=False).stdout
    return max(1, out.count("GPU "))


def start_servers(n: int) -> list[str]:
    # The installer may have left a server on the default port without our GPU pinning; start clean.
    subprocess.run(["pkill", "-f", "ollama serve"], check=False)
    time.sleep(2)
    urls, procs = [], []
    for gpu in range(n):
        port = 11434 + gpu
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(gpu), "OLLAMA_HOST": f"127.0.0.1:{port}",
               "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
               "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_KEEP_ALIVE": "-1"}
        log = open(f"/kaggle/working/ollama_{gpu}.log", "w")  # noqa: SIM115 (lives as long as the server)
        procs.append(subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=subprocess.STDOUT))
        urls.append(f"http://127.0.0.1:{port}")
    for gpu, (url, proc) in enumerate(zip(urls, procs, strict=True)):
        deadline = time.time() + 180          # first start probes the GPUs and can take a while
        while True:
            if proc.poll() is not None:
                tail = Path(f"/kaggle/working/ollama_{gpu}.log").read_text()[-2000:]
                raise SystemExit(f"ollama on GPU {gpu} exited:\n{tail}")
            try:
                requests.get(f"{url}/api/version", timeout=10).raise_for_status()
                print(f"ollama ready on GPU {gpu} ({url})", flush=True)
                break
            except requests.RequestException:
                if time.time() > deadline:
                    tail = Path(f"/kaggle/working/ollama_{gpu}.log").read_text()[-2000:]
                    raise SystemExit(f"ollama at {url} not ready after 180 s:\n{tail}") from None
                time.sleep(3)
    return urls


def pull(url: str) -> None:
    print(f"pulling {MODEL} (one-off, ~9 GB) ...", flush=True)
    r = requests.post(f"{url}/api/pull", json={"model": MODEL, "stream": False}, timeout=3600)
    r.raise_for_status()


def done_ids() -> set[str]:
    """Rulings already processed by this model variant (several variants can share one results file)."""
    if not OUT.exists():
        return set()
    recs = [json.loads(line) for line in OUT.read_text().splitlines() if line.strip()]
    return {r["id"] for r in recs if r["model"] == TAG}


def extract(url: str, system: str, schema: dict, job: dict) -> dict:
    base = {"id": job["id"], "model": TAG}
    room = NUM_CTX - (len(system) + len(job["user"])) / CHARS_PER_TOKEN
    if room < MAX_OUTPUT:
        return {**base, "status": "too_long", "error": f"{len(job['user'])} chars"}
    num_predict = int(min(room, MAX_OUTPUT + (THINK_BUDGET if THINK else 0)))
    t0 = time.time()
    try:
        r = requests.post(f"{url}/api/chat", timeout=1800, json={
            "model": MODEL, "stream": False, "think": THINK, "format": schema,
            "options": {"num_ctx": NUM_CTX, "num_predict": num_predict, "temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": job["user"]}],
        })
        r.raise_for_status()
        body = r.json()
    except requests.RequestException as e:
        return {**base, "status": "errored", "error": f"{type(e).__name__}: {e}"[:300]}
    rec = {**base, "input_tokens": body.get("prompt_eval_count"), "output_tokens": body.get("eval_count"),
           "thinking_chars": len(body.get("message", {}).get("thinking") or ""),
           "seconds": round(time.time() - t0, 1)}
    if body.get("done_reason") == "length":
        return {**rec, "status": "unavailable", "error": "max_tokens"}
    try:
        return {**rec, "status": "succeeded", "data": json.loads(body["message"]["content"])}
    except (json.JSONDecodeError, KeyError) as e:
        return {**rec, "status": "errored", "error": f"bad json: {e}"[:300]}


def main() -> None:
    src = find_input()
    manifest = json.loads((src / "manifest.json").read_text())
    jobs = pd.read_parquet(src / "jobs.parquet").to_dict("records")
    seen = done_ids()
    todo = [j for j in jobs if j["id"] not in seen][:LIMIT]
    print(f"{TAG}: {len(jobs)} jobs, {len(seen)} already done, {len(todo)} to run", flush=True)
    if not todo:
        return
    urls = start_servers(gpu_count())
    pull(urls[0])                      # servers share ~/.ollama/models, so one pull serves all
    lock = threading.Lock()
    stats = {"n": 0, "ok": 0, "t0": time.time()}

    jobs_q: queue.Queue = queue.Queue()
    for job in todo:
        jobs_q.put(job)

    def worker(url: str) -> None:          # one worker per GPU server, so each GPU has one request in flight
        while True:
            try:
                job = jobs_q.get_nowait()
            except queue.Empty:
                return
            rec = extract(url, manifest["system"], manifest["schema"], job)
            with lock, OUT.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                stats["n"] += 1
                stats["ok"] += rec["status"] == "succeeded"
                el = time.time() - stats["t0"]
                eta = el / stats["n"] * (len(todo) - stats["n"]) / 60
                print(f"{stats['n']}/{len(todo)} ok={stats['ok']} last={rec['status']} "
                      f"{rec.get('seconds', '-')}s  eta {eta:.0f} min", flush=True)

    threads = [threading.Thread(target=worker, args=(u,)) for u in urls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"done: {stats}. Download /kaggle/working/results.jsonl")


if __name__ == "__main__":
    main()
