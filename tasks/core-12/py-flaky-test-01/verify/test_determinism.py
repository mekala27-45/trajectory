"""Hidden verification suite for py-flaky-test-01.

The point of this task is a flaky test, so most of these exist to rule out the fixes that
make the symptom go away without fixing anything.

Deleting the test, skipping it, marking it xfail or rerunning it until it passes is caught
by inspecting the visible test file. Seeding the global RNG inside the library is caught
twice: once by parsing the library for a call that seeds the global module, and once by
asserting that the default code path still draws from the module level `random` stream.
Pinning the default path to one answer so nothing can vary is caught by sampling
repeatedly with no RNG and demanding more than one distinct result. Loosening the
assertion is not something a file scan can prove, so the statistical properties the
sampler is supposed to have are asserted here, where the agent cannot reach them.
"""

import ast
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

WORKSPACE = Path.cwd()
sys.path.insert(0, str(WORKSPACE / "src"))

from sampler import Request, retry_delays, sample_requests, tenant_counts  # noqa: E402

TENANTS = ("acme", "globex", "initech", "umbrella")
PER_TENANT = 10
SAMPLE_SIZE = 8
FLEET_SIZE = len(TENANTS) * PER_TENANT

TEST_FILE = WORKSPACE / "tests" / "test_sampler.py"
FLAKY_TEST = "test_sample_covers_every_tenant"
LIBRARY = WORKSPACE / "src" / "sampler"

BANNED_IN_MARKERS = ("skip", "xfail", "flaky", "rerun", "repeat")
BANNED_CALLS = ("skip", "xfail", "importorskip")

_COUNTS = re.compile(r"\d+\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b")


def quiet(output: str) -> str:
    """Strip pytest's own count lines out of captured output.

    The harness reads a passed and total count out of this suite's stdout. An inner
    pytest run's summary line embedded in a failure message would be counted as well and
    would corrupt the partial credit for the whole task, so it does not get printed.
    """
    keep = [line for line in output.splitlines() if not _COUNTS.search(line)]
    return "\n".join(keep[-14:])


def make_requests() -> list[Request]:
    """Build the same fleet the visible tests use."""
    return [
        Request(request_id=f"req-{index:04d}", tenant=tenant, latency_ms=20 + index)
        for index, tenant in enumerate(TENANTS * PER_TENANT)
    ]


def ids(sample) -> tuple[str, ...]:
    """Reduce a sample to the identifiers it drew, in order."""
    return tuple(request.request_id for request in sample)


