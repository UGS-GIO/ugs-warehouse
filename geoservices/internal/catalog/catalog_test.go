package catalog

import (
	"errors"
	"testing"
	"time"
)

func TestStaleOnError(t *testing.T) {
	c := NewTTL(10 * time.Millisecond)
	c.Get("k", func() (any, error) { return 1, nil })
	time.Sleep(20 * time.Millisecond)
	v, err := c.Get("k", func() (any, error) { return nil, errors.New("cdn down") })
	if err != nil || v != 1 {
		t.Fatalf("stale value not served: %v %v", v, err)
	}
	if _, err := c.Get("other", func() (any, error) { return nil, errors.New("down") }); err == nil {
		t.Fatal("no stale value: the error should surface")
	}
}

func TestParseTopics(t *testing.T) {
	doc := `{"items":[
	 {"id":"a","properties":{"title":"A","ugs:renders":{"z":{},"b":{"esri_url":"https://x/b.json","title":"B"}}},"assets":{"data":{"href":"a.parquet"},"overview_1":{"href":"o.parquet","ugs:simplify_tolerance":0.01}}},
	 {"id":"no-data","assets":{"thumbnail":{"href":"t.webp"}}},
	 {"id":"c","properties":{},"assets":{"data":{"href":"c.parquet"}}}]}`
	tp, err := parseTopics([]byte(doc))
	if err != nil {
		t.Fatal(err)
	}
	if len(tp.order) != 2 || tp.order[0].ID != "a" {
		t.Fatalf("topics %v", tp.order)
	}
	a := tp.byID["a"]
	if a.Renders[0].Name != "z" || a.Renders[1].EsriURL != "https://x/b.json" || *a.Title != "A" || len(a.Assets) != 2 {
		t.Errorf("renders keep document order: %+v", a)
	}
	if c := tp.byID["c"]; len(c.Renders) != 1 || c.Renders[0].Name != "default" || c.Title != nil {
		t.Errorf("a topic without renders is served as default: %+v", c)
	}
}
