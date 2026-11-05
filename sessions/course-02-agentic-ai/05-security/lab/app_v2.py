"""Session 5 lab, patched for Anthropic's OpenAI-compatible endpoint.

Run from the repository root:

    uv run --locked streamlit run sessions/course-02-agentic-ai/05-security/lab/app_v2.py

The stock `app.py` takes its model from `meridian.agent_runtime.default_model()`,
which returns `"openai:<model>"`. In pydantic-ai 2.x that prefix selects the
Responses API, and Anthropic's OpenAI-compatible endpoint answers `/v1/responses`
with a 404. It serves Chat Completions, which is the `"openai-chat:"` prefix.

This wrapper patches `default_model()` and then runs the unmodified `app.py`, so
the upstream file stays untouched for `git merge upstream/main`. Every model
request in the lab (the incident agent and the guardrail detector) goes through
`app.py`'s `get_model()`, so this single patch covers all of them.
"""

from __future__ import annotations

from pathlib import Path

import meridian.agent_runtime as agent_runtime

_stock_default_model = agent_runtime.default_model


def _chat_completions_model():
    model = _stock_default_model()
    if isinstance(model, str) and model.startswith("openai:"):
        return f"openai-chat:{model.removeprefix('openai:')}"
    return model


agent_runtime.default_model = _chat_completions_model

APP = Path(__file__).resolve().parent / "app.py"
exec(compile(APP.read_text(encoding="utf-8"), str(APP), "exec"), globals())
