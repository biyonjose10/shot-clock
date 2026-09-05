"""The shot list for the fictional feature film that the farm is rendering.

VFX work is organised as a hierarchy: a *film* is cut into *sequences* (a
continuous run of story action, named like ``SEQ_0400_ROOFTOP_CHASE``), and a
sequence is cut into *shots* — a single uninterrupted camera take, the atomic
unit of VFX work. A shot is owned by one artist, is rendered by one renderer
(Arnold or Karma here), and is some number of frames long. Everything the
studio promises the client is a promise about shots being final by the
*delivery date*, which is why the whole project is a race against a clock.

This module is the static catalogue: 1200 shots generated from a fixed seed so
that every run of the simulation, and every take of the demo video, is
byte-for-byte identical. Nothing here changes at runtime except ``Shot.status``,
which the farm state machine owns (see ``sim.farm``).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

# Changing this seed reshuffles the entire film. Don't, mid-hackathon.
FILM_SEED = 424242

FILM_TITLE = "THE LAST TRANSMISSION"

DEFAULT_DELIVERY_DATE = "2026-09-30"

TOTAL_SHOTS = 1200

# Frame length bounds for a single shot (24 fps, so 24-240 frames is 1-10 s).
MIN_FRAME_COUNT = 24
MAX_FRAME_COUNT = 240


class ShotStatus(StrEnum):
    """Lifecycle of a shot on the farm.

    PENDING   -- in the backlog, never dispatched
    RENDERING -- assigned to a node, frames coming out
    COMPLETE  -- every frame rendered and accepted
    FAILED    -- exhausted its retries; a human has to look at it
    """

    PENDING = "pending"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class Sequence:
    """A named run of story action, and the shot-id prefix its shots carry."""

    name: str
    code: str
    description: str
    shot_count: int
    # Sequences differ in how heavy they are to render: a crowd scene with
    # volumetrics is a different animal from a locked-off dialogue plate.
    base_complexity: float
    # Fraction of this sequence's shots that go through Arnold (rest = Karma).
    arnold_share: float


@dataclass
class Shot:
    """One camera take. ``status`` is the only mutable field; the farm owns it."""

    shot_id: str
    sequence: str
    artist: str
    renderer: str
    frame_count: int
    complexity: float
    status: ShotStatus = ShotStatus.PENDING

    @property
    def sequence_code(self) -> str:
        """The two-letter prefix of the shot id, e.g. ``RC`` for ``RC_0410``."""
        return self.shot_id.split("_", 1)[0]

    @property
    def complexity_factor(self) -> float:
        """Render-cost multiplier derived from complexity (1.0 = trivial plate).

        Complexity runs 1-10; a 10 costs roughly three times what a 1 costs per
        frame, which is about right for displacement- and volume-heavy work.
        """
        return 1.0 + (self.complexity / 5.0)

    @property
    def memory_gb(self) -> float:
        """Peak resident memory the render is expected to need, in GB.

        Heavy shots blow past 64 GB boxes; this is what makes node memory
        capacity an actual scheduling constraint rather than decoration.
        """
        return round(6.0 + self.complexity * 7.5, 1)


@dataclass(frozen=True)
class Film:
    """The film, its hard delivery date, and its full shot list."""

    title: str
    delivery_date: date
    sequences: tuple[Sequence, ...]
    shots: tuple[Shot, ...]
    _by_id: dict[str, Shot] = field(repr=False, default_factory=dict)

    @property
    def shot_count(self) -> int:
        return len(self.shots)

    @property
    def frame_count(self) -> int:
        """Total frames in the film's VFX work."""
        return sum(shot.frame_count for shot in self.shots)

    def get_shot(self, shot_id: str) -> Shot:
        """Look up one shot by id, e.g. ``RC_0410``. Raises ``KeyError``."""
        return self._by_id[shot_id]

    def shots_in_sequence(self, sequence: str) -> tuple[Shot, ...]:
        """Every shot in a sequence, given its full name or its two-letter code."""
        key = sequence.upper()
        return tuple(
            shot
            for shot in self.shots
            if shot.sequence == key or shot.sequence_code == key
        )

    def shots_by_status(self, status: ShotStatus) -> tuple[Shot, ...]:
        return tuple(shot for shot in self.shots if shot.status is status)

    def sequence(self, name: str) -> Sequence:
        key = name.upper()
        for seq in self.sequences:
            if seq.name == key or seq.code == key:
                return seq
        raise KeyError(name)

    def days_until_delivery(self, today: date | None = None) -> int:
        """Calendar days left before the plates are due. Negative means late."""
        return (self.delivery_date - (today or date.today())).days


# --- source data -----------------------------------------------------------

