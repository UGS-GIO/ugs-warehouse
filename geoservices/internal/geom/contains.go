package geom

import (
	"github.com/paulmach/orb"
	sf "github.com/peterstace/simplefeatures/geom"
)

// Contains is the DE-9IM contains of a over b. Esri's spatialRel puts the query geometry first:
// esriSpatialRelContains is Contains(query, feature), Within the reverse. Bounds rule out most
// pairs before the exact test.
func Contains(a, b orb.Geometry) bool {
	if a == nil || b == nil {
		return false
	}
	ba, bb := Bounds(a), Bounds(b)
	if !ba.Contains(bb.Min) || !ba.Contains(bb.Max) {
		return false
	}
	ok, err := sf.Contains(toSF(a), toSF(b))
	return err == nil && ok
}

// toSF is g as a simplefeatures geometry.
func toSF(g orb.Geometry) sf.Geometry {
	switch g := g.(type) {
	case orb.Point:
		return sf.NewPointXY(g[0], g[1]).AsGeometry()
	case orb.MultiPoint:
		return sf.NewMultiPointXY(flat(g)...).AsGeometry()
	case orb.LineString:
		return line(g).AsGeometry()
	case orb.MultiLineString:
		ls := make([]sf.LineString, len(g))
		for i, l := range g {
			ls[i] = line(l)
		}
		return sf.NewMultiLineString(ls).AsGeometry()
	case orb.Ring:
		return polygon(orb.Polygon{g}).AsGeometry()
	case orb.Polygon:
		return polygon(g).AsGeometry()
	case orb.MultiPolygon:
		ps := make([]sf.Polygon, len(g))
		for i, p := range g {
			ps[i] = polygon(p)
		}
		return sf.NewMultiPolygon(ps).AsGeometry()
	case orb.Bound:
		return polygon(g.ToPolygon()).AsGeometry()
	case orb.Collection:
		gs := make([]sf.Geometry, len(g))
		for i, x := range g {
			gs[i] = toSF(x)
		}
		return sf.NewGeometryCollection(gs).AsGeometry()
	}
	return sf.Geometry{}
}

func line(run []orb.Point) sf.LineString {
	return sf.NewLineString(sf.NewSequence(flat(run), sf.DimXY))
}

func polygon(p orb.Polygon) sf.Polygon {
	rings := make([]sf.LineString, len(p))
	for i, r := range p {
		rings[i] = line(r)
	}
	return sf.NewPolygon(rings)
}

func flat(run []orb.Point) []float64 {
	xy := make([]float64, 0, 2*len(run))
	for _, p := range run {
		xy = append(xy, p[0], p[1])
	}
	return xy
}
