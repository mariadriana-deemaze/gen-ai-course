"""The model a course 02 harness runs on, chosen by the environment.

Every exercise in this course builds a PydanticAI agent, and every agent needs
a model. The runner only knows one way to make one (`openai:$OPENAI_MODEL`),
and `meridian/` must not be edited, so this is the single place where a
harness points somewhere else. Use it from any session:

    from agent_models import build_model

    agent = Agent(build_model(), deps_type=..., output_type=...)

Set in your local `.env`:

    # anything that speaks the OpenAI chat protocol: FreeToken, vLLM, Ollama,
    # LM Studio, OpenRouter
    AGENT_PROVIDER=openai-compatible
    AGENT_BASE_URL=http://127.0.0.1:1919/v1
    AGENT_MODEL=Qwen3.6-35B-A3B-NVFP4
    AGENT_API_KEY=freetoken-local

    # the Anthropic API
    AGENT_PROVIDER=anthropic
    AGENT_MODEL=claude-opus-5
    ANTHROPIC_API_KEY=<your key>

`AGENT_PROVIDER` defaults to `openai-compatible` when `AGENT_BASE_URL` is set.
Any other provider PydanticAI knows by name (`openai`, `google-gla`, `groq`,
`bedrock`, ...) is accepted too and is resolved by PydanticAI itself, which
reads that provider's own key from the environment.

The key is read from `AGENT_API_KEY` first, then from the provider's usual
variable. Note that `meridian.run` still refuses to start without
`OPENAI_API_KEY`, whichever provider you pick, so keep a value there.
"""

from __future__ import annotations

import os
from typing import Callable

from dotenv import load_dotenv
from pydantic_ai.models import Model, infer_model
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.deepseek import deepseek_model_profile
from pydantic_ai.profiles.meta import meta_model_profile
from pydantic_ai.profiles.mistral import mistral_model_profile
from pydantic_ai.profiles.qwen import qwen_model_profile

OPENAI_COMPATIBLE = "openai-compatible"

# A server behind a base URL serves whatever model it was started with, so the
# profile cannot come from the provider: it is matched on the model name
# instead. `OpenAIProvider` would read these names as OpenAI ones and hand a
# Qwen model the wrong JSON schema dialect, which breaks its tool calls.
PROFILES: dict[str, Callable[[str], ModelProfile | None]] = {
    "qwen": qwen_model_profile,
    "deepseek": deepseek_model_profile,
    "mistral": mistral_model_profile,
    "llama": meta_model_profile,
}

USAGE = (
    "Set AGENT_PROVIDER and AGENT_MODEL in your local .env "
    f"(AGENT_PROVIDER={OPENAI_COMPATIBLE} also needs AGENT_BASE_URL). "
    "See sessions/course-02-agentic-ai/agent_models.py."
)


def model_profile_for(model_name: str) -> ModelProfile | None:
    """The profile of a self-served model, or None to let the provider decide."""
    lowered = model_name.lower()
    for marker, profile in PROFILES.items():
        if marker in lowered:
            return profile(model_name)
    return None


def api_key(*names: str) -> str | None:
    """`AGENT_API_KEY` first, then the provider's usual variable."""
    for name in ("AGENT_API_KEY", *names):
        value = os.getenv(name)
        if value:
            return value
    return None


def build_model(
    model_name: str | None = None,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> Model:
    """Build the model from the environment. Arguments override the variables."""
    load_dotenv()
    model_name = model_name or os.getenv("AGENT_MODEL")
    base_url = base_url or os.getenv("AGENT_BASE_URL")
    provider = provider or os.getenv("AGENT_PROVIDER") or (OPENAI_COMPATIBLE if base_url else None)
    if not model_name or not provider:
        raise SystemExit(f"No model configured. {USAGE}")

    if provider == OPENAI_COMPATIBLE:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        if not base_url:
            raise SystemExit(f"AGENT_BASE_URL is missing. {USAGE}")
        return OpenAIChatModel(
            model_name,
            # A local server ignores the key, but the client requires one.
            provider=OpenAIProvider(base_url=base_url, api_key=api_key("OPENAI_API_KEY") or "local"),
            profile=model_profile_for(model_name),
        )

    if provider == "anthropic":
        # Imported here, not at the top: the extra is only needed by whoever
        # picks this provider.
        try:
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider
        except ImportError as missing:
            raise SystemExit(f"The anthropic extra is not installed: {missing}. Run `uv sync`.") from missing

        key = api_key("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY is missing. Add it to your local .env.")
        return AnthropicModel(model_name, provider=AnthropicProvider(api_key=key))

    # Any other provider PydanticAI resolves itself, with its own key.
    return infer_model(f"{provider}:{model_name}")
