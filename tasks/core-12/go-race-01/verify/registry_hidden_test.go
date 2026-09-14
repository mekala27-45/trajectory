// Hidden verification suite for go-race-01.
//
// Copied into the workspace module as a test only package, so it compiles against the
// exported surface of agg and fails to build at all if that surface moved.
//
// Four of these tests exist to catch a change that silences the race detector without
// making the registry work:
//
//   - TestConcurrentReadsDoNotSerialise fails when every method is wrapped in one
//     exclusive mutex. That is the first thing most people reach for, it does silence the
//     detector, and it also turns every exporter scrape into a queue behind the request
//     path. It passes in the broken workspace, so it only ever fires on a bad fix.
//   - TestProvidedFilesAreUnmodified fails when the cost is taken out of histogram.go or
//     the failing tests are deleted, which are the two cheapest ways to make the symptom
//     go away without touching the cause.
//   - TestWorkspaceRaceSuitePasses runs the workspace's own suite under -race in a child
//     process, so satisfying this file alone does not count.
//   - TestPublicSurfaceIsUnchanged fails when the fix is to hand locking to the caller,
//     which is a rewrite of every call site dressed up as a fix.
package zzhidden

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"example.internal/aggregator/agg"
)

// moduleRoot is where go test puts the working directory for this package, one level
// below the module root.
const moduleRoot = ".."

// recurseGuard stops a child go test invocation from running this package again. The
// child is pointed at ./agg/ so it cannot happen today, and a fork bomb is an expensive
// way to find out that changed.
const recurseGuard = "TRAJECTORY_INNER_GO_TEST"

// Hashes of the two files the task statement puts out of bounds, taken from the workspace
// as shipped.
const (
	histogramSHA256 = "eb417e6ff6978839aecd2469d1fa575091b58481242f83fad2f89dbb295710ee"
	suiteSHA256     = "408950cba8bfabab173c519a5e0980e669bc00798aec3537b55a7201f0749b50"
)

func TestMain(m *testing.M) {
	if os.Getenv(recurseGuard) == "1" {
		fmt.Fprintln(os.Stderr, "the hidden suite was pulled into its own child run, refusing to recurse")
		os.Exit(2)
	}
	os.Exit(m.Run())
}

func TestProvidedFilesAreUnmodified(t *testing.T) {
	for path, want := range map[string]string{
		filepath.Join(moduleRoot, "agg", "histogram.go"):       histogramSHA256,
		filepath.Join(moduleRoot, "agg", "aggregator_test.go"): suiteSHA256,
	} {
		data, err := os.ReadFile(path) // #nosec G304  fixed paths
		if err != nil {
			t.Errorf("%s: %v", path, err)
			continue
		}
		sum := sha256.Sum256(data)
		if got := hex.EncodeToString(sum[:]); got != want {
			t.Errorf("%s was modified. The read path has to become concurrent, not cheaper, "+
				"and deleting the failing tests is not a fix", filepath.Base(path))
		}
	}
}

func TestPublicSurfaceIsUnchanged(t *testing.T) {
	registry := agg.New()

	// These assignments are the signature check. Nothing here compiles if a parameter,
	// a result or a receiver moved.
	var (
		_ func(string, int64)           = registry.Observe
		_ func(string) (agg.Stat, bool) = registry.Snapshot
		_ func() map[string]agg.Stat    = registry.SnapshotAll
		_ func() []string               = registry.Endpoints
		_ func() int64                  = registry.Observed
		_ func()                        = registry.Reset
		_ func() *agg.Registry          = agg.New
		_ func() int64                  = agg.Stat{}.MeanMicros
	)

	wantMethods := []string{"Endpoints", "Observe", "Observed", "Reset", "Snapshot", "SnapshotAll"}
	registryType := reflect.TypeOf(registry)
	var gotMethods []string
	for i := 0; i < registryType.NumMethod(); i++ {
		gotMethods = append(gotMethods, registryType.Method(i).Name)
	}
	sort.Strings(gotMethods)
	if !reflect.DeepEqual(gotMethods, wantMethods) {
		t.Errorf("Registry's exported methods are %v, want %v. Callers compile against "+
			"this surface, so locking cannot be pushed out to them", gotMethods, wantMethods)
	}

	wantFields := []struct {
		name string
		kind reflect.Kind
	}{
		{"Count", reflect.Int64},
		{"TotalMicros", reflect.Int64},
		{"MaxMicros", reflect.Int64},
		{"P95Micros", reflect.Int64},
	}
	statType := reflect.TypeOf(agg.Stat{})
	if statType.NumField() != len(wantFields) {
		t.Fatalf("Stat has %d fields, want %d", statType.NumField(), len(wantFields))
	}
	for i, want := range wantFields {
		field := statType.Field(i)
		if field.Name != want.name || field.Type.Kind() != want.kind {
			t.Errorf("Stat field %d is %s %s, want %s %s", i, field.Name, field.Type, want.name, want.kind)
		}
	}
}

