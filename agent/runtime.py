"""Run a crew agent and journal everything it does.

Every tool call and every response is written to the run journal as it happens,
which is what makes the war room's trace panel possible and what DEMO MODE
replays later. The journal is also the proof, for a judge or for us, that the
agent genuinely called Grafana rather than describing what it would call.
"""
from __future__ import annotations

import time
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import journal as J
from agent.models import record_use

APP_NAME = "shot-clock"
USER_ID = "supervisor"

#: Tool responses can be enormous (a Loki query returns every matching line).
#: The journal keeps a readable excerpt; the model still sees the whole thing.
MAX_RESULT_CHARS = 1200


def _excerpt(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"... [{len(text) - MAX_RESULT_CHARS} more chars]"


def make_tool_callbacks(jnl: J.Journal, actor: str):
    """Build before/after tool callbacks that journal into ``jnl``.

    ADK signatures:
        before(tool, args, tool_context)
        after(tool, args, tool_context, tool_response)
    Returning None leaves the call untouched, which is what we want -- these
    observe, they do not intercept.
    """
    started: dict[str, float] = {}

    def before(tool, args, tool_context):  # noqa: ANN001 - ADK callback shape
        started[tool.name] = time.monotonic()
        jnl.record(J.TOOL_CALL, actor, tool=tool.name, args=args)
        return None

    def after(tool, args, tool_context, tool_response):  # noqa: ANN001
        elapsed_ms = int((time.monotonic() - started.pop(tool.name, time.monotonic())) * 1000)
        jnl.record(
            J.TOOL_RESULT,
            actor,
            tool=tool.name,
            latency_ms=elapsed_ms,
            result=_excerpt(tool_response),
        )
        return None

    return before, after


def attach_journal(agent: LlmAgent, jnl: J.Journal, actor: str) -> LlmAgent:
    """Wire journaling callbacks onto an already-built agent."""
    before, after = make_tool_callbacks(jnl, actor)
    agent.before_tool_callback = before
    agent.after_tool_callback = after
    return agent


async def run_agent(
    agent: LlmAgent,
    prompt: str,
    jnl: J.Journal,
    actor: str | None = None,
) -> str:
    """Run one agent to completion. Returns its final text."""
    actor = actor or agent.name
    attach_journal(agent, jnl, actor)

    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)

    jnl.record(J.AGENT_START, actor, prompt=prompt)

    # The daily quota meters model requests, not agent runs. Each tool round
    # trip is another request, so a single investigation can be 15 of them --
    # counting once per agent undercounts by an order of magnitude and makes
    # rotation useless.
    model_name = getattr(getattr(agent, "model", None), "model", None)
    turns = 0

    final = ""
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        turns += 1
        # Recorded per turn, not at the end: a run that dies on a 429 has still
        # spent every call it made, and accounting for it only on success left
        # the ledger believing an exhausted model was fresh.
        if model_name:
            record_use(model_name, 1)
        text = _event_text(event)
        if not text:
            continue
        # Intermediate narration is the agent thinking out loud between tool
        # calls; the last one is its answer.
        jnl.record(J.AGENT_THOUGHT, actor, text=text)
        final = text

    return final


def _event_text(event) -> str:  # noqa: ANN001 - ADK event shape
    content = getattr(event, "content", None)
    if content is None or not getattr(content, "parts", None):
        return ""
    return "".join(p.text for p in content.parts if getattr(p, "text", None)).strip()


async def aclose(agent: LlmAgent) -> None:
    """Shut down any MCP toolsets, terminating the mcp-grafana subprocess."""
    for tool in getattr(agent, "tools", []) or []:
        close = getattr(tool, "close", None)
        if close is None:
            continue
        try:
            result = close()
            if hasattr(result, "__await__"):
                await result
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass
