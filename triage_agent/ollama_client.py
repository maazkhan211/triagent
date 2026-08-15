"""
Thin wrapper around the local Ollama HTTP API used for both chat-based reasoning
(severity classification, root cause analysis) and embeddings (similarity search).
Everything runs against http://localhost:11434 — no cloud API keys needed.
"""

import json
import re

import requests

from triage_agent.config import OLLAMA_HOST, OLLAMA_CHAT_MODEL, OLLAMA_EMBED_MODEL


class OllamaError(RuntimeError):
    pass


def chat_json(system_prompt: str, user_prompt: str, model: str = None, timeout: int = 180) -> dict:
    """
    Calls the local Ollama chat model and parses the response as JSON.
    Uses Ollama's `format: "json"` mode to constrain output, with a fallback
    regex extraction in case the model wraps the JSON in prose anyway.
    """
    model = model or OLLAMA_CHAT_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "format": "json",
        "stream": False,
        # num_gpu=0 forces CPU inference. On this machine Ollama's Vulkan GPU backend
        # segfaults on load (exit status 0xc0000005) for every model tested; CPU
        # inference is slower but reliable. Safe to remove if you're on a machine
        # where GPU offload works.
        "options": {"temperature": 0.1, "num_gpu": 0},
    }
    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(
            f"Could not reach Ollama at {OLLAMA_HOST} (model={model}). "
            f"Is `ollama serve` running and is the model pulled? Original error: {e}"
        ) from e

    content = resp.json().get("message", {}).get("content", "")
    return _extract_json(content)


def _extract_json(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise OllamaError(f"Model did not return valid JSON. Raw content: {content!r}")


def embed(text: str, model: str = None, timeout: int = 180) -> list:
    model = model or OLLAMA_EMBED_MODEL
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": model, "prompt": text, "options": {"num_gpu": 0}},
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(
            f"Could not reach Ollama at {OLLAMA_HOST} (model={model}) for embeddings. "
            f"Is `ollama serve` running and is the model pulled? Original error: {e}"
        ) from e
    data = resp.json()
    embedding = data.get("embedding")
    if not embedding:
        raise OllamaError(f"No embedding returned from Ollama. Raw response: {data!r}")
    return embedding
