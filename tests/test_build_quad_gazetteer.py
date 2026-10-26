from __future__ import annotations

import pytest

from scripts.build_quad_gazetteer import rows, rows_100k


def _feature(name: str, code: str, w: float, s: float, e: float, n: float) -> dict:
    ring = [[e, n], [e, s], [w, s], [w, n], [e, n]]
    return {"attributes": {"NAME": name, "OHIO_CODE": code}, "geometry": {"rings": [ring]}}


def test_rows_are_name_and_grid_code_with_usgs_suffix_case():
    # Dromedary Peak as UGRC serves it: Web Mercator drift on the corners, upper-case code.
    got = rows([
        _feature("Dromedary Peak", "40111E6", -111.75078, 40.49996, -111.62577, 40.62496),
        _feature("Camels Back Ridge Sw", "40112A8", -113.0, 40.0, -112.875, 40.125),
    ])
    assert got == [["Camels Back Ridge SW", "40112a8"], ["Dromedary Peak", "40111e6"]]


def test_an_outline_off_its_codes_cell_fails_the_build():
    with pytest.raises(ValueError, match="40111e6"):
        rows([_feature("Wrong", "40111E6", -111.875, 40.5, -111.75, 40.625)])


def test_100k_sheets_are_coded_by_their_se_cell_and_kept_only_in_utah():
    got = rows_100k([
        _feature("Salt Lake City", "", -112.0008, 40.4999, -111.0007, 41.0),
        _feature("Wells", "", -115.0009, 40.9999, -114.0009, 41.4999),        # Nevada, past 114W
        _feature("Evanston", "", -111.0008, 40.9999, -110.0007, 41.5),        # Wyoming notch
    ])
    assert got == [["Salt Lake City", "40111e1"]]


def test_a_100k_outline_off_the_30x60_grid_fails_the_build():
    with pytest.raises(ValueError, match="Odd"):
        rows_100k([_feature("Odd", "", -112.25, 40.5, -111.0, 41.0)])
