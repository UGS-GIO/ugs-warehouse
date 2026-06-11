"""Shared core used by both producers (vector topics, pubs/COG): config, GCS IO, STAC.

Everything in the warehouse reduces to the same end pattern — write cloud-native assets
to GCS (obstore/ADC, no HMAC) and emit a STAC item, all aggregated into one catalog. This
package holds that shared surface so neither producer reinvents STAC, GCS, or the catalog.
"""
from __future__ import annotations
