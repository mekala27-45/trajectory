// Package agg aggregates per endpoint request latency for a service that serves a few
// thousand requests a second across a handful of endpoints.
//
// One Registry is shared by every request goroutine and by the exporter goroutine that
// scrapes it once a second. Observe is on the request path. Snapshot, SnapshotAll,
// Endpoints and Observed are on the exporter's path. Neither path is allowed to make the
// other wait longer than it has to, and two exporter reads of the same endpoint must be
// able to run at the same time: quantile walks the histogram in place, so a read is not
// cheap and serialising reads behind one lock turns a one second scrape into a stall.
//
// This started life single threaded. A mutex was added later, around the map, which is
// where it stopped.
package agg

import (
	"sort"
	"sync"
)

// Stat is the aggregate view of one endpoint.
type Stat struct {
	Count       int64
	TotalMicros int64
	MaxMicros   int64
	P95Micros   int64
}

// MeanMicros returns the mean latency in microseconds, or zero when nothing has been
// recorded for the endpoint.
func (s Stat) MeanMicros() int64 {
	if s.Count == 0 {
		return 0
	}
	return s.TotalMicros / s.Count
}

// bucket holds the running aggregate for one endpoint.
type bucket struct {
	hist        histogram
	count       int64
	totalMicros int64
	maxMicros   int64
}

// Registry holds one bucket per endpoint.
type Registry struct {
	mu      sync.Mutex
	buckets map[string]*bucket

	// observed counts every call to Observe, across all endpoints.
	observed int64

	// hotName and hot cache the bucket for the endpoint seen most recently. In a real
	// process nearly every call arrives for the same endpoint as the one before it, so
	// this skips the map lookup for the common case.
	hotName string
	hot     *bucket
}

// New returns an empty registry.
func New() *Registry {
	return &Registry{buckets: make(map[string]*bucket)}
}

// Observe records one request against an endpoint.
func (r *Registry) Observe(endpoint string, micros int64) {
	b := r.lookup(endpoint)
	b.count++
	b.totalMicros += micros
	if micros > b.maxMicros {
		b.maxMicros = micros
	}
	b.hist.observe(micros)
	r.observed++
}

// lookup returns the bucket for an endpoint, creating it the first time it is seen.
func (r *Registry) lookup(endpoint string) *bucket {
	if r.hot != nil && endpoint == r.hotName {
		return r.hot
	}
	r.mu.Lock()
	b := r.buckets[endpoint]
	if b == nil {
		b = &bucket{}
		r.buckets[endpoint] = b
	}
	r.mu.Unlock()
	r.hotName = endpoint
	r.hot = b
	return b
}

// Snapshot returns the aggregate for one endpoint, and false when the endpoint has never
// been observed.
func (r *Registry) Snapshot(endpoint string) (Stat, bool) {
	r.mu.Lock()
	b := r.buckets[endpoint]
	r.mu.Unlock()
	if b == nil {
		return Stat{}, false
	}
	return Stat{
		Count:       b.count,
		TotalMicros: b.totalMicros,
		MaxMicros:   b.maxMicros,
		P95Micros:   b.hist.quantile(0.95),
	}, true
}

// SnapshotAll returns the aggregate for every endpoint seen so far.
func (r *Registry) SnapshotAll() map[string]Stat {
	names := r.Endpoints()
	out := make(map[string]Stat, len(names))
	for _, name := range names {
		if stat, ok := r.Snapshot(name); ok {
			out[name] = stat
		}
	}
	return out
}

// Endpoints returns every endpoint seen so far, sorted.
func (r *Registry) Endpoints() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	names := make([]string, 0, len(r.buckets))
	for name := range r.buckets {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// Observed returns the number of Observe calls the registry has taken.
func (r *Registry) Observed() int64 {
	return r.observed
}

// Reset drops every recorded sample. The exporter calls it after a successful publish.
func (r *Registry) Reset() {
	r.mu.Lock()
	r.buckets = make(map[string]*bucket)
	r.observed = 0
	r.mu.Unlock()
}
