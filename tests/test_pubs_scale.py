from ugs_warehouse.pubs import scale


def test_tier_of_bins_by_denominator():
    assert scale.tier_of("1:24,000") == "24k"
    assert scale.tier_of("1:62,500") == "24k"       # upper bound of the finest tier
    assert scale.tier_of("1:100,000") == "250k"     # there is NO 100k tier
    assert scale.tier_of("1:250,000") == "250k"
    assert scale.tier_of("1:350,000") == "250k"     # upper bound of the intermediate tier
    assert scale.tier_of("1:500,000") == "500k"
    assert scale.tier_of("1 inch = 1 mile") == "250k"   # _denominator -> 63,360; >62,500 -> 250k
    assert scale.tier_of("1 inch = 2000 feet") == "24k"  # _denominator -> 24,000
    assert scale.tier_of("") is None
    assert scale.tier_of("not a scale") is None


def test_scale_vocabulary_constants():
    assert scale.DEFAULT_TIER == "24k"
    assert scale.SCALE_LABEL["24k"] == "1:24,000"
    assert set(scale.SCALE_LABEL) == {"24k", "250k", "500k"}
