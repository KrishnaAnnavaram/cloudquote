"""Small LLM interface plus adapters. Model ids are configuration, never account-specific code.

The LLM is only ever used to (a) extract a ``Requirement`` and (b) optionally phrase an
explanation of a quote that was already computed deterministically.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    pass


@runtime_checkable
class LLM(Protocol):
    name: str

    def complete(self, prompt: str, *, system: str = "", json_schema: dict | None = None) -> str: ...


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL from config)
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise LLMError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc


class OpenAICompatibleLLM:
    """OpenAI, or any local server with ``/chat/completions`` (Ollama, vLLM, LM Studio)."""

    def __init__(self, api_key: str, model: str = "", base_url: str = "") -> None:
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        if not api_key and "api.openai.com" in self.base_url:
            raise ValueError("OPENAI_API_KEY is required for the hosted OpenAI endpoint")
        self._key = api_key
        self.model = model or "gpt-4.1-mini"
        self.name = f"openai:{self.model}"

    def complete(self, prompt: str, *, system: str = "", json_schema: dict | None = None) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0}
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        data = _post_json(f"{self.base_url}/chat/completions", payload, headers)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response: {str(data)[:200]}") from exc


class GeminiLLM:
    def __init__(self, api_key: str, model: str = "", base_url: str = "") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for CLOUDQUOTE_LLM_PROVIDER=gemini")
        self._key = api_key
        self.model = model or "gemini-2.5-flash"
        self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self.name = f"gemini:{self.model}"

    def complete(self, prompt: str, *, system: str = "", json_schema: dict | None = None) -> str:
        payload: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0}}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if json_schema is not None:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        data = _post_json(f"{self.base_url}/models/{self.model}:generateContent", payload,
                          {"x-goog-api-key": self._key})
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response: {str(data)[:200]}") from exc


class ScriptedLLM:
    """Deterministic test double: returns queued responses and records each call."""

    def __init__(self, responses: list[str], name: str = "scripted") -> None:
        self._responses = list(responses)
        self.name = name
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, system: str = "", json_schema: dict | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system, "json_schema": json_schema})
        if not self._responses:
            raise LLMError("scripted LLM has no responses left")
        return self._responses.pop(0)


def build_llm(provider: str, *, model: str = "", base_url: str = "", openai_key: str = "",
              gemini_key: str = "") -> LLM | None:
    provider = (provider or "none").lower()
    if provider == "openai":
        return OpenAICompatibleLLM(openai_key, model, base_url)
    if provider == "gemini":
        return GeminiLLM(gemini_key, model, base_url)
    if provider in {"none", ""}:
        return None
    raise ValueError(f"unknown CLOUDQUOTE_LLM_PROVIDER {provider!r} (use none, openai or gemini)")
