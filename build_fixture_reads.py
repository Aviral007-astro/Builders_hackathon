"""Record actual fixture opens during the unmodified, pinned SQLGlot tests."""
import argparse
import builtins
from collections import defaultdict
import gzip
import io
import json
import os
from pathlib import Path
import sys

import pytest


class FixtureReads:
    def __init__(self, checkout):
        self.checkout = checkout
        self.root = checkout / "tests" / "fixtures"
        self.readers = defaultdict(set)
        self.current = None

    def capture(self, open_file):
        def observed(path, *args, **kwargs):
            if self.current and isinstance(path, (str, bytes, os.PathLike)):
                name = os.fsdecode(path)
                if "fixtures" in name:
                    absolute = Path(os.path.abspath(name))
                    if absolute.is_relative_to(self.root):
                        key = absolute.relative_to(self.checkout).as_posix()
                        self.readers[key].add(self.current)
            return open_file(path, *args, **kwargs)
        return observed

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item):
        self.current = item.nodeid
        try:
            yield
        finally:
            self.current = None


def record(checkout, inventory):
    tests = json.loads(inventory.read_text())["tests"]
    valid_tests = {test["id"] for test in tests}
    os.chdir(checkout)
    sys.path.insert(0, str(checkout))
    os.environ["SKIP_INTEGRATION"] = "0"
    fixture_reads = FixtureReads(checkout)
    builtin_open, io_open = builtins.open, io.open
    try:
        builtins.open = fixture_reads.capture(builtin_open)
        io.open = fixture_reads.capture(io_open)
        status = pytest.main(
            ["-q", "-o", "addopts=", "--disable-warnings",
             *sorted({test["file"] for test in tests})],
            plugins=[fixture_reads],
        )
    finally:
        builtins.open, io.open = builtin_open, io_open
    if status not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        raise SystemExit(status)
    readers = {path: sorted(ids & valid_tests) for path, ids in sorted(fixture_reads.readers.items())}
    return {path: ids for path, ids in readers.items() if ids}

def augment(base, destination, readers):
    with gzip.open(base, "rt", encoding="utf-8") as source:
        model = json.load(source)
    for path, ids in sorted(readers.items()):
        original = model["fixtures"].get(path, [])
        model["fixtures"][path] = sorted(set(original) | set(ids))
    payload = json.dumps(model, separators=(",", ":")).encode()
    destination.write_bytes(gzip.compress(payload, mtime=0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-map", type=Path)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    base_map = arguments.base_map.resolve() if arguments.base_map else None
    readers = record(arguments.checkout.resolve(), arguments.inventory.resolve())
    if base_map:
        augment(base_map, output, readers)
    else:
        output.write_text(json.dumps(readers, separators=(",", ":")), encoding="utf-8")
    print(f"Recorded fixture readers for {len(readers)} fixtures.")
