"""The harvest exit code means "the run is broken", not "some publication is unharvestable".

Around 86 publications can never be harvested — their bundle carries no georeferenceable raster.
Failing on any per-pub failure made the job exit 1 on every run: a run that produced 19 new COGs
reported `0/5 tasks were a success`, and the job-failure alert fired every time. An alert that
always fires is an alert nobody reads.
"""
from __future__ import annotations

import pytest

from ugs_warehouse.pubs.harvest import exit_code


def _tally(ok: int = 0, expected: int = 0, attention: int = 0) -> dict[str, int]:
    return {"ok": ok, "expected": expected, "attention": attention}


def test_the_run_that_triggered_this_is_a_success():
    """19 ok, 1433 expected, 34 attention — real numbers from geolmap-harvest-mknwx."""
    assert exit_code(_tally(ok=19, expected=1433, attention=34)) == 0


def test_unharvestable_publications_alone_do_not_fail_the_run():
    assert exit_code(_tally(expected=900, attention=86)) == 0


def test_a_shard_of_only_unharvestable_pubs_is_not_a_failure():
    """Sharded runs can draw a slice where nothing is harvestable — not a broken run."""
    assert exit_code(_tally(expected=900, attention=40)) == 0


def test_a_fully_clean_run_passes():
    assert exit_code(_tally(ok=5, expected=100)) == 0


def test_a_no_op_run_passes():
    """Everything already harvested: nothing attempted, nothing wrong."""
    assert exit_code(_tally(expected=1050)) == 0


def test_one_success_is_enough_to_not_be_systemic():
    assert exit_code(_tally(ok=1, attention=300)) == 0


@pytest.mark.parametrize("tally,expected", [
    (_tally(ok=19, attention=34), 1),
    (_tally(attention=1), 1),
    (_tally(ok=5), 0),
    (_tally(expected=10), 0),
])
def test_strict_restores_fail_on_any_attention(tally, expected):
    assert exit_code(tally, strict=True) == expected
