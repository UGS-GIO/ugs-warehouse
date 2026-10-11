import time

import pytest

from ugs_warehouse.core import gcs

PATH = "_locks/ingest/hazards.hazards_qfaults_current"


def test_lease_is_released_after_the_block():
    with gcs.lease(PATH, ttl_s=60, wait_s=0):
        assert float(gcs.get_bytes(PATH)) > time.time()
    with gcs.lease(PATH, ttl_s=60, wait_s=0):  # the released lease is free at once
        pass


def test_live_lease_blocks_until_the_wait_runs_out():
    with gcs.lease(PATH, ttl_s=60, wait_s=0):
        with pytest.raises(gcs.LeaseTimeout):
            with gcs.lease(PATH, ttl_s=60, wait_s=0):
                pass
        assert gcs.exists(PATH)


def test_expired_lease_is_taken_over():
    gcs.put_bytes(str(time.time() - 1).encode(), PATH, content_type="text/plain")
    with gcs.lease(PATH, ttl_s=60, wait_s=0):
        assert float(gcs.get_bytes(PATH)) > time.time()


def test_waiter_runs_once_the_holder_releases(monkeypatch):
    gcs.put_bytes(str(time.time() + 60).encode(), PATH, content_type="text/plain")
    monkeypatch.setattr(gcs.time, "sleep", lambda _s: gcs.delete(PATH))
    with gcs.lease(PATH, ttl_s=60, wait_s=60):
        assert gcs.exists(PATH)


def test_a_taken_over_lease_is_left_for_its_new_holder():
    with gcs.lease(PATH, ttl_s=60, wait_s=0):
        gcs.put_bytes(b"9999999999", PATH, content_type="text/plain")
    assert gcs.get_bytes(PATH) == b"9999999999"


def test_a_corrupt_lease_names_itself():
    gcs.put_bytes(b"garbage", PATH, content_type="text/plain")
    with pytest.raises(ValueError, match=PATH):
        with gcs.lease(PATH, ttl_s=60, wait_s=0):
            pass