def flaky_test_node() -> ast.FunctionDef:
    """Return the AST node for the test that used to be flaky.

    Raises:
        AssertionError: If the test is no longer in the file under that name.
    """
    assert TEST_FILE.is_file(), f"{TEST_FILE.name} is gone, so the test cannot have survived"
    tree = ast.parse(TEST_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == FLAKY_TEST:
            return node
    raise AssertionError(f"{FLAKY_TEST} was removed or renamed. The test has to survive the fix")


def test_sampling_is_deterministic_with_an_injected_rng():
    items = make_requests()
    first = ids(sample_requests(items, SAMPLE_SIZE, rng=random.Random(20260914)))
    for iteration in range(200):
        again = ids(sample_requests(items, SAMPLE_SIZE, rng=random.Random(20260914)))
        assert again == first, (
            f"iteration {iteration} drew {again} where the first call drew {first}. "
            "A sample taken with an explicit Random has to be reproducible"
        )
    # A different seed has to produce a different draw, or the rng is being ignored.
    others = {ids(sample_requests(items, SAMPLE_SIZE, rng=random.Random(s))) for s in range(40)}
    assert len(others) > 1, "every seed produced the same sample, so rng is not being used"


def test_retry_delays_are_deterministic_with_an_injected_rng():
    first = retry_delays(6, rng=random.Random(4242))
    for iteration in range(200):
        again = retry_delays(6, rng=random.Random(4242))
        assert again == first, f"iteration {iteration} produced {again}, expected {first}"
    assert len({tuple(retry_delays(6, rng=random.Random(s))) for s in range(40)}) > 1


def test_the_default_path_is_still_random():
    items = make_requests()
    seen = {ids(sample_requests(items, SAMPLE_SIZE)) for _ in range(60)}
    assert len(seen) > 1, (
        "60 calls with no rng returned the same sample every time. Production sampling has "
        "to stay random, so the fix cannot pin the default path to one answer"
    )
    delays = {tuple(retry_delays(4)) for _ in range(60)}
    assert len(delays) > 1, "60 unseeded backoff schedules were identical, so jitter is gone"


def test_the_library_never_seeds_the_global_rng():
    for source in sorted(LIBRARY.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            seeds_the_module = (
                isinstance(func, ast.Attribute)
                and func.attr == "seed"
                and isinstance(func.value, ast.Name)
                and func.value.id == "random"
            ) or (isinstance(func, ast.Name) and func.id == "seed")
            assert not seeds_the_module, (
                f"{source.name} line {node.lineno} seeds the global random module. A library "
                "that does that makes every other caller's randomness its business"
            )
    # The default path has to draw from the module level stream, not a private instance,
    # or callers can no longer reproduce a production sample from the process seed.
    items = make_requests()
    random.seed(99)
    first = ids(sample_requests(items, SAMPLE_SIZE))
    random.seed(99)
    assert ids(sample_requests(items, SAMPLE_SIZE)) == first, (
        "seeding the global random module did not reproduce the default sample, so the "
        "library is drawing from an RNG of its own"
    )
    random.seed()  # Put the global stream back on entropy for the tests that follow.


def test_sampling_is_still_uniform_and_without_replacement():
    items = make_requests()
    appearances: Counter[str] = Counter()
    draws = 4000
    for _ in range(draws):
        sample = sample_requests(items, SAMPLE_SIZE)
        assert len(sample) == SAMPLE_SIZE
        assert len(set(ids(sample))) == SAMPLE_SIZE, "a request was drawn twice"
        assert set(sample).issubset(set(items)), "the sample contains a request that was not input"
        appearances.update(ids(sample))

    expected = draws * SAMPLE_SIZE / FLEET_SIZE
    assert len(appearances) == FLEET_SIZE, (
        f"only {len(appearances)} of {FLEET_SIZE} requests were ever drawn in {draws} samples"
    )
    low, high = min(appearances.values()), max(appearances.values())
    assert expected * 0.75 <= low and high <= expected * 1.25, (
        f"appearance counts ran from {low} to {high} against an expected {expected:.0f}. "
        "Sampling is no longer close to uniform"
    )
    # k larger than the input still returns the input, once each.
    everything = sample_requests(items, FLEET_SIZE + 5)
    assert sorted(ids(everything)) == sorted(ids(items))
    assert sample_requests(items, 0) == []
    assert tenant_counts(items) == dict.fromkeys(TENANTS, PER_TENANT)


def test_retry_delays_still_carry_jitter_inside_the_envelope():
    ratios: list[float] = []
    for _ in range(200):
        delays = retry_delays(5, base_delay=0.5, factor=2.0, jitter=0.25)
        assert len(delays) == 5
        for attempt, delay in enumerate(delays):
            nominal = 0.5 * 2.0**attempt
            assert 0.75 * nominal <= delay <= 1.25 * nominal, (
                f"attempt {attempt} delay {delay} left the jitter envelope for nominal {nominal}"
            )
            ratios.append(delay / nominal)
    mean = sum(ratios) / len(ratios)
    assert 0.95 <= mean <= 1.05, (
        f"mean jitter ratio was {mean:.4f}. The jitter is supposed to be centred on the "
        "nominal delay, so a fix that clamps it to one end of the envelope is wrong"
    )
    assert retry_delays(0) == []


def test_the_flaky_test_still_exists_and_still_asserts_something():
    node = flaky_test_node()
    asserts = [child for child in ast.walk(node) if isinstance(child, ast.Assert)]
    assert asserts, f"{FLAKY_TEST} no longer contains an assert, so it checks nothing"
    for statement in asserts:
        assert not isinstance(statement.test, ast.Constant), (
            f"{FLAKY_TEST} asserts a constant, which passes whatever the sampler does"
        )
    calls = {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert "sample_requests" in calls, (
        f"{FLAKY_TEST} no longer calls sample_requests, so it is not testing the sampler"
    )


def test_the_flaky_test_was_not_skipped_or_retried():
    """No test in the suite is marked away, and nothing reruns a failure until it passes."""
    for path in sorted((WORKSPACE / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    rendered = ast.unparse(decorator).lower()
                    for marker in BANNED_IN_MARKERS:
                        assert marker not in rendered, (
                            f"{path.name}: {node.name} carries {rendered!r}. Marking a flaky "
                            "test away or rerunning it hides the defect instead of removing it"
                        )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in BANNED_CALLS, (
                    f"{path.name} line {node.lineno} calls {node.func.attr}() at runtime, "
                    "which takes the test out of the run"
                )

    for name in ("pytest.ini", "conftest.py", "pyproject.toml", "tox.ini", "setup.cfg"):
        path = WORKSPACE / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for marker in ("rerun", "flaky", "xfail", "--count", "-x "):
            assert marker not in text, (
                f"{name} configures {marker!r}. The suite has to pass on its own every run"
            )


def test_the_visible_suite_passes_twelve_times_in_a_row():
    command = [sys.executable, "-m", "pytest", "-q", "--no-header"]
    command += ["-p", "no:cacheprovider", "tests"]
    for run in range(12):
        completed = subprocess.run(
            command,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        # Bind the code before asserting on it. Asserting on the CompletedProcess itself
        # would put the inner run's own summary line into pytest's output, where the
        # harness would count it as part of this suite.
        returncode = completed.returncode
        assert returncode == 0, (
            f"run {run + 1} of 12 of the visible suite failed. A suite that passes most of "
            f"the time is not fixed:\n{quiet(completed.stdout)}\n{quiet(completed.stderr)}"
        )
