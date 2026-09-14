package agg_test

import (
	"fmt"
	"sync"
	"testing"

	"example.internal/aggregator/agg"
)

func TestObserveThenSnapshot(t *testing.T) {
	r := agg.New()
	for i := 0; i < 10; i++ {
		r.Observe("/v1/checkout", 900)
	}
	r.Observe("/v1/cart", 120)

	stat, ok := r.Snapshot("/v1/checkout")
	if !ok {
		t.Fatal("/v1/checkout was observed but Snapshot says it does not exist")
	}
	if stat.Count != 10 {
		t.Errorf("Count = %d, want 10", stat.Count)
	}
	if stat.TotalMicros != 9000 {
		t.Errorf("TotalMicros = %d, want 9000", stat.TotalMicros)
	}
	if stat.MaxMicros != 900 {
		t.Errorf("MaxMicros = %d, want 900", stat.MaxMicros)
	}
	if stat.MeanMicros() != 900 {
		t.Errorf("MeanMicros = %d, want 900", stat.MeanMicros())
	}
	// Buckets are powers of two and quantile reports the bucket's upper bound, so ten
	// samples of 900 microseconds put p95 in the bucket that ends at 1024.
	if stat.P95Micros != 1024 {
		t.Errorf("P95Micros = %d, want 1024", stat.P95Micros)
	}
	if r.Observed() != 11 {
		t.Errorf("Observed = %d, want 11", r.Observed())
	}
}

func TestSnapshotAllCoversEveryEndpoint(t *testing.T) {
	r := agg.New()
	r.Observe("/v1/cart", 100)
	r.Observe("/v1/checkout", 200)
	r.Observe("/v1/cart", 300)

	all := r.SnapshotAll()
	if len(all) != 2 {
		t.Fatalf("SnapshotAll returned %d endpoints, want 2", len(all))
	}
	if all["/v1/cart"].Count != 2 || all["/v1/cart"].TotalMicros != 400 {
		t.Errorf("/v1/cart = %+v, want Count 2 and TotalMicros 400", all["/v1/cart"])
	}
	if got := r.Endpoints(); len(got) != 2 || got[0] != "/v1/cart" || got[1] != "/v1/checkout" {
		t.Errorf("Endpoints = %v, want sorted [/v1/cart /v1/checkout]", got)
	}
}

// TestConcurrentObserveKeepsEveryUpdate is the reproduction. Every request goroutine in
// the service calls Observe on the same registry.
func TestConcurrentObserveKeepsEveryUpdate(t *testing.T) {
	const (
		writers = 8
		each    = 250
	)
	endpoints := []string{"/v1/cart", "/v1/checkout", "/v1/inventory", "/v1/orders"}

	r := agg.New()
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			for i := 0; i < each; i++ {
				r.Observe(endpoints[(worker+i)%len(endpoints)], int64(100+i))
			}
		}(w)
	}
	wg.Wait()

	if got, want := r.Observed(), int64(writers*each); got != want {
		t.Errorf("Observed = %d, want %d", got, want)
	}
	var total int64
	for _, name := range endpoints {
		stat, ok := r.Snapshot(name)
		if !ok {
			t.Errorf("%s was observed but is missing from the registry", name)
			continue
		}
		total += stat.Count
	}
	if want := int64(writers * each); total != want {
		t.Errorf("counts across endpoints sum to %d, want %d", total, want)
	}
}

// TestExporterScrapesWhileRequestsLand is the other half of the reproduction: the
// exporter reads while the request path writes.
func TestExporterScrapesWhileRequestsLand(t *testing.T) {
	const (
		writers = 4
		each    = 200
		scrapes = 5
	)

	r := agg.New()
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			name := fmt.Sprintf("/v1/shard-%d", worker)
			for i := 0; i < each; i++ {
				r.Observe(name, int64(200+i))
			}
		}(w)
	}

	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < scrapes; i++ {
			for name, stat := range r.SnapshotAll() {
				if stat.Count < 0 || stat.TotalMicros < 0 {
					t.Errorf("%s scraped a negative aggregate: %+v", name, stat)
				}
			}
		}
	}()
	wg.Wait()

	if got, want := r.Observed(), int64(writers*each); got != want {
		t.Errorf("Observed = %d, want %d", got, want)
	}
}
