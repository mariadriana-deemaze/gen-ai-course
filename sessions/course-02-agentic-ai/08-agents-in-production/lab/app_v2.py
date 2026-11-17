"""Session 8 lab, loading the exercise work from the `_v2` starter files.

Run from the repository root:

    uv run --locked streamlit run sessions/course-02-agentic-ai/08-agents-in-production/lab/app_v2.py

Unlike sessions 5 to 7, this lab needs no model patch for Anthropic's
OpenAI-compatible endpoint. Every model call goes through `room.patient()`, which
turns `"openai:<model>"` into an `OpenAIChatModel` (Chat Completions) whose
`AsyncOpenAI` client reads `OPENAI_BASE_URL` from the `.env`. The live recording
(`record.py`, run in its own process) uses the session 7 team, whose `patient()`
does the same.

What this wrapper does change is where the exercises are read from. `room.py`'s
`load_starter(name)` reads `starter/<name>.py` by exact filename, and `a2a.py` and
`traces.py` import it by name, so all three bindings are replaced with a loader
that prefers `starter/<name>_v2.py` when it exists. The guard attribute keeps the
wrapper from stacking, because Streamlit re-executes this script on every rerun.
"""

from __future__ import annotations

from pathlib import Path

import a2a
import room
import traces

if not getattr(room.load_starter, "_v2_patch", False):
    _stock_load_starter = room.load_starter

    def _load_v2_starter(name: str):
        if not room.use_solutions() and (room.STARTER / f"{name}_v2.py").exists():
            return _stock_load_starter(f"{name}_v2")
        return _stock_load_starter(name)

    _load_v2_starter._v2_patch = True
    room.load_starter = a2a.load_starter = traces.load_starter = _load_v2_starter

APP = Path(__file__).resolve().parent / "app.py"
exec(compile(APP.read_text(encoding="utf-8"), str(APP), "exec"), globals())