func TestSnapshotReportsCorrectAggregates(t *testing.T) {
	registry := agg.New()
	for i := 0; i < 20; i++ {
		registry.Observe("/v1/checkout", 900)
	}
	registry.Observe("/v1/checkout", 4000)
	registry.Observe("/v1/cart", 50)

	stat, ok := registry.Snapshot("/v1/checkout")
	if !ok {
		t.Fatal("Snapshot says /v1/checkout does not exist")
	}
	if stat.Count != 21 {
		t.Errorf("Count = %d, want 21", stat.Count)
	}
	if stat.TotalMicros != 22000 {
		t.Errorf("TotalMicros = %d, want 22000", stat.TotalMicros)
	}
	if stat.MaxMicros != 4000 {
		t.Errorf("MaxMicros = %d, want 4000", stat.MaxMicros)
	}
	if stat.MeanMicros() != 1047 {
		t.Errorf("MeanMicros = %d, want 1047", stat.MeanMicros())
	}
	// Twenty samples at 900 microseconds land in the bucket ending at 1024 and one at
	// 4000 lands in the bucket ending at 4096. The twentieth sample is the p95.
	if stat.P95Micros != 1024 {
		t.Errorf("P95Micros = %d, want 1024", stat.P95Micros)
	}
	if _, ok := registry.Snapshot("/v1/nothing-here"); ok {
		t.Error("Snapshot invented an endpoint that was never observed")
	}
	if registry.Observed() != 22 {
		t.Errorf("Observed = %d, want 22", registry.Observed())
	}
}

func TestSnapshotAllAgreesWithSnapshot(t *testing.T) {
	registry := agg.New()
	for i, name := range []string{"/a", "/b", "/c"} {
		for j := 0; j <= i; j++ {
			registry.Observe(name, int64(100*(i+1)))
		}
	}

	all := registry.SnapshotAll()
	names := registry.Endpoints()
	if !reflect.DeepEqual(names, []string{"/a", "/b", "/c"}) {
		t.Fatalf("Endpoints = %v, want sorted [/a /b /c]", names)
	}
	if len(all) != len(names) {
		t.Fatalf("SnapshotAll returned %d endpoints, Endpoints returned %d", len(all), len(names))
	}
	for _, name := range names {
		one, ok := registry.Snapshot(name)
		if !ok {
			t.Errorf("%s is in Endpoints but not in Snapshot", name)
			continue
		}
		if all[name] != one {
			t.Errorf("%s: SnapshotAll gave %+v, Snapshot gave %+v", name, all[name], one)
		}
	}
}

func TestResetClearsEveryEndpoint(t *testing.T) {
	registry := agg.New()
	registry.Observe("/v1/cart", 100)
	registry.Observe("/v1/cart", 200)
	registry.Reset()

	if registry.Observed() != 0 {
		t.Errorf("Observed = %d after Reset, want 0", registry.Observed())
	}
	if names := registry.Endpoints(); len(names) != 0 {
		t.Errorf("Endpoints = %v after Reset, want empty", names)
	}

	// The endpoint that was live when Reset ran is the one a cache in front of the map
	// gets wrong: it is still the most recently seen name, and its bucket is no longer
	// the bucket the registry reports.
	registry.Observe("/v1/cart", 700)
	if names := registry.Endpoints(); !reflect.DeepEqual(names, []string{"/v1/cart"}) {
		t.Fatalf("Endpoints = %v after observing again, want [/v1/cart]", names)
	}
	stat, ok := registry.Snapshot("/v1/cart")
	if !ok {
		t.Fatal("the endpoint observed after Reset is missing from the registry")
	}
	if stat.Count != 1 || stat.TotalMicros != 700 || stat.MaxMicros != 700 {
		t.Errorf("after Reset, /v1/cart = %+v, want one sample of 700 microseconds", stat)
	}
	if registry.Observed() != 1 {
		t.Errorf("Observed = %d, want 1", registry.Observed())
	}
}

// TestConcurrentReadsDoNotSerialise is the constraint that rules out one lock around
// everything. It passes in the unfixed workspace and fails on that shortcut.
func TestConcurrentReadsDoNotSerialise(t *testing.T) {
	const readers = 8

	registry := agg.New()
	for i := 0; i < 32; i++ {
		registry.Observe("/v1/checkout", 900)
	}
	registry.Snapshot("/v1/checkout") // pay for anything lazy before timing anything

	start := time.Now()
	registry.Snapshot("/v1/checkout")
	single := time.Since(start)

	var wg sync.WaitGroup
	start = time.Now()
	for i := 0; i < readers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, ok := registry.Snapshot("/v1/checkout"); !ok {
				t.Error("a concurrent Snapshot lost an endpoint that was observed")
			}
		}()
	}
	wg.Wait()
	together := time.Since(start)

	budget := single * readers / 3
	if budget < 5*time.Millisecond {
		budget = 5 * time.Millisecond
	}
	if together > budget {
		t.Fatalf("%d concurrent reads took %v, one read takes %v, budget is %v. Readers are "+
			"queueing behind each other, which is what an exclusive lock around the whole "+
			"registry does: the exporter scrapes every endpoint on every tick",
			readers, together, single, budget)
	}
}

