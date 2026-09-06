"""Run a crew agent and journal everything it does.

Every tool call and every response is written to the run journal as it happens,
which is what makes the war room's trace panel possible and what DEMO MODE
replays later. The journal is also the proof, for a judge or for us, that the
agent genuinely called Grafana rather than describing what it would call.
"""
from __future__ import annotations

import json
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

#: Hard ceiling on model turns per agent.
#:
#: The free quota is 20 requests per day per model, and an agent that wanders
#: spends the whole of one: a Scout run once made 25 tool calls, ten of them
#: re-reads of metrics it had already queried, exhausted its model and never
#: reached its own briefing -- so nothing downstream ran and nothing was
#: checkpointed. A cap turns "the day is gone" into "hand over what you have",
#: which is both recoverable and better television: the trace panel shows a
#: focused investigation instead of a dozen duplicate queries.
#:
#: Counted in ADK events, not model requests: a tool call logs both the call
#: and its result, so an agent doing N queries spends about 2N events and then
#: one more to write its answer.
#:
#: That arithmetic is why this is a safety net rather than a working limit. Set
#: to 20 it fired at exactly ten queries -- the turn before Scout summarised --
#: so the stage handed the Gaffer this cap notice instead of a briefing. An
#: agent only writes its answer when it stops calling tools, so a cap that
#: binds in normal operation always severs the conclusion. With re-reads no
#: longer wasted, Scout needs roughly 22 events; 30 leaves it room to finish
#: and still catches the runaway that started this (52 events, quota gone).
MAX_TURNS_PER_AGENT = 30


#: Grafana writes the war room renders as their own beat, mapped to the
#: `resource` label the UI prints. The trace row alone is not enough: the
#: report panel builds its incident and annotation rows from WRITE_BACK, and
#: its cost tiles from COSTING. Only the scripted stand-in ever emitted those,
#: so a genuine crew run produced a journal the UI could not fully draw --
#: the Producer priced the delay and the First AD opened a real incident, and
#: neither reached the screen.
WRITE_TOOLS: dict[str, str] = {
    "create_incident": "incident",
    "add_activity_to_incident": "incident activity",
    "create_annotation": "annotation",
    "update_dashboard": "dashboard",
    "create_snapshot": "snapshot",
    "generate_deeplink": "deeplink",
}

#: The deterministic costing tool. Its result is the Producer's whole output.
COSTING_TOOL = "price_delivery_risk"


def _mcp_payload(response: Any) -> dict | str | None:
    """Best effort at the useful part of an MCP tool response.

    Returns a parsed object when the tool answered with JSON, the raw string
    when it answered with text (``generate_deeplink`` returns a bare URL), or
    None. Never raises: a malformed response must not take down a run.
    """
    try:
        content = response.get("content") if isinstance(response, dict) else None
        if not content:
            return response if isinstance(response, dict) else None
        text = content[0].get("text") if isinstance(content[0], dict) else None
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return json.loads(stripped)
        return stripped
    except Exception:  # noqa: BLE001 - observation must never break the run
        return None


def _write_back_payload(tool_name: str, args: dict, response: Any) -> dict:
    """Describe one Grafana write in the shape the report panel reads."""
    out: dict[str, Any] = {
        "resource": WRITE_TOOLS[tool_name],
        "target": WRITE_TOOLS[tool_name],
        "title": (args or {}).get("title") or (args or {}).get("text") or "",
    }
    parsed = _mcp_payload(response)
    if isinstance(parsed, str) and parsed.startswith("http"):
        out["url"] = parsed
    elif isinstance(parsed, dict):
        ident = (
            parsed.get("incidentID")
            or parsed.get("activityItemID")
            or (parsed.get("Payload") or {}).get("id")
            or parsed.get("id")
        )
        if ident is not None:
            out["id"] = str(ident)
        for key in ("url", "URL", "snapshotUrl"):
            if isinstance(parsed.get(key), str):
                out["url"] = parsed[key]
                break
    return out


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

        # Some results are beats in their own right, not just trace lines.
        try:
            if tool.name == COSTING_TOOL and isinstance(tool_response, dict):
                read = tool_response.get("read_from_grafana") or {}
                jnl.record(
                    J.COSTING,
                    actor,
                    headline=tool_response.get("headline", ""),
                    lines=tool_response.get("tiles") or [],
                    shots_at_risk=read.get("shots_at_risk"),
                    delay_hours=tool_response.get("slip_hours"),
                    cost_usd=tool_response.get("total_exposure"),
                )
            elif tool.name in WRITE_TOOLS:
                jnl.record(
                    J.WRITE_BACK,
                    actor,
                    **_write_back_payload(tool.name, args, tool_response),
                )
        except Exception:  # noqa: BLE001 - never let observation break a run
            pass
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
        if text:
            # Intermediate narration is the agent thinking out loud between
            # tool calls; the last one is its answer.
            jnl.record(J.AGENT_THOUGHT, actor, text=text)
            final = text
        if turns >= MAX_TURNS_PER_AGENT:
            # Journalled, but deliberately NOT assigned to `final`: the next
            # agent is handed this stage's conclusions, and overwriting them
            # with a notice about the budget is how the Gaffer once received
            # "Reached the 20-turn budget" as the Scout's entire report.
            jnl.record(
                J.AGENT_THOUGHT,
                actor,
                text=(
                    f"Reached the {MAX_TURNS_PER_AGENT}-turn budget. Handing "
                    f"over on what has been read so far."
                ),
            )
            break

    if not final:
        final = (
            f"{actor} stopped at its turn budget before writing a summary. "
            f"Its tool calls and their results are in the journal above."
        )

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
