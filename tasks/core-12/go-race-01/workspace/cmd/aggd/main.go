// Command aggd runs a short synthetic load against the registry and prints the report the
// exporter would publish. It is the quickest way to watch the registry misbehave outside
// of the test suite.
package main

import (
	"fmt"
	"sync"

	"example.internal/aggregator/agg"
)

const (
	workers = 6
	each    = 500
)

var endpoints = []string{"/v1/cart", "/v1/checkout", "/v1/inventory"}

func main() {
	registry := agg.New()

	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			for i := 0; i < each; i++ {
				registry.Observe(endpoints[(worker+i)%len(endpoints)], int64(150+i%400))
			}
		}(w)
	}
	wg.Wait()

	fmt.Printf("observed %d of %d\n", registry.Observed(), workers*each)
	for _, name := range registry.Endpoints() {
		stat, ok := registry.Snapshot(name)
		if !ok {
			continue
		}
		fmt.Printf("%-16s count=%-6d mean=%-6d max=%-6d p95=%d\n",
			name, stat.Count, stat.MeanMicros(), stat.MaxMicros, stat.P95Micros)
	}
}
