"""Publication previews as small WebP (#372): the harvest's map thumbnail and 3D sheet, the cover
job's covers, the item assets that point at them, and the one-time backfill of the PNGs already
published.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

from ugs_warehouse.core import config, gcs
from ugs_warehouse.pubs import harvest, identity, ingest, sink_stac, thumbs, webp, webp_backfill

WEBP = b"RIFF\x00\x00\x00\x00WEBP"
COGS = identity.COG_PREFIX
COVERS = identity.PUB_THUMB_PREFIX
needs_gdal = pytest.mark.skipif(not shutil.which("gdal_translate"), reason="GDAL CLI not installed")


@pytest.fixture
def bucket(monkeypatch) -> dict[str, dict]:
    """An in-memory bucket: object path -> {data, content_type, cache_control}."""
    b: dict[str, dict] = {}

    def put(data, path, content_type=None, cache_control=None):
        b[path] = {"data": data, "content_type": content_type, "cache_control": cache_control}
        return gcs.FileMeta(len(data))

    def upload(local_path, path, *, content_type, cache_control=None):
        with open(local_path, "rb") as f:
            return put(f.read(), path, content_type, cache_control)

    def get_bytes(path):
        if path not in b:
            raise FileNotFoundError(path)
        return b[path]["data"]

    monkeypatch.setattr(gcs, "upload", upload)
    monkeypatch.setattr(gcs, "put_bytes",
                        lambda data, path, *, content_type, cache_control=None, **_: put(
                            data, path, content_type, cache_control))
    monkeypatch.setattr(gcs, "get_bytes", get_bytes)
    monkeypatch.setattr(gcs, "exists", lambda path: path in b)
    monkeypatch.setattr(gcs, "list_paths", lambda prefix: sorted(p for p in b if p.startswith(prefix)))
    monkeypatch.delenv("CLOUD_RUN_TASK_COUNT", raising=False)
    monkeypatch.delenv("CLOUD_RUN_TASK_INDEX", raising=False)
    return b


@pytest.fixture
def encodes(monkeypatch) -> list[dict]:
    """Stand in for the GDAL encode: write WebP-looking bytes and record what was asked for."""
    calls: list[dict] = []

    def encode(src, dst, *, fit=None, quality=None):
        calls.append({"src": src, "out": dst.rsplit("/", 1)[-1], "fit": fit})
        with open(dst, "wb") as f:
            f.write(WEBP)

    monkeypatch.setattr(webp, "encode", encode)
    return calls


def _object(bucket: dict, path: str, data: bytes = b"\x89PNG", content_type: str = "image/png") -> None:
    bucket[path] = {"data": data, "content_type": content_type, "cache_control": gcs.CACHE_IMMUTABLE}


# ---- the encoder, against real GDAL -------------------------------------------------------------

def _raster(path, w: int, h: int, *, ot: str = "Byte", burn=(200, 150, 100, 255)) -> str:
    burns = [a for v in burn for a in ("-burn", str(v))]
    subprocess.run(["gdal_create", "-of", "GTiff", "-outsize", str(w), str(h), "-bands", str(len(burn)),
                    "-ot", ot, *burns, str(path)], check=True, capture_output=True)
    return str(path)


def _png(path, w: int, h: int, color: tuple[int, int, int], *, depth: int = 8, frame: int = 8) -> str:
    """An RGBA PNG shaped like a plate thumbnail: `color` inside, transparent in a `frame`-pixel
    margin. A 16-bit one keeps `color` as is and puts only its alpha at 65535, as OFR-688's does.
    Built by hand so the test controls that exact 16-bit layout."""
    fmt = ">4B" if depth == 8 else ">4H"
    inside = struct.pack(fmt, *color, 255 if depth == 8 else 65535)
    clear = struct.pack(fmt, 0, 0, 0, 0)
    edge = b"\x00" + clear * w
    middle = b"\x00" + clear * frame + inside * (w - 2 * frame) + clear * frame
    rows = edge * frame + middle * (h - 2 * frame) + edge * frame

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return str(path)


def _info(path) -> dict:
    out = subprocess.run(["gdalinfo", "-json", str(path)], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def _pixel(path, x: int, y: int) -> list[float]:
    out = subprocess.run(["gdallocationinfo", "-valonly", str(path), str(x), str(y)],
                         check=True, capture_output=True, text=True)
    return [float(v) for v in out.stdout.split()]


@needs_gdal
@pytest.mark.parametrize(("size", "fitted"), [((700, 350), [512, 256]), ((350, 700), [256, 512]),
                                              ((400, 300), [400, 300])])
def test_a_catalog_thumbnail_fits_in_512_px_and_is_never_upscaled(tmp_path, size, fitted):
    out = tmp_path / "out.webp"
    webp.encode(_raster(tmp_path / "src.tif", *size), str(out), fit=webp.CATALOG_PX)
    head = out.read_bytes()[:12]
    assert (head[:4], head[8:12]) == (b"RIFF", b"WEBP")
    assert _info(out)["size"] == fitted


@needs_gdal
def test_the_3d_sheet_keeps_its_size_and_its_transparency(tmp_path):
    out = tmp_path / "sheet.webp"
    webp.encode(_png(tmp_path / "src.png", 700, 923, (90, 140, 60)), str(out))
    assert _info(out)["size"] == [700, 923]
    assert _pixel(out, 2, 2)[3] == 0
    assert _pixel(out, 350, 460) == pytest.approx([90, 140, 60, 255], abs=5)


@needs_gdal
def test_a_16_bit_plate_keeps_its_colors(tmp_path):
    """The harvest's lzw fallback leaves some plates 16-bit, with their colors in 0-255 and only the
    alpha in 0-65535 (OFR-688, OFR-691). One scale for every band would draw them black."""
    out = tmp_path / "out.webp"
    webp.encode(_png(tmp_path / "src.png", 64, 64, (200, 150, 100), depth=16), str(out), fit=webp.CATALOG_PX)
    assert _pixel(out, 32, 32) == pytest.approx([200, 150, 100, 255], abs=5)
    assert _pixel(out, 2, 2)[3] == 0


@needs_gdal
def test_a_16_bit_plate_warped_a_little_past_255_is_still_read_as_8_bit(tmp_path):
    """A lanczos warp can push 8-bit values stored in 16 bits a little past 255; read as full 16-bit
    range, the plate would come out black."""
    out = tmp_path / "out.webp"
    webp.encode(_png(tmp_path / "src.png", 64, 64, (300, 150, 100), depth=16), str(out))
    assert _pixel(out, 32, 32) == pytest.approx([255, 150, 100, 255], abs=5)


@needs_gdal
def test_a_true_16_bit_plate_keeps_its_hue_when_one_channel_is_dark(tmp_path):
    """The color bands share one scale: a truly 16-bit plate whose blue stays low would otherwise
    read that band as 8-bit and blow it out."""
    out = tmp_path / "out.webp"
    webp.encode(_png(tmp_path / "src.png", 64, 64, (50000, 40000, 600), depth=16), str(out))
    assert _pixel(out, 32, 32) == pytest.approx([195, 156, 2, 255], abs=5)


@needs_gdal
def test_a_gdal_failure_carries_gdals_message(tmp_path):
    with pytest.raises(RuntimeError, match="gdalinfo: .+"):
        webp.encode(str(tmp_path / "missing.png"), str(tmp_path / "out.webp"))


@needs_gdal
def test_a_raster_of_another_type_is_refused_rather_than_guessed(tmp_path):
    with pytest.raises(ValueError, match="Float32"):
        webp.encode(_raster(tmp_path / "src.tif", 16, 16, ot="Float32", burn=(0.5, 0.5, 0.5)),
                    str(tmp_path / "out.webp"))


# ---- the producers ------------------------------------------------------------------------------

def test_the_harvest_publishes_a_small_thumbnail_and_the_3d_sheet_as_webp(bucket, encodes, monkeypatch,
                                                                          tmp_path):
    overviews: list[list[str]] = []

    def gdal_translate(cmd):  # the 700 px overview of the COG that both images are cut from
        overviews.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"II*\x00")

    monkeypatch.setattr(harvest, "run", gdal_translate)
    harvest._report_begin("M-299DM")
    harvest._write_previews(identity.Pub.parse("M-299DM"), str(tmp_path / "M-299DM.cog.tif"), str(tmp_path))
    assert harvest._pub_report["produced"] == {
        "cog": None, "units_parquet": None,
        "thumbnail": f"{COGS}/M-299DM.thumb.webp", "sheet": f"{COGS}/M-299DM.sheet.webp"}
    assert {p: (o["content_type"], o["cache_control"]) for p, o in bucket.items()} == {
        f"{COGS}/M-299DM.thumb.webp": ("image/webp", gcs.CACHE_IMMUTABLE),
        f"{COGS}/M-299DM.sheet.webp": ("image/webp", gcs.CACHE_IMMUTABLE),
    }
    assert {c["out"]: c["fit"] for c in encodes} == {"M-299DM.thumb.webp": webp.CATALOG_PX,
                                                    "M-299DM.sheet.webp": None}
    # Both come from the one small overview, not from reading the full-resolution COG twice.
    [overview] = overviews
    assert overview[overview.index("-outsize") + 1] == str(webp.SHEET_PX)
    assert {c["src"] for c in encodes} == {overview[-1]}


def test_a_pdf_cover_is_published_as_a_small_webp(bucket, encodes, monkeypatch, tmp_path):
    def pdftoppm(cmd):
        with open(cmd[-1] + ".png", "wb") as f:
            f.write(b"\x89PNG")

    monkeypatch.setattr(thumbs, "run", pdftoppm)
    assert thumbs._render_cover("B-10", str(tmp_path / "pub.pdf"), str(tmp_path), thumbs.cover_object("B-10"))
    cover = bucket[f"{COVERS}/B-10.webp"]
    assert (cover["data"], cover["content_type"]) == (WEBP, "image/webp")
    assert [c["fit"] for c in encodes] == [webp.CATALOG_PX]


def test_a_cover_taken_from_the_map_is_its_webp_thumbnail(bucket):
    _object(bucket, f"{COGS}/M-1.thumb.webp", WEBP + b"map", "image/webp")
    assert thumbs._cog_cover("M-1", thumbs.cover_object("M-1"))
    cover = bucket[f"{COVERS}/M-1.webp"]
    assert (cover["data"], cover["content_type"]) == (WEBP + b"map", "image/webp")


def test_a_cover_is_never_copied_from_a_leftover_png_thumbnail(bucket):
    """PNG bytes under a .webp name would be served with the wrong type."""
    _object(bucket, f"{COGS}/M-1.thumb.png")
    assert not thumbs._cog_cover("M-1", thumbs.cover_object("M-1"))
    assert not [p for p in bucket if p.startswith(COVERS)]


# ---- the catalog --------------------------------------------------------------------------------

def test_the_ingest_points_only_at_webps_and_names_pubs_still_on_png(bucket, capsys):
    """A pub whose preview is still only a PNG goes out without it, so the run says which ones."""
    for path in (f"{COGS}/M-1.thumb.webp", f"{COVERS}/M-1.webp", f"{COGS}/M-2.thumb.png", f"{COVERS}/M-2.png",
                 f"{COGS}/M-1.thumb.png", f"{COGS}/M-1.sheet.webp", f"{COGS}/M-1.cog.tif"):
        _object(bucket, path)
    assert ingest._image_ids() == ({"M-1"}, {"M-1"})
    warning = capsys.readouterr().err
    assert "1 pub(s)" in warning and "M-2" in warning


def test_a_pub_item_points_at_its_webp_thumbnail_and_cover(bucket):
    item = sink_stac.build_item({"series_id": "M-1", "pub_name": "Geologic map"}, [],
                                has_thumb=True, has_cover=True)
    thumb, cover = item["assets"]["thumbnail"], item["assets"]["preview"]
    assert (thumb["href"], thumb["type"]) == (config.public_url(f"{COGS}/M-1.thumb.webp"), "image/webp")
    assert (cover["href"], cover["type"]) == (config.public_url(f"{COVERS}/M-1.webp"), "image/webp")


# ---- the one-time backfill ----------------------------------------------------------------------

@pytest.fixture
def published(bucket) -> dict[str, dict]:
    """What's in GCS before the switch: PNG plate thumbnails and covers, one cover already done."""
    _object(bucket, f"{COGS}/M-1.thumb.png")
    _object(bucket, f"{COGS}/M-1.cog.tif", b"II*\x00", config.COG_MIME)
    _object(bucket, f"{COVERS}/B-10.png")
    _object(bucket, f"{COVERS}/B-11.png")
    _object(bucket, f"{COVERS}/B-11.webp", WEBP + b"earlier", "image/webp")
    return bucket


