"""Seed fixtures package — the catalog of demo loans + the dataclasses
that describe them.

The ``seed.py`` runner imports ``LOANS_TO_SEED`` (and the SeedX
dataclasses for type hints in its helpers). Each fixture lives in a
domain module so adding a new asset type / borrower class is a
focused edit rather than a 1500-line diff:

  - ``_types``            — SeedDoc, SeedParty, SeedLoan dataclasses
  - ``commercial_phase1`` — first 5 CRE deals (bridge + permanent baseline)
  - ``commercial_phase2`` — hospitality, construction, mixed-use,
                            self-storage, distressed retail
  - ``personal``          — KAYA (consumer personal loan)

Adding a fixture: write it in the relevant domain module, then
append to ``LOANS_TO_SEED`` below. The order here drives the demo
pipeline ordering (left to right in the pipeline view).
"""

from __future__ import annotations

from scripts.seed_fixtures._types import SeedDoc, SeedLoan, SeedParty
from scripts.seed_fixtures.commercial_phase1 import (
    ATLAS,
    BAYLINE,
    CEDAR,
    HALCYON,
    NORTHGATE,
)
from scripts.seed_fixtures.commercial_phase2 import (
    HIGHLAND,
    LAKESIDE,
    MERIDIAN,
    PORTSIDE,
    SUNRISE,
)
from scripts.seed_fixtures.personal import KAYA

LOANS_TO_SEED: list[SeedLoan] = [
    ATLAS,
    CEDAR,
    BAYLINE,
    NORTHGATE,
    HALCYON,
    MERIDIAN,
    PORTSIDE,
    LAKESIDE,
    SUNRISE,
    HIGHLAND,
    KAYA,
]

__all__ = [
    "ATLAS",
    "BAYLINE",
    "CEDAR",
    "HALCYON",
    "HIGHLAND",
    "KAYA",
    "LAKESIDE",
    "LOANS_TO_SEED",
    "MERIDIAN",
    "NORTHGATE",
    "PORTSIDE",
    "SUNRISE",
    "SeedDoc",
    "SeedLoan",
    "SeedParty",
]
