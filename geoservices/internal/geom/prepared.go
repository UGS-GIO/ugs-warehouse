package geom

import (
	"github.com/paulmach/orb"
	sf "github.com/peterstace/simplefeatures/geom"
)

// Prepared is a query geometry made ready to test many features against: its bounds, whether it
// is a plain box, and simplefeatures' prepared (indexed) form for the exact DE-9IM tests.
type Prepared struct {
	G     orb.Geometry
	Bound orb.Bound
	box   bool
	sf    *sf.PreparedGeometry
}

// Prepare readies g; the exact tests answer false if simplefeatures cannot take it.
func Prepare(g orb.Geometry) *Prepared {
	p := &Prepared{G: g, Bound: Bounds(g)}
	p.box = isBox(g, p.Bound)
	if pg, err := sf.Prepare(toSF(g)); err == nil {
		p.sf = &pg
	}
	return p
}

// Place is where a bbox lies against a prepared geometry.
type Place int8

const (
	Across  Place = iota // it may meet the boundary: only the feature itself can tell
	Outside              // disjoint from the geometry
	Inside               // in its interior, clear of the boundary
)

// Place locates bbox b, so a feature inside it is known to meet or miss the geometry unread.
func (p *Prepared) Place(b orb.Bound) Place {
	if !b.Intersects(p.Bound) {
		return Outside
	}
	if p.box {
		if b.Min[0] > p.Bound.Min[0] && b.Min[1] > p.Bound.Min[1] && b.Max[0] < p.Bound.Max[0] && b.Max[1] < p.Bound.Max[1] {
			return Inside
		}
		return Across
	}
	if p.sf == nil {
		return Across
	}
	bb := toSF(boundShape(b))
	if in, err := p.sf.ContainsProperly(bb); err == nil && in {
		return Inside
	}
	if meets, err := p.sf.Intersects(bb); err == nil && !meets {
		return Outside
	}
	return Across
}

// Contains is the DE-9IM contains of the geometry over g.
func (p *Prepared) Contains(g orb.Geometry) bool {
	b := Bounds(g)
	if p.sf == nil || !p.Bound.Contains(b.Min) || !p.Bound.Contains(b.Max) {
		return false
	}
	ok, err := p.sf.Contains(toSF(g))
	return err == nil && ok
}

// Within is the DE-9IM within of the geometry in g.
func (p *Prepared) Within(g orb.Geometry) bool {
	b := Bounds(g)
	if p.sf == nil || !b.Contains(p.Bound.Min) || !b.Contains(p.Bound.Max) {
		return false
	}
	ok, err := p.sf.Within(toSF(g))
	return err == nil && ok
}

// boundShape is b as the simplest geometry covering it: a point, a segment or a polygon.
func boundShape(b orb.Bound) orb.Geometry {
	switch {
	case b.Min == b.Max:
		return b.Min
	case b.Min[0] == b.Max[0] || b.Min[1] == b.Max[1]:
		return orb.LineString{b.Min, b.Max}
	}
	return b.ToPolygon()
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
