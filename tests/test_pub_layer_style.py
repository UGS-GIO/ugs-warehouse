"""pubs/layer_style.py: a publication layer's style is the viewer's default look."""
from ugs_warehouse.pubs import layer_style as L


def test_the_style_is_the_viewers_default_look_one_layer_per_geometry_type():
    style = L.default_style("units")

    assert list(style) == ["layers"]  # no sources: the viewer supplies the PMTiles source
    assert [lay["type"] for lay in style["layers"]] == ["fill", "line", "circle"]
    assert style["layers"][0]["paint"] == {
        "fill-color": L.NEUTRAL, "fill-opacity": 0.15, "fill-outline-color": L.NEUTRAL}
    assert style["layers"][1]["paint"] == {"line-color": L.NEUTRAL, "line-width": 1.2}
    assert style["layers"][2]["paint"]["circle-radius"] == 3.5
    assert len({lay["id"] for lay in style["layers"]}) == 3
    assert all("source-layer" not in lay and "source" not in lay for lay in style["layers"])
