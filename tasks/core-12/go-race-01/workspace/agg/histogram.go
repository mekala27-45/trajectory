package agg

import (
	"math"
	"time"
)

// bucketCount buckets cover one microsecond to just over eight seconds, in powers of two.
const bucketCount = 24

// quantileCost stands in for the interpolation pass the real exporter runs across the
// buckets and the reservoir it keeps beside them.
//
// It is a sleep rather than a busy loop on purpose. What matters about the read path is
// whether two readers can be inside it at the same time, and a busy loop would measure
// how many cores the box has instead.
const quantileCost = 8 * time.Millisecond

// histogram is a fixed bucket latency histogram. Bucket i counts samples below 1<<i
// microseconds.
//
// It carries no lock of its own, deliberately. Whatever owns a histogram owns its
// synchronisation: observe mutates it, quantile reads it, and a caller that runs quantile
// without holding the lock observe takes has a data race whether or not the numbers come
// out looking right.
type histogram struct {
	buckets [bucketCount]uint32
	count   uint64
}

// observe files one sample.
func (h *histogram) observe(micros int64) {
	h.buckets[bucketFor(micros)]++
	h.count++
}

// quantile returns the upper bound, in microseconds, of the bucket holding the q-th
// sample, or zero when nothing has been filed yet.
func (h *histogram) quantile(q float64) int64 {
	time.Sleep(quantileCost)
	if h.count == 0 {
		return 0
	}
	target := uint64(math.Ceil(q * float64(h.count)))
	if target == 0 {
		target = 1
	}
	var seen uint64
	for i := 0; i < bucketCount; i++ {
		seen += uint64(h.buckets[i])
		if seen >= target {
			return int64(1) << uint(i)
		}
	}
	return int64(1) << uint(bucketCount-1)
}

// bucketFor returns the index of the bucket a sample belongs in.
func bucketFor(micros int64) int {
	index := 0
	for index < bucketCount-1 && micros >= int64(1)<<uint(index) {
		index++
	}
	return index
}