# Eight sequences whose shot counts sum to exactly TOTAL_SHOTS.
SEQUENCES: tuple[Sequence, ...] = (
    Sequence("SEQ_0100_OPENING_DESCENT", "OD", "Ship breaks cloud layer at dawn", 120, 5.5, 0.70),
    Sequence("SEQ_0200_HARBOR_ARRIVAL", "HA", "Dock crowd, practical water extension", 140, 4.0, 0.45),
    Sequence("SEQ_0300_MARKET_CROWD", "MC", "Full CG crowd, heavy instancing", 180, 7.5, 0.80),
    Sequence("SEQ_0400_ROOFTOP_CHASE", "RC", "Handheld chase, city set extension", 165, 6.5, 0.60),
    Sequence("SEQ_0500_TUNNEL_COLLAPSE", "TC", "Destruction sim and dust volumes", 150, 8.5, 0.85),
    Sequence("SEQ_0600_ORBITAL_DOCK", "OB", "Hard-surface hero ship, long lens", 130, 6.0, 0.35),
    Sequence("SEQ_0700_DESERT_STORM", "DS", "Atmospherics, sand sim, low contrast", 175, 7.0, 0.55),
    Sequence("SEQ_0800_FINAL_ASCENT", "FA", "Hero VFX, full-frame CG environments", 140, 9.0, 0.75),
)

ARTISTS: tuple[str, ...] = (
    "A. Okafor", "B. Lindqvist", "C. Moreau", "D. Ramachandran",
    "E. Vasquez", "F. Nakamura", "G. Petrov", "H. Adeyemi",
    "I. Kowalski", "J. Ferreira", "K. Bergstrom", "L. Haddad",
    "M. Sullivan", "N. Ivanova", "O. Delacroix", "P. Ngata",
    "Q. Zielinski", "R. Castellanos", "S. Bergqvist", "T. Oyelaran",
    "U. Marchetti", "V. Andersen", "W. Sorensen", "X. Rahimi",
)

RENDERERS: tuple[str, ...] = ("arnold", "karma")


def _load_delivery_date() -> date:
    """Read the hard delivery date from the environment, ISO ``YYYY-MM-DD``."""
    raw = os.environ.get("SHOT_CLOCK_DELIVERY_DATE", "").strip()
    if not raw:
        raw = DEFAULT_DELIVERY_DATE
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"SHOT_CLOCK_DELIVERY_DATE must be ISO YYYY-MM-DD, got {raw!r}"
        ) from exc


def _build_shots(sequences: tuple[Sequence, ...], seed: int) -> tuple[Shot, ...]:
    """Generate the whole shot list from one seeded stream.

    The draw order is fixed (sequence by sequence, shot by shot, and the same
    number of draws per shot every time) so the output is stable across runs
    and across machines.
    """
    rng = random.Random(seed)
    shots: list[Shot] = []

    for seq in sequences:
        # Shot numbers step by 10 so a supervisor can insert RC_0415 later,
        # which is exactly how real shot numbering works.
        for index in range(seq.shot_count):
            number = (index + 1) * 10
            shot_id = f"{seq.code}_{number:04d}"

            artist = ARTISTS[rng.randrange(len(ARTISTS))]
            renderer = "arnold" if rng.random() < seq.arnold_share else "karma"
            frame_count = rng.randint(MIN_FRAME_COUNT, MAX_FRAME_COUNT)

            # Complexity clusters around the sequence's baseline, then nudges up
            # for long takes — longer plates tend to carry more moving parts.
            spread = rng.gauss(0.0, 1.15)
            length_bias = (frame_count - MIN_FRAME_COUNT) / (
                MAX_FRAME_COUNT - MIN_FRAME_COUNT
            )
            complexity = seq.base_complexity + spread + length_bias
            complexity = round(min(10.0, max(1.0, complexity)), 1)

            shots.append(
                Shot(
                    shot_id=shot_id,
                    sequence=seq.name,
                    artist=artist,
                    renderer=renderer,
                    frame_count=frame_count,
                    complexity=complexity,
                )
            )

    return tuple(shots)


def build_film(
    delivery_date: date | None = None, seed: int = FILM_SEED
) -> Film:
    """Build a film from scratch. ``FILM`` below is the module-level instance."""
    total = sum(seq.shot_count for seq in SEQUENCES)
    if total != TOTAL_SHOTS:
        raise ValueError(f"sequence shot counts sum to {total}, expected {TOTAL_SHOTS}")

    shots = _build_shots(SEQUENCES, seed)
    return Film(
        title=FILM_TITLE,
        delivery_date=delivery_date or _load_delivery_date(),
        sequences=SEQUENCES,
        shots=shots,
        _by_id={shot.shot_id: shot for shot in shots},
    )


#: The one film the simulation renders. Import this, don't rebuild it.
FILM: Film = build_film()


# --- module-level accessors ------------------------------------------------


def get_shot(shot_id: str) -> Shot:
    """Look up a shot in ``FILM`` by id."""
    return FILM.get_shot(shot_id)


def shots_in_sequence(sequence: str) -> tuple[Shot, ...]:
    """Every shot in a sequence of ``FILM``, by full name or two-letter code."""
    return FILM.shots_in_sequence(sequence)


def sequence_names() -> tuple[str, ...]:
    return tuple(seq.name for seq in FILM.sequences)


def sequence_codes() -> tuple[str, ...]:
    return tuple(seq.code for seq in FILM.sequences)


def all_shot_ids() -> tuple[str, ...]:
    return tuple(shot.shot_id for shot in FILM.shots)


def reset_statuses() -> None:
    """Put every shot back in the backlog. Called when a farm is created."""
    for shot in FILM.shots:
        shot.status = ShotStatus.PENDING
