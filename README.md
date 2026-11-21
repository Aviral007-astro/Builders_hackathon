# RWX Blast Radius

The selector uses test coverage collected on SQLGlot commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. It reads the scored inventory
and unified diffs at runtime. The coverage map stores test node IDs and
line coverage from the unmodified checkout. It contains no challenge
patches or answer labels.

The selector balances the tests for each patch across one to eight
workers and picks the layout with the lowest predicted cost, including
the two-second charge per worker.

Line coverage supplies the primary selection. Static references broaden
coverage when a dialect setting changes, and function-level coverage
helps with control-flow edits. Boolean operator changes also select
optimizer and executor tests as a safeguard.

## Public sample result

The official scorer reported 293.884 simulated seconds and zero missed
commits across the 30 public patches. The official sample starter scored
1,210.470 simulated seconds. Docker produced the same output as the direct
VM run under the scorer's user, network, memory and read-only disk settings.
The local score excludes selector CPU and does not predict the unseen
practice or final score.

The coverage-build run observed 1,615 of the 1,620 scored test IDs.
One lazy-load test failed with a file lookup error in the coverage
environment. The selector retains tests without observed coverage for
library code changes.

## Verification

Run the official local scorer against the public fixtures.

```sh
python selector.py --data /path/to/sample > output.jsonl
python /path/to/starter/score.py /path/to/sample /path/to/sample/answers.json output.jsonl
```

The Docker entry point uses `/data` and writes one JSON line per patch.
`build_coverage.py` regenerates the map from the pinned checkout and
public test inventory. Coverage instrumentation runs during preparation.
The container uses only Python's standard library.
