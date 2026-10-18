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
	// b clear of a box's edges is inside it.
	if isBox(a, ba) && bb.Min[0] > ba.Min[0] && bb.Min[1] > ba.Min[1] && bb.Max[0] < ba.Max[0] && bb.Max[1] < ba.Max[1] {
		return true
	}
	ok, err := sf.Contains(toSF(a), toSF(b))
	return err == nil && ok
}

// isBox reports a polygon that is its bounds b: one closed ring through b's four corners along
// axis-parallel edges.
func isBox(g orb.Geometry, b orb.Bound) bool {
	p, ok := g.(orb.Polygon)
	if !ok || len(p) != 1 || len(p[0]) != 5 || p[0][0] != p[0][4] || b.Min[0] == b.Max[0] || b.Min[1] == b.Max[1] {
		return false
	}
	r := p[0]
	for i := range 4 {
		corner := (r[i][0] == b.Min[0] || r[i][0] == b.Max[0]) && (r[i][1] == b.Min[1] || r[i][1] == b.Max[1])
		if !corner || (r[i][0] != r[i+1][0]) == (r[i][1] != r[i+1][1]) || r[i] == r[(i+2)%4] {
			return false
		}
	}
	return true
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
