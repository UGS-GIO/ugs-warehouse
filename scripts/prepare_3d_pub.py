#!/usr/bin/env python
import os
import sys
import shutil
import zipfile
import subprocess
from pathlib import Path

# Ensure ugs_warehouse is in python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ugs_warehouse.core import config, gcs, stac
from ugs_warehouse.pubs import sink_stac

def main():
    print("=== Starting OFR-778DM 3D Publication Processing Pipeline ===")
    
    # 1. Define paths
    tmp_dir = "/var/folders/3v/d4s5z7_11gv_mdzgcmw3lt_00000gq/T/opencode/ofr-778"
    gdb_path = f"{tmp_dir}/OFR-778DM_Woodland.gdb"
    
    poly_3d_out = f"{tmp_dir}/OFR-778DM_3D_polygons.geojson"
    line_3d_out = f"{tmp_dir}/OFR-778DM_3D_lines.geojson"
    gdb_zip_out = f"{tmp_dir}/OFR-778DM_Woodland_gdb.zip"
    clipped_tif = f"{tmp_dir}/clipped.tif"
    cog_out = f"{tmp_dir}/OFR-778DM.cog.tif"
    cog_thumb = f"{tmp_dir}/OFR-778DM.thumb.png"
    cover_png = f"{tmp_dir}/OFR-778DM.png"
    
    # Check that input files exist
    if not os.path.exists(gdb_path):
        print(f"Error: Geodatabase not found at {gdb_path}", file=sys.stderr)
        return 1
        
    raw_tif = f"{tmp_dir}/OFR-778DM_Woodland_GeoTiff.tif"
    if not os.path.exists(raw_tif):
        print(f"Error: GeoTIFF map sheet not found at {raw_tif}", file=sys.stderr)
        return 1
        
    booklet_pdf = f"{tmp_dir}/OFR-778DM_Woodland_Booklet.pdf"
    if not os.path.exists(booklet_pdf):
        print(f"Error: Booklet PDF not found at {booklet_pdf}", file=sys.stderr)
        return 1

    # 2. Export 3D Geodatabase Layers to 3D GeoJSON (EPSG:4326)
    print("-> Converting 3D geodatabase layers to EPSG:4326 GeoJSON...")
    try:
        import geopandas as gpd
        
        # Polygons
        print("  - Reading CSA_3D_MapUnitPolys...")
        df_poly = gpd.read_file(gdb_path, layer="CSA_3D_MapUnitPolys")
        df_poly_4326 = df_poly.to_crs(epsg=4326)
        df_poly_4326.to_file(poly_3d_out, driver="GeoJSON")
        print(f"  - Successfully wrote 3D polygons to {poly_3d_out}")
        
        # Lines (Contacts and Faults)
        print("  - Reading CSA_3D_ContactsAndFaults...")
        df_line = gpd.read_file(gdb_path, layer="CSA_3D_ContactsAndFaults")
        df_line_4326 = df_line.to_crs(epsg=4326)
        df_line_4326.to_file(line_3d_out, driver="GeoJSON")
        print(f"  - Successfully wrote 3D lines to {line_3d_out}")
        
    except Exception as e:
        print(f"Error during 3D vector extraction: {e}", file=sys.stderr)
        return 1

    # 3. Zip Geodatabase Folder
    print("-> Packaging Geodatabase folder into ZIP archive...")
    try:
        with zipfile.ZipFile(gdb_zip_out, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(gdb_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, os.path.dirname(gdb_path))
                    zipf.write(file_path, arcname)
        print(f"  - Successfully wrote GDB zip to {gdb_zip_out} ({os.path.getsize(gdb_zip_out) / (1024*1024):.2f} MB)")
    except Exception as e:
        print(f"Error zipping Geodatabase: {e}", file=sys.stderr)
        return 1

    # 4. Generate Cloud-Optimized GeoTIFF (COG)
    print("-> Warping GeoTIFF to EPSG:3857...")
    try:
        subprocess.run([
            "gdalwarp", "-t_srs", "EPSG:3857", "-r", "bilinear", "-dstalpha", "-overwrite",
            "-co", "BIGTIFF=YES", "-co", "COMPRESS=DEFLATE", raw_tif, clipped_tif
        ], check=True, capture_output=True, text=True)
        
        print("-> Translating to Cloud-Optimized GeoTIFF (lossless LZW)...")
        from rio_cogeo.cogeo import cog_translate
        from rio_cogeo.profiles import cog_profiles
        
        prof = dict(cog_profiles.get("lzw"))
        prof["bigtiff"] = "IF_SAFER"
        prof["predictor"] = 2
        
        cog_translate(clipped_tif, cog_out, prof, web_optimized=True, quiet=True)
        print(f"  - Successfully created COG at {cog_out} ({os.path.getsize(cog_out) / (1024*1024):.2f} MB)")
        
        # Clean up intermediate clipped file
        if os.path.exists(clipped_tif):
            os.remove(clipped_tif)
            
    except Exception as e:
        print(f"COG conversion failed: {e}", file=sys.stderr)
        return 1

    # 5. Generate COG Thumbnail and Booklet Cover Preview
    print("-> Generating COG thumbnail image...")
    try:
        subprocess.run([
            "gdal_translate", "-of", "PNG", "-outsize", "700", "0", cog_out, cog_thumb
        ], check=True, capture_output=True, text=True)
        print(f"  - Created COG thumbnail at {cog_thumb}")
    except Exception as e:
        print(f"Failed to generate COG thumbnail: {e}", file=sys.stderr)
        return 1

    print("-> Extracting first-page cover preview from Booklet PDF...")
    try:
        prefix_out = f"{tmp_dir}/OFR-778DM"
        subprocess.run([
            "pdftoppm", "-png", "-f", "1", "-l", "1", "-r", "150", booklet_pdf, prefix_out
        ], check=True, capture_output=True, text=True)
        
        # pdftoppm appends "-1.png" or "-01.png" or similar. Find it.
        extracted = f"{prefix_out}-1.png"
        if not os.path.exists(extracted):
            extracted = f"{prefix_out}-01.png"
            
        if os.path.exists(extracted):
            shutil.move(extracted, cover_png)
            print(f"  - Created booklet cover preview at {cover_png}")
        else:
            print("Warning: pdftoppm completed but output preview file was not found.", file=sys.stderr)
    except Exception as e:
        print(f"Failed to extract booklet cover preview: {e}", file=sys.stderr)
        return 1

    # 6. Upload All Assets to Google Cloud Storage
    print("-> Uploading processed files and documents to GCS...")
    try:
        # 3D Vector datasets
        gcs.upload(poly_3d_out, "geolmap/3d/OFR-778DM_3D_polygons.geojson", content_type="application/geo+json", cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(line_3d_out, "geolmap/3d/OFR-778DM_3D_lines.geojson", content_type="application/geo+json", cache_control=gcs.CACHE_MUTABLE)
        
        # COG and thumbnails
        gcs.upload(cog_out, "geolmap/cogs/OFR-778DM.cog.tif", content_type=config.COG_MIME, cache_control=gcs.CACHE_IMMUTABLE)
        gcs.upload(cog_thumb, "geolmap/cogs/OFR-778DM.thumb.png", content_type="image/png", cache_control=gcs.CACHE_IMMUTABLE)
        gcs.upload(cover_png, "pubs/thumbs/OFR-778DM.png", content_type="image/png", cache_control=gcs.CACHE_IMMUTABLE)
        
        # Raw documents
        prefix_gcs_docs = "publications/OFR/OFR-778DM"
        gcs.upload(booklet_pdf, f"{prefix_gcs_docs}/OFR-778DM_Woodland_Booklet.pdf", content_type="application/pdf", cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(f"{tmp_dir}/OFR-778DM_Woodland_Plate1.pdf", f"{prefix_gcs_docs}/OFR-778DM_Woodland_Plate1.pdf", content_type="application/pdf", cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(f"{tmp_dir}/OFR-778DM_Woodland_Plate2.pdf", f"{prefix_gcs_docs}/OFR-778DM_Woodland_Plate2.pdf", content_type="application/pdf", cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(f"{tmp_dir}/OFR-778DM_Woodland_Metadata.pdf", f"{prefix_gcs_docs}/OFR-778DM_Woodland_Metadata.pdf", content_type="application/pdf", cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(gdb_zip_out, f"{prefix_gcs_docs}/OFR-778DM_Woodland_gdb.zip", content_type="application/zip", cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(f"{tmp_dir}/OFR-778DM_Woodland_CSA_3D.mapx", f"{prefix_gcs_docs}/OFR-778DM_Woodland_CSA_3D.mapx", content_type="application/octet-stream", cache_control=gcs.CACHE_MUTABLE)
        
        print("  - All files uploaded successfully to GCS.")
    except Exception as e:
        print(f"Error uploading files to GCS: {e}", file=sys.stderr)
        return 1

    # 7. Programmatically construct and write the STAC Item
    print("-> Constructing STAC Item metadata for OFR-778DM...")
    
    # Metadata extracted from XML
    p = {
        "series_id": "OFR-778DM",
        "pub_name": "Interim Geologic map of the Woodland 7.5' quadrangle, Summit and Wasatch Counties, Utah",
        "pub_author": "Reeher, L.J.",
        "pub_year": "2026",
        "pub_scale": "1:24,000",
        "pub_publisher": "Utah Geological Survey",
        "full_citation": "Reeher, L.J., 2026, Interim Geologic Map of the Woodland Quadrangle, Summit and Wasatch Counties, Utah: Utah Geological Survey Open-File Report 778DM, 14 p., 2 plates, scale 1:24,000, http://doi.org/10.34191/OFR-778DM.",
        "keywords": "geoscientific information, geologic map, geology, contacts, faults, lineaments, dikes, landslide scarps, geologic formations, geologic units, GIS, Kamas, Summit County, Wasatch County, 7.5' Quadrangle, Utah",
        "pub_url": f"{prefix_gcs_docs}/OFR-778DM_Woodland_Booklet.pdf"
    }
    
    attachments = [
        {"pub_url": f"{prefix_gcs_docs}/OFR-778DM_Woodland_Plate1.pdf", "extra_data": "Plate 1 - Map"},
        {"pub_url": f"{prefix_gcs_docs}/OFR-778DM_Woodland_Plate2.pdf", "extra_data": "Plate 2 - Cross Section"},
        {"pub_url": f"{prefix_gcs_docs}/OFR-778DM_Woodland_Metadata.pdf", "extra_data": "Metadata XML (as PDF)"},
        {"pub_url": f"{prefix_gcs_docs}/OFR-778DM_Woodland_gdb.zip", "extra_data": "Geodatabase ZIP"},
        {"pub_url": f"{prefix_gcs_docs}/OFR-778DM_Woodland_CSA_3D.mapx", "extra_data": "ArcGIS Pro 3D MapX Scene"}
    ]
    
    # Precise footprint from corners of GeoTIFF
    bbox = [-111.2673, 40.4711, -111.1085, 40.6361]
    geom = stac.bbox_polygon(bbox)
    
    try:
        # Build standard publication item
        item = sink_stac.build_item(
            p, attachments, geom=geom, bbox=bbox,
            has_cog=True, has_thumb=True, has_cover=True
        )
        
        # Inject the custom 3D assets
        item["assets"]["csa_3d_polygons"] = {
            "href": config.public_url("geolmap/3d/OFR-778DM_3D_polygons.geojson"),
            "type": "application/geo+json",
            "title": "3D Fence Diagram Polygons (Fence Diagram)",
            "roles": ["data", "3d-vector"]
        }
        item["assets"]["csa_3d_lines"] = {
            "href": config.public_url("geolmap/3d/OFR-778DM_3D_lines.geojson"),
            "type": "application/geo+json",
            "title": "3D Fence Diagram Contacts & Faults",
            "roles": ["data", "3d-vector"]
        }
        
        # Attach rendering styles, metadata XML, and write
        stac.attach_renders(item)
        stac.attach_iso(item)
        
        stac_path = stac.write_item(item)
        print(f"  - Successfully wrote STAC item for OFR-778DM to {stac_path}")
        
    except Exception as e:
        print(f"Failed to build/write STAC item: {e}", file=sys.stderr)
        return 1

    # 8. Refresh STAC Catalog
    print("-> Refreshing the entire STAC Catalog (derive-from-truth)...")
    try:
        stac.refresh_catalog()
        print("  - STAC catalog refreshed successfully.")
    except Exception as e:
        print(f"Failed to refresh STAC catalog: {e}", file=sys.stderr)
        return 1

    print("=== Pipeline Completed Successfully! ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