def _backfill(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["webp_backfill", *args])
    return webp_backfill.main()


def test_the_backfill_writes_nothing_without_apply(published, encodes, monkeypatch):
    before = {p: dict(o) for p, o in published.items()}
    assert _backfill(monkeypatch) == 0
    assert published == before
    assert encodes == []


def test_the_backfill_writes_each_missing_webp_next_to_its_png(published, encodes, monkeypatch):
    before = {p: dict(o) for p, o in published.items()}
    assert _backfill(monkeypatch, "--apply") == 0
    new = {p: (o["content_type"], o["cache_control"]) for p, o in published.items() if p not in before}
    assert new == {
        f"{COGS}/M-1.thumb.webp": ("image/webp", gcs.CACHE_IMMUTABLE),
        f"{COGS}/M-1.sheet.webp": ("image/webp", gcs.CACHE_IMMUTABLE),
        f"{COVERS}/B-10.webp": ("image/webp", gcs.CACHE_IMMUTABLE),
    }
    assert {p: o for p, o in published.items() if p in before} == before
    assert {c["out"]: c["fit"] for c in encodes} == {"M-1.thumb.webp": webp.CATALOG_PX, "M-1.sheet.webp": None,
                                                    "B-10.webp": webp.CATALOG_PX}


def test_a_failed_conversion_fails_the_run_after_the_rest_are_written(published, monkeypatch):
    def encode(src, dst, *, fit=None, quality=None):
        if dst.endswith("B-10.webp"):
            raise RuntimeError("gdal_translate: ERROR 1: unsupported")
        with open(dst, "wb") as f:
            f.write(WEBP)

    monkeypatch.setattr(webp, "encode", encode)
    assert _backfill(monkeypatch, "--apply") == 1
    assert f"{COGS}/M-1.thumb.webp" in published
    assert f"{COVERS}/B-10.webp" not in published


