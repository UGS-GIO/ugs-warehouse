import pyarrow as pa
import pytest

from ugs_warehouse.core import stac
from ugs_warehouse.pubs import vectors


@pytest.mark.parametrize("duck,expected", [
    ("GEOMETRY", "geometry"), ("BIGINT", "integer"), ("HUGEINT", "integer"), ("UBIGINT", "integer"),
    ("DOUBLE", "number"), ("FLOAT", "number"), ("DECIMAL(10,2)", "number"),
    ("BOOLEAN", "boolean"), ("TIMESTAMP", "datetime"), ("TIMESTAMP WITH TIME ZONE", "datetime"),
    ("DATE", "date"), ("VARCHAR", "string"), ("TEXT", "string"),
    ("BLOB", "blob"), ("UUID", "uuid")])
def test_duckdb_type_names(duck, expected):
    assert stac.table_type(duck) == expected


@pytest.mark.parametrize("arrow,expected", [
    (pa.int8(), "integer"), (pa.uint64(), "integer"),
    (pa.float16(), "number"), (pa.float32(), "number"), (pa.float64(), "number"),
    (pa.decimal128(10, 2), "number"), (pa.decimal256(40, 2), "number"),
    (pa.bool_(), "boolean"),
    (pa.timestamp("us"), "datetime"), (pa.timestamp("us", "UTC"), "datetime"),
    (pa.date32(), "date"), (pa.date64(), "date"),
    (pa.string(), "string"), (pa.large_string(), "string"),
    (pa.dictionary(pa.int8(), pa.string()), "string"),
    (pa.dictionary(pa.int8(), pa.int64()), "integer"),
    (pa.binary(), "binary"), (pa.time64("us"), "time64[us]"),
    (pa.list_(pa.int64()), "list<item: int64>"), (pa.struct([("a", pa.int8())]), "struct<a: int8>")])
def test_arrow_types_read_as_their_duckdb_equivalent(arrow, expected):
    assert stac.table_type(arrow) == expected


def test_a_publication_layer_and_a_serving_topic_name_a_column_the_same():
    """A pyarrow type and the DuckDB type it reads back as map to one name."""
    pairs = [(pa.int32(), "INTEGER"), (pa.float64(), "DOUBLE"), (pa.decimal128(12, 3), "DECIMAL(12,3)"),
             (pa.large_string(), "VARCHAR"), (pa.timestamp("ms"), "TIMESTAMP"), (pa.date32(), "DATE")]
    for arrow, duck in pairs:
        assert stac.table_type(arrow) == stac.table_type(duck)


def test_layer_columns_use_the_shared_mapper_and_the_geo_metadata():
    schema = pa.schema([("geometry", pa.binary()), ("n", pa.int16()), ("note", pa.large_string())],
                       metadata={b"geo": b'{"columns": {"geometry": {}}}'})
    cols, geo = vectors._columns_of(schema)
    assert cols == [{"name": "geometry", "type": "geometry"}, {"name": "n", "type": "integer"},
                    {"name": "note", "type": "string"}]
    assert geo == {"columns": {"geometry": {}}}
