import pytest

from ugs_warehouse.core import stac
from ugs_warehouse.pubs import identity, prune


@pytest.mark.parametrize("path,expected", [
    ("ugs-publications/OFR/OFR-1/collection.json", ("ugs-publications", "OFR", "OFR-1", "collection.json")),
    ("ugs-external/BYU/BYU-1/BYU-1_plate/BYU-1_plate.json",
     ("ugs-external", "BYU", "BYU-1", "BYU-1_plate/BYU-1_plate.json")),
    ("ugs-mining-district-files/MD/MD-1/MD-1.json",
     ("ugs-mining-district-files", "MD", "MD-1", "MD-1.json")),
    ("ugs-publications/OFR/catalog.json", ("ugs-publications", "OFR", "", "catalog.json")),
    ("ugs-publications/catalog.json", None),
    ("ugs-publications", None),
    ("ugs-serving-topics/hazards/qfaults/qfaults.json", None),
    ("ugs-publications/OFR/OFR-1/", None),
    ("ugs-publications//OFR-1/collection.json", None),
    ("", None)])
def test_pub_layout_of(path, expected):
    assert stac.pub_layout_of(path) == expected


def test_the_group_list_is_defined_once():
    assert stac.PUB_GROUPS == ("ugs-publications", "ugs-mining-district-files", "ugs-external")
    assert identity.PUB_GROUPS is stac.PUB_GROUPS
    assert (identity.PUBLICATIONS_COLLECTION, identity.MINING_DISTRICT_COLLECTION,
            identity.EXTERNAL_COLLECTION) == stac.PUB_GROUPS
    assert prune.PUB_GROUPS is stac.PUB_GROUPS


def test_the_prune_layout_dir_follows_the_shared_parser():
    prefix = stac.config.STAC_PREFIX
    assert prune._layout_dir(f"{prefix}/ugs-publications/OFR/OFR-1/collection.json") == \
        "ugs-publications/OFR/OFR-1"
    assert prune._layout_dir(f"{prefix}/ugs-publications/OFR/catalog.json") is None
    assert prune._layout_dir("elsewhere/ugs-publications/OFR/OFR-1/collection.json") is None
