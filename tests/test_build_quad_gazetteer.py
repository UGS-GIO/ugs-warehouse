from __future__ import annotations

import pytest

from scripts.build_quad_gazetteer import rows


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