func TestGoVetIsClean(t *testing.T) {
	if out, err := runGo(t, "vet", "./..."); err != nil {
		t.Fatalf("go vet ./... failed: %v\n%s", err, sanitise(out))
	}
}

// TestWorkspaceRaceSuitePasses is the headline check. The workspace ships a suite that
// reproduces the race, and it has to pass under -race without having been weakened.
func TestWorkspaceRaceSuitePasses(t *testing.T) {
	if out, err := runGo(t, "test", "-race", "-count=1", "./agg/"); err != nil {
		t.Fatalf("go test -race ./agg/ still fails: %v\n%s", err, sanitise(out))
	}
}

func TestConcurrentObserveLosesNoUpdates(t *testing.T) {
	const (
		writers = 8
		each    = 300
	)
	endpoints := []string{"/v1/cart", "/v1/checkout", "/v1/inventory", "/v1/orders"}

	registry := agg.New()
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			name := endpoints[worker%len(endpoints)]
			for i := 0; i < each; i++ {
				registry.Observe(name, int64(100+i))
			}
		}(w)
	}
	wg.Wait()

	if got, want := registry.Observed(), int64(writers*each); got != want {
		t.Errorf("Observed = %d, want %d. Increments were lost", got, want)
	}
	// Two writers per endpoint, each filing 100 through 399 once.
	const (
		wantCount = 2 * each
		wantTotal = 2 * (each * (100 + (100 + each - 1)) / 2)
		wantMax   = 100 + each - 1
	)
	for _, name := range endpoints {
		stat, ok := registry.Snapshot(name)
		if !ok {
			t.Errorf("%s was observed but is missing from the registry", name)
			continue
		}
		if stat.Count != wantCount {
			t.Errorf("%s: Count = %d, want %d", name, stat.Count, wantCount)
		}
		if stat.TotalMicros != wantTotal {
			t.Errorf("%s: TotalMicros = %d, want %d", name, stat.TotalMicros, wantTotal)
		}
		if stat.MaxMicros != wantMax {
			t.Errorf("%s: MaxMicros = %d, want %d", name, stat.MaxMicros, wantMax)
		}
	}
}

// TestScrapeWhileRequestsLand runs the two paths against each other. Under -race a single
// unsynchronised access on either side fails this test, and the assertions catch a
// snapshot torn across a concurrent write.
func TestScrapeWhileRequestsLand(t *testing.T) {
	const (
		writers = 6
		each    = 400
		scrapes = 12
	)

	registry := agg.New()
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			name := fmt.Sprintf("/v1/shard-%d", worker%3)
			for i := 0; i < each; i++ {
				registry.Observe(name, 250)
			}
		}(w)
	}

	wg.Add(1)
	go func() {
		defer wg.Done()
		var last int64
		for i := 0; i < scrapes; i++ {
			if seen := registry.Observed(); seen < last {
				t.Errorf("Observed went backwards, from %d to %d", last, seen)
			} else {
				last = seen
			}
			for name, stat := range registry.SnapshotAll() {
				if stat.Count*250 != stat.TotalMicros {
					t.Errorf("%s: Count %d and TotalMicros %d disagree, so the snapshot was "+
						"read while a write was halfway through", name, stat.Count, stat.TotalMicros)
				}
				if stat.MaxMicros != 250 {
					t.Errorf("%s: MaxMicros = %d, want 250", name, stat.MaxMicros)
				}
			}
		}
	}()
	wg.Wait()

	if got, want := registry.Observed(), int64(writers*each); got != want {
		t.Errorf("Observed = %d, want %d", got, want)
	}
	for shard := 0; shard < 3; shard++ {
		name := fmt.Sprintf("/v1/shard-%d", shard)
		stat, ok := registry.Snapshot(name)
		if !ok {
			t.Errorf("%s is missing from the registry", name)
			continue
		}
		if want := int64(writers * each / 3); stat.Count != want {
			t.Errorf("%s: Count = %d, want %d", name, stat.Count, want)
		}
	}
}

func goBin(t *testing.T) string {
	t.Helper()
	if path, err := exec.LookPath("go"); err == nil {
		return path
	}
	const fallback = "/usr/local/go/bin/go"
	if _, err := os.Stat(fallback); err == nil {
		return fallback
	}
	t.Fatal("no go toolchain on PATH and none at /usr/local/go/bin/go")
	return ""
}

func runGo(t *testing.T, args ...string) (string, error) {
	t.Helper()
	cmd := exec.Command(goBin(t), args...) // #nosec G204  fixed argument lists
	cmd.Dir = moduleRoot
	cmd.Env = append(os.Environ(), recurseGuard+"=1")
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// sanitise blanks the per test markers in captured child output. The harness counts those
// lines in this process's output to award partial credit, and quoting a child's markers
// into a failure message would have them counted twice.
func sanitise(out string) string {
	if len(out) > 3000 {
		out = out[len(out)-3000:]
	}
	return strings.ReplaceAll(out, "--- ", "child: ")
}
