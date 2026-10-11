import time

import pytest

from ugs_warehouse.core import gcs

PATH = "_locks/ingest/hazards.hazards_qfaults_current"


def test_lease_is_released_after_the_block():
    with gcs.lease(PATH, ttl_s=60, wait_s=0):
        assert gcs.exists(PATH)
    assert not gcs.exists(PATH)


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
