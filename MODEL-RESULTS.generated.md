# Results: model

- Harness version: `0.1.1`
- Runs: 12 (4 solved, 8 unsolved)
- Models: nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b
- Tasks: 12
- Seeds: [0]
- Backends: docker
- Total provider spend: 0.0000 USD
- Total wall clock: 3033s

## Leaderboard

Every cell is a mean across seeds with one population standard deviation. A pass rate without that spread is not a result.

| model | runs | solve rate | partial credit | step efficiency | tool validity | redundancy | recovery | premature finish | cost per solved | mean wall clock |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 12 | 33.3% +/- 0.0% | 0.746 +/- 0.000 | 0.500 +/- 0.000 | 0.980 +/- 0.000 | 0.253 +/- 0.000 | 1.000 +/- 0.000 | 25.0% +/- 0.0% | n/a | 252.8s |

## Failure modes

Share is of the 8 unsolved run(s), not of all runs.

| id | mode | runs | share of unsolved |
| --- | --- | --- | --- |
| F03 | premature success | 3 | 37.5% |
| F01 | tool schema violation | 2 | 25.0% |
| F04 | retry loop | 2 | 25.0% |
| F08 | destructive action | 1 | 12.5% |
| F09 | scope creep | 1 | 12.5% |

## Failure modes on runs that passed

This is the view a pass rate cannot produce. A run that made malformed tool calls, invented paths, or reached for a destructive command and still got the hidden tests green is a process problem that succeeded, and it is invisible in any aggregate that only looks at failures.

Share is of the 4 solved run(s).

| id | mode | runs | share of solved |
| --- | --- | --- | --- |
| F01 | tool schema violation | 1 | 25.0% |
| F02 | path hallucination | 1 | 25.0% |
