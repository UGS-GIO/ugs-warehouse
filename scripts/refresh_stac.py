"""Standalone STAC catalog regenerator."""
from __future__ import annotations
import os
from ugs_warehouse import sink_stac

def main():
    if not os.environ.get("WAREHOUSE_PUBLIC_BASE_URL"):
        print("Warning: WAREHOUSE_PUBLIC_BASE_URL not set.")
    sink_stac.refresh_catalog()
    print("STAC catalog regenerated.")

if __name__ == "__main__":
    main()
