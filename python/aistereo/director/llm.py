"""Optional, vocabulary-bounded OpenAI Responses preset provider.

The API key is accepted only from an explicit in-process secret or the
``AISTEREO_LLM_API_KEY`` environment variable. It is never part of a model,
request parameter, artifact, log, or error detail.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError

from ..config import LLMConfig
from ..errors import AIStereoError
from ..models import LLMPresetRecommendation, ShotFeatures, StereoPreset
from ..security import captured_llm_api_key
from .rules import DirectionDecision, RuleBasedDirector

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]
OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_RESPONSE_BYTES = 256 * 1024


class PresetProvider(Protocol):
    def recommend(self, features: ShotFeatures) -> LLMPresetRecommendation: ...


class LLMProviderError(AIStereoError):
    code = "llm_provider_error"
    retryable = True


class LLMAuthenticationError(LLMProviderError):
    code = "llm_authentication_failed"
    retryable = False


class LLMRateLimitError(LLMProviderError):
    code = "llm_rate_limited"


class LLMServiceError(LLMProviderError):
    code = "llm_service_unavailable"


def _default_transport(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise LLMAuthenticationError(
                "The preset service rejected the configured credential"
            ) from exc
        if exc.code == 429:
            raise LLMRateLimitError("The preset service rate limit was reached") from exc
        if 500 <= exc.code <= 599:
            raise LLMServiceError("The preset service is temporarily unavailable") from exc
        raise LLMProviderError("The preset service rejected the request", retryable=False) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Never include request headers or exception representations that may
        # contain them in a user-facing detail object.
        raise LLMProviderError("The preset service could not be reached") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise LLMProviderError(
            "The preset service response exceeded the safety limit", retryable=False
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LLMProviderError("The preset service returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMProviderError("The preset service returned an unexpected response")
    return parsed


def _extract_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    text = block.get("text")
                    if isinstance(text, str):
                        return text
    raise LLMProviderError("The preset service returned no structured recommendation")


class OpenAIResponsesPresetProvider:
    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config or LLMConfig()
        self._api_key = api_key if api_key is not None else captured_llm_api_key()
        self._transport = transport or _default_transport
        base_url = self.config.base_url.rstrip("/")
        if (
            base_url != OFFICIAL_OPENAI_BASE_URL
            and os.environ.get("AISTEREO_ALLOW_CUSTOM_LLM_BASE_URL") != "1"
        ):
            raise LLMProviderError(
                "Custom preset service endpoints are disabled in production", retryable=False
            )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "enabled": self.config.enabled,
            "provider": self.config.provider,
            "model": self.config.model,
        }

    def _body(self, features: ShotFeatures) -> dict[str, Any]:
        summary = features.model_dump(mode="json")
        schema = LLMPresetRecommendation.model_json_schema()
        return {
            "model": self.config.model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Choose one bounded stereo preset from the schema. "
                                "Do not propose numerical stereo or disparity parameters. "
                                "Prefer comfort under motion or uncertainty."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {"shot_features": summary}, separators=(",", ":"), sort_keys=True
                            ),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "stereo_preset_recommendation",
                    "strict": True,
                    "schema": schema,
                }
            },
            "store": False,
            "max_output_tokens": 300,
        }

    def recommend(self, features: ShotFeatures) -> LLMPresetRecommendation:
        if not self._api_key:
            raise LLMProviderError(
                "No app-specific preset service key is configured", retryable=False
            )
        url = self.config.base_url.rstrip("/") + "/responses"
        response = self._transport(
            url,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ai-stereo-director/0.1",
            },
            self._body(features),
            self.config.timeout_seconds,
        )
        try:
            raw = json.loads(_extract_output_text(response))
            return LLMPresetRecommendation.model_validate(raw)
        except (json.JSONDecodeError, PydanticValidationError, TypeError) as exc:
            raise LLMProviderError("The preset recommendation failed schema validation") from exc


class LLMDirector:
    """Converts a provider's vocabulary-only response to a director decision."""

    name = "llm-assisted-v1"

    def __init__(
        self,
        provider: PresetProvider,
        *,
        fallback: str = "rules",
        rules: RuleBasedDirector | None = None,
        minimum_confidence: float = 0.55,
    ) -> None:
        self.provider = provider
        self.fallback = fallback
        self.rules = rules or RuleBasedDirector()
        self.minimum_confidence = min(1.0, max(0.0, minimum_confidence))
        self.last_error: str | None = None

    def select(self, features: ShotFeatures) -> DirectionDecision:
        try:
            recommendation = self.provider.recommend(features)
        except Exception as exc:
            # Any provider/network/schema error is contained. The exception text
            # is intentionally not persisted because third-party transports may
            # expose implementation details.
            self.last_error = type(exc).__name__
            if self.fallback == "neutral":
                return DirectionDecision(
                    StereoPreset.NEUTRAL, 0.5, "preset provider unavailable; neutral fallback"
                )
            decision = self.rules.select(features)
            return DirectionDecision(
                decision.preset,
                decision.confidence,
                "preset provider unavailable; deterministic rules fallback",
            )
        self.last_error = None
        if recommendation.confidence < self.minimum_confidence:
            return DirectionDecision(
                StereoPreset.NEUTRAL,
                recommendation.confidence,
                "assistant confidence was below the safe threshold; neutral fallback",
            )
        return DirectionDecision(
            recommendation.preset,
            recommendation.confidence,
            recommendation.reason,
        )
