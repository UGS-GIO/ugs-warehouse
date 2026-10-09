from __future__ import annotations

import pytest

from ugs_warehouse.core.bbox import to_2d_bbox


def test_a_2d_bbox_is_unchanged():
    assert to_2d_bbox([-112.0, 40.0, -111.0, 41.0]) == (-112.0, 40.0, -111.0, 41.0)


def test_a_3d_bbox_drops_z_and_keeps_the_max_corner():
    assert to_2d_bbox([-112.0, 40.0, 1200.0, -111.0, 41.0, 1500.0]) == (-112.0, 40.0, -111.0, 41.0)


def test_any_other_length_is_an_error():
    with pytest.raises(ValueError):
        to_2d_bbox([1.0, 2.0, 3.0])
