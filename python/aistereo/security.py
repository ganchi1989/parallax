"""Process-local secret capture and child-process environment hygiene."""

from __future__ import annotations

import os

_LLM_ENV_NAME = "AISTEREO_LLM_API_KEY"
# Imported during engine startup, before any media subprocess is launched.
_CAPTURED_LLM_API_KEY = os.environ.pop(_LLM_ENV_NAME, None)


def captured_llm_api_key() -> str | None:
    return _CAPTURED_LLM_API_KEY


def sanitized_subprocess_env() -> dict[str, str]:
    """Return a child environment with application/API credentials removed."""

    environment = dict(os.environ)
    for key in list(environment):
        compact = "".join(character for character in key.lower() if character.isalnum())
        if compact in {
            "aistereollmapikey",
            "openaiapikey",
            "authorization",
        } or any(token in compact for token in ("accesstoken", "refreshtoken")):
            environment.pop(key, None)
    return environment
