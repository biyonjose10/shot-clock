"""The invariants that are expensive to get wrong.

Every test here is offline: no Grafana, no Gemini, no network. They cover the
four things that have actually broken this project rather than the things that
are easy to test -- a costing that did not add up, a journal that lost its
place, a quota ledger that lied, and a trace whose children outlasted their
parent.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agent import journal as J
from agent import models as M
from agent.economics import FarmReading, estimate
from sim.tracing import FrameTracer, STAGE_SHARE


# --- economics --------------------------------------------------------------

def _reading(now_rate: float = 300.0, healthy: float = 1400.0) -> FarmReading:
    now = datetime(2026, 9, 25, 6, 0, 0)
    return FarmReading(
        frames_total=161_200,
        frames_rendered=2_600,
        frames_per_hour_now=now_rate,
        frames_per_hour_healthy=healthy,
        nodes_total=200,
        nodes_affected=200,
        shots_at_risk=800,
        shots_total=1_200,
        delivery_date=now + timedelta(hours=141),
        now=now,
    )


def test_costing_components_reconcile():
    """The headline must equal the sum of its parts, or the UI shows nonsense."""
    est = estimate(_reading())
    est.check()  # asserts internally; this is the guarantee the Producer relies on
    assert est.total_exposure > 0
    assert est.slip_hours >= 0


def test_costing_is_deterministic():
    """Same farm state, same numbers. A model asked twice gave $172k then $283k."""
    a, b = estimate(_reading()), estimate(_reading())
    assert a.total_exposure == b.total_exposure
    assert a.slip_hours == b.slip_hours


def test_healthy_farm_is_not_at_risk():
    """A farm running at its healthy rate must not manufacture exposure."""
    est = estimate(_reading(now_rate=1400.0, healthy=1400.0))
    est.check()
    assert est.slip_hours == 0


# --- replay direction -------------------------------------------------------

def _run() -> list[J.Event]:
    """A miniature crew run: five captioned sections with work inside each."""
    events, seq, offset = [], 0, 0.0
    seq += 1
    events.append(J.Event(J.RUN_START, "system", 0.0, {"film": "TEST"}, seq))
    for actor in ("scout", "gaffer", "vision", "producer", "first_ad"):
        seq += 1
        events.append(J.Event(J.CAPTION, actor, offset, {"text": actor}, seq))
        for i in range(3):
            offset += 4.0
            seq += 1
            events.append(J.Event(J.TOOL_CALL, actor, offset, {"tool": f"t{i}"}, seq))
        offset += 4.0
    seq += 1
    events.append(J.Event(J.RUN_END, "system", offset, {}, seq))
    return events


def test_direct_alters_nothing_but_timing():
    """The whole credibility of DEMO MODE rests on this."""
    original = _run()
    directed = J.direct(original)
    assert len(directed) == len(original)
    assert [e.seq for e in directed] == [e.seq for e in original]
    assert [e.kind for e in directed] == [e.kind for e in original]
    assert [e.actor for e in directed] == [e.actor for e in original]
    assert [e.payload for e in directed] == [e.payload for e in original]


def test_direct_is_monotonic_and_hits_its_marks():
    directed = J.direct(_run())
    offsets = [e.offset for e in directed]
    assert offsets == sorted(offsets), "a replay cannot run backwards"

    caps = [e for e in directed if e.kind == J.CAPTION]
    starts = {e.actor: e.offset for e in caps}
    expected = 0.0
    for actor in ("scout", "gaffer", "vision", "producer", "first_ad"):
        assert starts[actor] == pytest.approx(expected, abs=0.5)
        expected += J.DEMO_SECTION_SECONDS[actor]


def test_direct_holds_after_the_verdict():
    """The tech check needs stillness at the end, not a wipe to the next beat."""
    directed = J.direct(_run())
    vision = [e for e in directed if e.actor == "vision"]
    producer_start = next(
        e.offset for e in directed if e.kind == J.CAPTION and e.actor == "producer"
    )
    gap = producer_start - max(e.offset for e in vision)
    assert gap >= J.SECTION_HOLD_SECONDS["vision"] - 0.5


def test_direct_survives_a_journal_with_no_captions():
    events = [J.Event(J.RUN_START, "system", 0.0, {}, 1)]
    assert J.direct(events) == events


# --- resuming an interrupted run --------------------------------------------

def test_resume_continues_sequence_and_timeline(tmp_path):
    path = tmp_path / "run.jsonl"
    first = J.Journal(run_id="t", path=path)
    first.record(J.RUN_START, "system")
    first.record(J.CAPTION, "scout", text="SCOUT")
    last_seq, last_offset = first._seq, first.events[-1].offset

    second = J.Journal(run_id="t", path=path, resume=True)
    assert second._seq == last_seq
    assert len(second.events) == 2

    second.record(J.CAPTION, "gaffer", text="GAFFER")
    e = second.events[-1]
    assert e.seq == last_seq + 1
    assert e.offset >= last_offset + J.RESUME_GAP

    on_disk = J.read(path)
    assert [x.seq for x in on_disk] == [1, 2, 3]


# --- quota accounting -------------------------------------------------------

def test_roles_are_dealt_distinct_models(monkeypatch):
    """Independent fallback once put Scout and Gaffer on the same budget."""
    monkeypatch.setattr(M, "usage_today", lambda: {})
    picked = [M.model_for(r) for r in M.CREW_ROLE_ORDER]
    assert len(set(picked)) == len(picked)


def test_exhausted_models_are_skipped(monkeypatch):
    spent = {M.MODEL_POOL[0]: M.DAILY_BUDGET}
    monkeypatch.setattr(M, "usage_today", lambda: spent)
    picked = [M.model_for(r) for r in M.CREW_ROLE_ORDER]
    assert M.MODEL_POOL[0] not in picked
    assert len(set(picked)) == len(picked)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("429 RESOURCE_EXHAUSTED quota exceeded", True),
        ("503 service unavailable", False),
        ("connection reset by peer", False),
    ],
)
def test_quota_errors_are_recognised(text, expected):
    assert M.is_quota_error(RuntimeError(text)) is expected


# --- simulated traces -------------------------------------------------------

@pytest.mark.parametrize("cache", [0.95, 0.9, 0.7, 0.5, 0.35, 0.1])
def test_span_shares_always_sum_to_one(cache):
    """Children outlasting their parent is impossible in a real trace."""
    shares = FrameTracer._shares(cache)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert set(shares) == set(STAGE_SHARE)
    assert all(v > 0 for v in shares.values())


def test_a_cold_cache_lands_on_texture_fetch():
    healthy = FrameTracer._shares(0.95)["texture_fetch"]
    cold = FrameTracer._shares(0.35)["texture_fetch"]
    assert cold > healthy * 3, "the fault must be visible in the waterfall"


# --- replay cadence ---------------------------------------------------------

def test_replay_yields_every_event_in_order(tmp_path):
    path = tmp_path / "r.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for e in _run():
            fh.write(e.to_json() + "\n")

    async def collect():
        return [e.seq async for e in J.replay(path, speed=1000.0, max_gap=0.0)]

    assert asyncio.run(collect()) == [e.seq for e in _run()]


# --- a server left running --------------------------------------------------

def test_a_spent_production_restarts_instead_of_emptying_the_board():
    """The worst first impression this project can make, and no bug is needed.

    The clock runs at ten production minutes a second over a six-day window,
    so the farm outruns its own delivery date after about fourteen minutes of
    uptime -- and Cloud Run keeps an instance warm for roughly fifteen. A
    visitor arriving at a warm instance was shown 183,050 of 161,200 frames
    rendered: empty board, nothing in flight, and a countdown a day PAST
    delivery.
    """
    import web.server as W

    console = W.Console()
    console.warm_up()
    assert not console.production_is_spent()

    for _ in range(5000):
        if console.production_is_spent():
            break
        console.farm.tick(W.SIM_SECONDS_PER_SECOND * W.TICK_INTERVAL)
    else:
        pytest.fail("the production never finished; this test would not prove anything")

    console.recycle()
    summary = console.farm.summary()
    board = W.build_board(console.farm)
    assert summary.seconds_to_delivery > 0, "a recycled farm needs calendar left"
    assert summary.frames_rendered < summary.frames_total, "and work left to do"
    assert sum(board["in_flight_counts"].values()) > 0, "and shots on the board"


# --- the only control that spends money -------------------------------------

def test_live_run_enforces_its_daily_allowance(tmp_path, monkeypatch):
    from agent import live_run as lr

    monkeypatch.setattr(lr, "STATE", tmp_path / "live_run.json")
    assert lr.remaining() == lr.DAILY_LIMIT
    for _ in range(lr.DAILY_LIMIT):
        lr._spend()
    assert lr.remaining() == 0
    with pytest.raises(lr.Spent):
        lr._spend()
    assert "used up" in (lr.blocked_reason() or "")


def test_live_run_allows_only_one_at_a_time(tmp_path, monkeypatch):
    """A crew run is minutes long; four at once multiply the bill and prove
    nothing the first does not."""
    from agent import live_run as lr
    from agent import readings as R

    monkeypatch.setattr(lr, "STATE", tmp_path / "live_run.json")
    # This test is about the slot, not the farm. Without stubbing, it asserts
    # whatever Grafana happens to say -- which passed locally with a simulator
    # running and failed in CI, where there are no credentials at all.
    monkeypatch.setattr(R, "farm_is_reporting", lambda *a, **k: True)
    lr._farm_checked_at = float("-inf")  # older than any cache window
    assert lr.available()
    assert lr._slot.acquire(blocking=False)
    try:
        assert not lr.available()
        assert "already in progress" in (lr.blocked_reason() or "")
    finally:
        lr._slot.release()
    assert lr.available()


def test_live_run_is_withheld_when_no_farm_is_reporting(tmp_path, monkeypatch):
    """The container never exports telemetry -- its farm drives the shot board
    and nothing else. The crew reads Grafana, fed by a simulator running
    elsewhere. Without one, a live run finds nothing and fails in front of
    whoever pressed the button, so the button must not be there."""
    from agent import live_run as lr
    from agent import readings as R

    monkeypatch.setattr(lr, "STATE", tmp_path / "live_run.json")

    def farm(live):
        # -inf, not 0.0: the cache compares against time.monotonic(), which on
        # a freshly booted runner can be smaller than the cache window, so 0.0
        # still counted as "checked recently" and returned the stale answer.
        lr._farm_checked_at = float("-inf")
        monkeypatch.setattr(R, "farm_is_reporting", lambda *a, **k: live)

    farm(True)
    assert lr.available()
    assert lr.blocked_reason() is None

    farm(False)
    assert not lr.available()
    assert "not reporting" in (lr.blocked_reason() or "")


def test_unreachable_grafana_withholds_the_live_run(tmp_path, monkeypatch):
    """Failing to answer is not the same as answering yes."""
    from agent import live_run as lr
    from agent import readings as R

    monkeypatch.setattr(lr, "STATE", tmp_path / "live_run.json")
    lr._farm_checked_at = float("-inf")

    def boom(*a, **k):
        raise RuntimeError("grafana unreachable")

    monkeypatch.setattr(R, "farm_is_reporting", boom)
    assert not lr.available()
