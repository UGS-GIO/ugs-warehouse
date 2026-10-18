package geom

import (
	"slices"

	"github.com/paulmach/orb"
)

// Contains reports that b lies wholly inside a, boundaries included. Esri's spatialRel puts the
// query geometry first: esriSpatialRelContains is Contains(query, feature), Within the reverse.
func Contains(a, b orb.Geometry) bool {
	if a == nil || b == nil {
		return false
	}
	pa, pb := flatten(a), flatten(b)
	if len(pa) == 0 || len(pb) == 0 {
		return false
	}
	ba, bb := Bounds(a), Bounds(b)
	if !ba.Contains(bb.Min) || !ba.Contains(bb.Max) {
		return false
	}
	for _, y := range pb {
		if !slices.ContainsFunc(pa, func(x orb.Geometry) bool { return covers(x, y) }) {
			return false
		}
	}
	return true
}

// covers is Contains for one part of each.
func covers(a, b orb.Geometry) bool {
	switch a := a.(type) {
	case orb.Point:
		p, ok := b.(orb.Point)
		return ok && p == a
	case orb.LineString:
		switch b := b.(type) {
		case orb.Point:
			return onLine(a, b)
		case orb.LineString:
			return allOf(b, func(p orb.Point) bool { return onLine(a, p) })
		}
	case orb.Polygon:
		switch b := b.(type) {
		case orb.Point:
			return inPolygon(b, a)
		case orb.LineString:
			return runInPolygon(a, b)
		case orb.Polygon:
			if len(b) == 0 || !runInPolygon(a, b[0]) {
				return false
			}
			// A hole of a inside b leaves part of b uncovered.
			for _, h := range a[1:] {
				if slices.ContainsFunc(h, func(p orb.Point) bool { return ringPosition(p, b[0]) == 1 }) {
					return false
				}
			}
			return true
		}
	}
	return false
}

// allOf tests every vertex of a run and the midpoint of every segment.
func allOf(run []orb.Point, test func(orb.Point) bool) bool {
	for i, p := range run {
		if !test(p) {
			return false
		}
		if i > 0 && !test(orb.Point{(p[0] + run[i-1][0]) / 2, (p[1] + run[i-1][1]) / 2}) {
			return false
		}
	}
	return true
}

// runInPolygon: the run's points are in poly and none of its segments cuts across a ring.
func runInPolygon(poly orb.Polygon, run []orb.Point) bool {
	if !allOf(run, func(p orb.Point) bool { return inPolygon(p, poly) }) {
		return false
	}
	for _, ring := range poly {
		for i := 0; i+1 < len(run); i++ {
			for j := 0; j+1 < len(ring); j++ {
				if crossesInside(run[i], run[i+1], ring[j], ring[j+1]) {
					return false
				}
			}
		}
	}
	return true
}

// crossesInside: the segments cross at a point inside both, not at an end of either.
func crossesInside(a1, a2, b1, b2 orb.Point) bool {
	denom := (b2[1]-b1[1])*(a2[0]-a1[0]) - (b2[0]-b1[0])*(a2[1]-a1[1])
	if denom == 0 {
		return false
	}
	ua := ((b2[0]-b1[0])*(a1[1]-b1[1]) - (b2[1]-b1[1])*(a1[0]-b1[0])) / denom
	ub := ((a2[0]-a1[0])*(a1[1]-b1[1]) - (a2[1]-a1[1])*(a1[0]-b1[0])) / denom
	return ua > 0 && ua < 1 && ub > 0 && ub < 1
}
