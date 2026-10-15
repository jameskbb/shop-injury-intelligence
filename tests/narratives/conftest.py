"""Shared fixtures for wii.narratives tests. Synthetic narratives only -- never reads real CPSC
data (that would violate Task 2's blindness protocol for the labeled validation fixture)."""

from __future__ import annotations

import pytest

from wii.narratives.rules import RuleTables, load_rules


@pytest.fixture(scope="session")
def rules() -> RuleTables:
    return load_rules()
