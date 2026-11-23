"""Configurable Online AI Provider Gateway for Suit and Brains Build 040.

Supports Google Gemini, OpenAI, deterministic ReplayProvider, and OfflineAdvisoryProvider.
API keys come strictly from environment variables. All outputs are ADVISORY (authority=0).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class AIResponse:
    provider: str
    model: str
    text: str
    advisory_only: bool
    authority: int
    tokens_used: int
    estimated_cost_usd: float
    request_digest: str
    from_replay: bool = False
    fallback_used: bool = False


class AIProvider(Protocol):
    name: str
    def generate(self, prompt: str, *, timeout_s: float = 10.0) -> AIResponse: ...


def _req_digest(provider: str, prompt: str) -> str:
    return hashlib.sha256(f"{provider}:{prompt}".encode("utf-8")).hexdigest()


class OfflineAdvisoryProvider:
    name = "offline_advisory"

    def generate(self, prompt: str, *, timeout_s: float = 10.0) -> AIResponse:
        return AIResponse(
            provider=self.name,
            model="deterministic-offline-v1",
            text=f"[OFFLINE_ADVISORY] Parsed request ({len(prompt)} chars); deterministic memory & 49 Brains active.",
            advisory_only=True,
            authority=0,
            tokens_used=0,
            estimated_cost_usd=0.0,
            request_digest=_req_digest(self.name, prompt),
            fallback_used=True,
        )


class ReplayProvider:
    name = "replay_cache"

    def __init__(self, recorded: dict[str, str] | None = None) -> None:
        self.recorded = dict(recorded or {})

    def record(self, prompt: str, text: str) -> None:
        self.recorded[hashlib.sha256(prompt.encode("utf-8")).hexdigest()] = text

    def generate(self, prompt: str, *, timeout_s: float = 10.0) -> AIResponse:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if key not in self.recorded:
            raise KeyError(f"No recorded response for prompt digest {key}")
        return AIResponse(
            provider=self.name,
            model="recorded-replay",
            text=self.recorded[key],
            advisory_only=True,
            authority=0,
            tokens_used=0,
            estimated_cost_usd=0.0,
            request_digest=key,
            from_replay=True,
        )


class GoogleGeminiProvider:
    name = "google_gemini"

    def __init__(self, model: str = "gemini-2.5-flash", api_key_env: str = "GEMINI_API_KEY") -> None:
        self.model = model
        self.api_key_env = api_key_env

    def generate(self, prompt: str, *, timeout_s: float = 10.0) -> AIResponse:
        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Missing environment variable {self.api_key_env}")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={api_key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        tokens = int(data.get("usageMetadata", {}).get("totalTokenCount", 0))
        return AIResponse(
            provider=self.name,
            model=self.model,
            text=text,
            advisory_only=True,
            authority=0,
            tokens_used=tokens,
            estimated_cost_usd=round(tokens * 1e-6, 6),
            request_digest=_req_digest(self.name, prompt),
        )


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key_env: str = "OPENAI_API_KEY") -> None:
        self.model = model
        self.api_key_env = api_key_env

    def generate(self, prompt: str, *, timeout_s: float = 10.0) -> AIResponse:
        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Missing environment variable {self.api_key_env}")
        url = "https://api.openai.com/v1/chat/completions"
        body = json.dumps({"model": self.model, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        tokens = int(data.get("usage", {}).get("total_tokens", 0))
        return AIResponse(
            provider=self.name,
            model=self.model,
            text=text,
            advisory_only=True,
            authority=0,
            tokens_used=tokens,
            estimated_cost_usd=round(tokens * 1.5e-6, 6),
            request_digest=_req_digest(self.name, prompt),
        )


class OnlineAIGateway:
    """Retry/backoff, rate-limit, replay, and offline-fallback gateway (never authoritative)."""

    def __init__(
        self,
        primary: AIProvider | None = None,
        *,
        fallback: AIProvider | None = None,
        max_retries: int = 2,
        backoff_s: float = 0.05,
    ) -> None:
        self.primary = primary or OfflineAdvisoryProvider()
        self.fallback = fallback or OfflineAdvisoryProvider()
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.provenance_log: list[dict[str, Any]] = []

    def request_advisory(self, prompt: str, *, timeout_s: float = 5.0) -> AIResponse:
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.primary.generate(prompt, timeout_s=timeout_s)
                self.provenance_log.append({
                    "provider": resp.provider, "model": resp.model,
                    "request_digest": resp.request_digest, "authority": 0, "fallback": False,
                })
                return resp
            except (HTTPError, URLError, RuntimeError, TimeoutError, KeyError, OSError) as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_s * (attempt + 1))
        resp = self.fallback.generate(prompt, timeout_s=timeout_s)
        self.provenance_log.append({
            "provider": resp.provider, "model": resp.model,
            "request_digest": resp.request_digest, "authority": 0, "fallback": True, "error": str(last_err),
        })
        return resp