def test_the_check_fails_until_every_png_has_its_webp(published, encodes, monkeypatch):
    assert _backfill(monkeypatch, "--check") == 1
    assert _backfill(monkeypatch, "--apply") == 0
    assert _backfill(monkeypatch, "--check") == 0


def test_shards_started_together_split_the_work_without_overlap(published, encodes, monkeypatch):
    """A plate's two images come from one download, so they stay in the same shard."""
    start = {p: dict(o) for p, o in published.items()}
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    shards = []
    for i in range(2):
        published.clear()
        published.update({p: dict(o) for p, o in start.items()})
        encodes.clear()
        monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(i))
        assert _backfill(monkeypatch, "--apply") == 0
        shards.append({c["out"] for c in encodes})
    assert not shards[0] & shards[1]
    assert shards[0] | shards[1] == {"B-10.webp", "M-1.sheet.webp", "M-1.thumb.webp"}
    assert any({"M-1.sheet.webp", "M-1.thumb.webp"} <= shard for shard in shards)


def test_a_shard_that_starts_late_still_gets_its_share(published, encodes, monkeypatch):
    """Shards are cut from every PNG, not from what's left, so one that starts after another has
    finished doesn't find its share already reassigned."""
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    for i in range(2):
        monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(i))
        assert _backfill(monkeypatch, "--apply") == 0
    assert sorted(c["out"] for c in encodes) == ["B-10.webp", "M-1.sheet.webp", "M-1.thumb.webp"]
