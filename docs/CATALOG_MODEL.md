# Catalog model: tables, relationships and publications

This page sets out how the warehouse's STAC catalog models three things:

- tables, whether or not they have geometry;
- the relationships between tables;
- publications and the data that comes with them.

For how the catalog is built and served, see [Architecture](ARCHITECTURE.md).

The model uses STAC 1.1 and its official extensions, and follows Portolan's conformance rules. The
one field STAC has no standard for, the join columns of a relationship, is the namespaced
`ugs:foreign_keys`.

## Status

| Part | State |
|---|---|
| Publications as Collections, with their plate, layers and tables as Items | Rolling out, one series at a time |
| Relationships between a publication's layers and tables | Rolling out with publications |
| Non-spatial serving-topic tables as their own Items | Planned |
| Spreadsheet attachments (xls, xlsx, csv) as table Items, and as point layers when they hold latitude and longitude | Rolling out with publications |

## One Item per table

Every table is its own STAC Item, whether or not it has geometry. That includes small lookup
tables, the "one" side of a one-to-many, and many-to-many bridge tables. The Item carries the data
as a `data` asset and the schema as `table:columns` (the Table extension). Whether a table is
spatial never decides whether it gets an Item.

This is what the [STAC Table extension](https://github.com/stac-extensions/table) recommends: "Use
`Item` objects to catalog an individual table." It is also how Microsoft Planetary Computer's `fia`
collection works, with one Item for each of its 51 tables, lookups included.

One Item per table means:

- a shared lookup table, such as a geologic map's Description of Map Units, can be found and
  linked to on its own;
- a relationship between two non-spatial tables has an Item at each end;
- every relationship link resolves, as Portolan requires (CORE-035).

## Geometry for a table without its own

A table with no geometry gets a real geometry for its area of interest, never a null geometry.
For a table in a publication, the area of interest is the publication's footprint. For a serving
table, it will be the bounding box of the spatial tables it relates to (a rectangle, not their
merged shapes), falling back to a buffered Utah outline.

Null geometry is valid STAC. The catalog avoids it for three reasons:

- STAC's Best Practices strongly recommend that every Item have a geometry. They warn that an Item
  without one "will likely not show up in STAC API searches".
- The catalog's item mirror (`items.parquet`) leaves out Items without geometry, and Portolan
  requires the mirror to reproduce the catalog (FMT-042).
- The prior art does the same: Planetary Computer gives its lookup tables the program's footprint.

The trade-off is that an area of interest is approximate. A search anywhere in the area returns
these tables, even where no row of the table applies. The viewer keeps them off the Discover map.

## Relationships

Three different meanings get three different standard link relations, and they are never mixed:

| Meaning | Link relation | Detail |
|---|---|---|
| One table joins another | `related`, on both Items | `ugs:foreign_keys`: which columns join, and which end is the "one" |
| One product is built from others | `derived_from`, from the product to its sources | none |
| The catalog tree | `root`, `parent`, `child`, `collection`, `item` | none |

For example, a geologic map's unit polygons join its Description of Map Units on `MapUnit`. That's
a `related` link with `ugs:foreign_keys`. A scale-tier mosaic built from many map plates is a
`derived_from` link from the mosaic to each plate.

A `related` link says two Items are related, but not how to join them. `ugs:foreign_keys` carries
the join, in the shape of a Frictionless Table Schema `foreignKeys` entry. It sits on the joining
Item's `data` asset. This one is from the unit polygons of OFR-784DM (Mills Junction):

```json
"ugs:foreign_keys": [{
  "fields": ["MapUnit"],
  "reference": {
    "resource": "OFR-784DM__OFR784DM_MillsJunction__DescriptionOfMapUnits",
    "href": "https://maps-assets.geology.utah.gov/.../OFR784DM_MillsJunction__DescriptionOfMapUnits.parquet",
    "fields": ["MapUnit"]
  },
  "ugs:detected_by": "inferred",
  "ugs:confidence": 100
}]
```

The viewer uses it to show a clicked feature's related rows.

### Where relationships come from

- **Serving topics** take them from the foreign keys declared in the database, which dbt tests
  enforce. The catalog only projects them; nothing is edited by hand.
- **Publications** take them from the data files. A file geodatabase's relationship classes are
  read as declared (`ugs:detected_by: "declared"`). Other joins are inferred within one dataset,
  from matching column names and overlapping values (`ugs:detected_by: "inferred"`, with
  `ugs:confidence`). The rules are strict, because these are published without review:
  - only a non-spatial table can be the target;
  - the key needs at least three distinct values;
  - row-id columns are never keys.

A table that many layers join to, such as a data-sources lookup, gets the `reference` asset role,
so the viewer can collapse it.

### Retiring the copy onto the parent

Today a serving topic's non-spatial table is copied onto each spatial table that references it, as
a `related` asset. Once every table has its own Item, that copy goes away: the viewer follows the
`related` link and reads the table's own `data` asset. That removes the duplicate storage and the
extra reads.

## Publications

Each publication is a STAC Collection, and each series is a Catalog:

```text
ugs-publications/                 group Catalog
  M/                              series Catalog (from the publication id's letters)
    M-246DM/                      publication Collection: PDF, cover, original zips
      M-246DM_plate/              plate COG Item
      M-246DM__<label>/           one Item per extracted layer or table
```

There are three groups: `ugs-publications`, `ugs-mining-district-files` and `ugs-external`.
Portolan requires Collections to be leaves (PORTO-CORE-018), so a series is a Catalog and holds no
data itself.

- **Files on the Collection.** The publication's own files are Collection assets: the PDF
  (`publication`), the cover (`thumbnail`) and the original zips (`source`). A `geopackage` asset
  holds every layer and table in one file. The DOI is a `cite-as` link plus `sci:doi` and
  `sci:citation`, on the Collection and on each Item.
- **Data as Items.** The plate COG, the map units, any 3D fence diagram, and each layer and table
  extracted from the publication's data zips and spreadsheets are Items. A layer is GeoParquet
  plus PMTiles. A table is Parquet, with the publication footprint as its area of interest. A
  spreadsheet with latitude and longitude columns is also a point layer.
- **Item ids.** A layer or table Item's id is `<publication id>__<label>`. Publication ids never
  contain `__`, so the first `__` always ends the publication id, and no layer id can collide with
  another publication's id. The plate is `<publication id>_plate`.
- **Kind and source.** Each Item records its kind (`ugs:layer_kind`: `layer`, `table` or
  `supporting`), the file it came from (`ugs:source_file`) and its dataset within that file
  (`ugs:source_key`, `ugs:source_dataset`). The viewer groups a publication's parts by these.
- **Report-only publications.** A publication with no data is a Collection with its documents and
  no Items.
- **Single-file publications.** A publication with exactly one data file keeps that file on the
  Collection and has no Items. These two cases are Portolan's single-file pattern. A Collection
  whose only Item holds a PDF fails Portolan's validator (PTL-COL-001).
- **Editions.** `predecessor-version`, `successor-version` and `latest-version` links connect a
  publication's Collections. The viewer shows them as the publication's history.
- **Mosaics.** A scale-tier mosaic's `derived_from` links point at each source plate Item, or at
  the publication Collection when the plate is its only data file. Its `tiles` asset is the raster
  PMTiles and its `visual` asset a Web Mercator WebP COG, which the ImageServer serves.
- **Geologic units under a raster.** A plate or mosaic Item whose maps have unit polygons carries a
  `units` asset (PMTiles of unit code, name, age and description, from each map's own data). The
  viewer reads it to show the unit at a clicked point.

The tree is grouped by series because a series comes from the publication id and never changes.
Quadrangle, county, scale and year are properties, offered as filters in the viewer, not folders.
Portolan's validator reads parent and child from the folder tree, so a tree grouped any other way
would fail it.

### Search and the catalog index

The root `items.json` has one entry per publication Collection, plus an entry per Item tagged with
its publication (`ugs:in_publication`, `ugs:publication_title`). Discover shows each publication as
one result and rolls its layers and tables up under it, rather than listing a separate result for
every publication's Description of Map Units. Each series Catalog also has its own `items.json`,
advertised with an `ugs-items-index` link.

### Large series

Portolan says a Catalog with twenty or more children SHOULD use subcatalogs (PORTO-CORE-078). Many
series are far over that: OFR has over 800 publications and M over 300. No stable grouping clears
it. Year folders would still leave 17 years over twenty and add a URL segment. So each
series stays one level of publications, the validator reports a warning (not an error), and search
and filters do the browsing.

## Standards check

**Standard:**

- one Item per table;
- `related` (IANA-registered) on both ends;
- `derived_from` for lineage (a STAC relation type, from the Best Practices);
- `table:columns`;
- an area-of-interest geometry;
- the Scientific and Version extensions for DOIs and editions.

**Allowed custom values:**

- the `related` and `reference` asset roles; STAC allows custom roles.

**Namespaced, because nothing standard fits:**

- `ugs:foreign_keys`. The Table extension's relationship issue has been open since 2021, and
  `related` carries no columns or direction.

**Avoided:**

- a `bbox` alongside a null geometry, which STAC's JSON schema prohibits;
- a new link relation name;
- join semantics packed into a link's `rel`.

## References

- [STAC Item spec 1.1.0](https://github.com/radiantearth/stac-spec/blob/master/item-spec/item-spec.md):
  geometry may be null; `bbox` is required with a geometry and prohibited without one.
- [STAC Best Practices](https://github.com/radiantearth/stac-spec/blob/master/best-practices.md):
  "Unlocated Items", "Data that is not spatial" and "Using Relation Types".
- [STAC Table extension](https://github.com/stac-extensions/table) and its open issue
  ["Capture table relationships"](https://github.com/stac-extensions/table/issues/2).
- [Frictionless Table Schema](https://specs.frictionlessdata.io/table-schema/#foreign-keys)
  `foreignKeys`.
- [IANA link relations](https://www.iana.org/assignments/link-relations/link-relations.xhtml):
  `related`. STAC Best Practices, "Using Relation Types": `derived_from`.
- [Portolan](https://github.com/portolan-sdi/portolan-spec): PORTO-CORE-018, CORE-035,
  PORTO-CORE-078 and FMT-042; PTL-COL-001 is its validator's check for a Collection whose only Item
  holds a PDF.
- Microsoft Planetary Computer's
  [`fia` collection](https://planetarycomputer.microsoft.com/dataset/fia): one Item per table,
  lookups included.
