# Changelog

## [1.1.0](https://github.com/UGS-GIO/ugs-warehouse/compare/v1.0.0...v1.1.0) (2026-10-04)


### Features

* **featureserv:** replace duckdb_featureserv with pygeoapi ([#502](https://github.com/UGS-GIO/ugs-warehouse/issues/502)) ([32be130](https://github.com/UGS-GIO/ugs-warehouse/commit/32be1308d1f3d4ba90145db5d289bbd42b53a7ef))
* **pubs:** extract map layers and prune stale items in the full refresh ([#490](https://github.com/UGS-GIO/ugs-warehouse/issues/490)) ([44cf203](https://github.com/UGS-GIO/ugs-warehouse/commit/44cf2034c151c7490cf1591c3ab1d509b9e8c63c))
* **pubs:** read COG asset bands and projection from the header with rio-stac ([#482](https://github.com/UGS-GIO/ugs-warehouse/issues/482)) ([ac691c7](https://github.com/UGS-GIO/ugs-warehouse/commit/ac691c7f33f220ddbdd00cd9e03d1619c8ee03e9))
* **pubs:** write pubs GeoParquet with geoparquet-io (Hilbert sort, bbox covering) ([#481](https://github.com/UGS-GIO/ugs-warehouse/issues/481)) ([69e4806](https://github.com/UGS-GIO/ugs-warehouse/commit/69e48069dcdfc7627b97e61dfe87260a2c35b32f))
* **stac:** write items.parquet with rustac for every collection ([#472](https://github.com/UGS-GIO/ugs-warehouse/issues/472)) ([317eac3](https://github.com/UGS-GIO/ugs-warehouse/commit/317eac30a9104641698b13dfd95abfb68575b342))


### Bug Fixes

* **featureserv:** report the total in numberMatched ([#510](https://github.com/UGS-GIO/ugs-warehouse/issues/510)) ([453d5c9](https://github.com/UGS-GIO/ugs-warehouse/commit/453d5c9b1ae0493f5f176813a50a3960bce0a8e5))
* **pubs:** give an undated publication a flagged interval instead of a null date ([#487](https://github.com/UGS-GIO/ugs-warehouse/issues/487)) ([ac8b242](https://github.com/UGS-GIO/ugs-warehouse/commit/ac8b24279534eb071709d33be43aa2b8bd9b5d98))
* **pubs:** mark a publisher's plain TIFF scan as a source asset ([#486](https://github.com/UGS-GIO/ugs-warehouse/issues/486)) ([57a06cd](https://github.com/UGS-GIO/ugs-warehouse/commit/57a06cdad52bf12196de2a7ef64a25eb8941baf1))
* **pubs:** prune only orphans whose files the source still lists ([#492](https://github.com/UGS-GIO/ugs-warehouse/issues/492)) ([9f57a7e](https://github.com/UGS-GIO/ugs-warehouse/commit/9f57a7e4f1d017855e4a67be100e7bf9d8b0d813))
* **pubs:** stop re-downloading text-less PDFs and gzip the article corpus ([#500](https://github.com/UGS-GIO/ugs-warehouse/issues/500)) ([0525850](https://github.com/UGS-GIO/ugs-warehouse/commit/0525850b38863abf6e5e63b6ab25a1c15db61f19))
* read the max corner of a 3D STAC bbox in the catalog and the viewer ([#473](https://github.com/UGS-GIO/ugs-warehouse/issues/473)) ([1bfb867](https://github.com/UGS-GIO/ugs-warehouse/commit/1bfb867f42d3a615265c65feb05a49353858afcc))
* **serve:** answer HEAD on review objects like GCS does [ALL-6067] ([#438](https://github.com/UGS-GIO/ugs-warehouse/issues/438)) ([61f51e0](https://github.com/UGS-GIO/ugs-warehouse/commit/61f51e05cd85bad88e42fa10c67168576461b79c))
* **vector:** rebuild an archive written in an older GeoParquet format ([#503](https://github.com/UGS-GIO/ugs-warehouse/issues/503)) ([b515493](https://github.com/UGS-GIO/ugs-warehouse/commit/b51549311c18cb06d99c48794f894334cb2b30c0))
* **viewer:** draw the visual COG on the map page [ALL-6090] ([#448](https://github.com/UGS-GIO/ugs-warehouse/issues/448)) ([9330a2c](https://github.com/UGS-GIO/ugs-warehouse/commit/9330a2c59e345f751d1dc522f7e2af1f1b1bff4f))
* **viewer:** find publications by series ID in Discover and list newest first [ALL-6035] ([#474](https://github.com/UGS-GIO/ugs-warehouse/issues/474)) ([22cc918](https://github.com/UGS-GIO/ugs-warehouse/commit/22cc918dadc33392a11ff721d85265f94dd30004))
* **viewer:** pass the directory to OPFS move so Safari can finish a download ([#467](https://github.com/UGS-GIO/ugs-warehouse/issues/467)) ([175440d](https://github.com/UGS-GIO/ugs-warehouse/commit/175440de09a470d4d027ca630dc95aafb27240a2))
* **viewer:** turn the item preview's 3D terrain off when toggled off [ALL-6091] ([#450](https://github.com/UGS-GIO/ugs-warehouse/issues/450)) ([c288081](https://github.com/UGS-GIO/ugs-warehouse/commit/c2880814ad30a82201d0f9a150a6545b36f0c0f0))
* **viewer:** update to maplibre-gl 6 and deck.gl 9.4 [ALL-6080] ([#445](https://github.com/UGS-GIO/ugs-warehouse/issues/445)) ([a99637e](https://github.com/UGS-GIO/ugs-warehouse/commit/a99637ed4b890bc914f58450bba4dcdd446f0152))


### Performance Improvements

* load the whole catalog from one root items.json ([#496](https://github.com/UGS-GIO/ugs-warehouse/issues/496)) ([1c3b206](https://github.com/UGS-GIO/ugs-warehouse/commit/1c3b20606e8b3fdd030f65c216d81fe1552edd43))
* **pubs:** publish plate thumbnails, 3D sheets and covers as small WebP [ALL-6025] ([#386](https://github.com/UGS-GIO/ugs-warehouse/issues/386)) ([8e5ff6c](https://github.com/UGS-GIO/ugs-warehouse/commit/8e5ff6c792b7d37dfdddf8001fae783daf797a4a))
* **pubs:** skip the GDAL directory listing when reading COG headers ([#483](https://github.com/UGS-GIO/ugs-warehouse/issues/483)) ([0d4267e](https://github.com/UGS-GIO/ugs-warehouse/commit/0d4267e9dd6c2764eab652f2fc9b317b42cfdec9))

## [1.0.0](https://github.com/UGS-GIO/ugs-warehouse/compare/v0.0.1...v1.0.0) (2026-09-30)


### Miscellaneous Chores

* **release:** cut versioned releases with release-please, starting at 1.0.0 [ALL-6079] ([#439](https://github.com/UGS-GIO/ugs-warehouse/issues/439)) ([ff34deb](https://github.com/UGS-GIO/ugs-warehouse/commit/ff34debcc3a19d4babd7a2c7e2fdd034d6cfdf6f))
