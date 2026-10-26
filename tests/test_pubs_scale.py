import pytest

from ugs_warehouse.pubs import scale


def test_tier_of_bins_by_denominator():
    assert scale.tier_of("1:24,000") == "24k"
    assert scale.tier_of("1:62,500") == "24k"       # upper bound of the finest tier
    assert scale.tier_of("1:100,000") == "250k"     # there is NO 100k tier
    assert scale.tier_of("1:250,000") == "250k"
    assert scale.tier_of("1:350,000") == "250k"     # upper bound of the intermediate tier
    assert scale.tier_of("1:500,000") == "500k"
    assert scale.tier_of("1 inch = 1 mile") == "250k"   # denominator -> 63,360; >62,500 -> 250k
    assert scale.tier_of("1 inch = 2000 feet") == "24k"  # denominator -> 24,000
    assert scale.tier_of("") is None
    assert scale.tier_of("not a scale") is None


def test_scale_vocabulary_constants():
    assert scale.DEFAULT_TIER == "24k"
    assert scale.SCALE_LABEL["24k"] == "1:24,000"
    assert set(scale.SCALE_LABEL) == {"24k", "250k", "500k"}


@pytest.mark.parametrize("raw,n", [
    ("1:24,000", 24000), ("1:24000", 24000), ("1 : 24,000", 24000), ("1:24 000", 24000),
    ("1:62.500", 62500), ("1:2,950,000", 2950000), ("500000", 500000), ("100k", 100000),
    ("500k", 500000), ("1 inch = 200 feet", 2400), ("1 inch = 2,000 feet", 24000),
    ("1 inch = 8,000 feet", 96000), ("1 inch = 1 mile", 63360), ("2 inches = 1 mile", 31680),
    ("1 inch = 2 miles", 126720), ("1 inch = 20 miles", 1267200), ("1 inch = 2.500 miles", 158400),
    ("1 inch = 12.500 miles", 792000),
    ("1\" = 20'", 240), ("1\" =  40'", 480), ("1\" =  50'", 600), ("1\" = 3200'", 38400),
    ("1/4 inch = 1 foot", 48),
])
def test_denominator_reads_ratio_and_verbal_scales(raw, n):
    assert scale.denominator(raw) == n


def test_denominator_reads_10000_feet_to_the_inch_as_a_mistyped_ratio():
    assert scale.denominator("1 inch = 24,000 feet") == 24000
    assert scale.denominator("1 inch = 62,500 feet") == 62500


def test_denominator_takes_the_largest_of_several_scales():
    assert scale.denominator("1:24,000 & 1:48,000") == 24000
    assert scale.denominator("1: 48,000 and 1:24,000") == 24000


@pytest.mark.parametrize("raw", ["", "unk", "0.180555556", "0.180", "1:0.5", "0k", "0.25", "2 inch = 250,00 feet",
                                 "1 inch = . feet", "1 inch = 2.000.0 miles"])
def test_denominator_leaves_an_unreadable_scale_none(raw):
    assert scale.denominator(raw) is None
