"""Build a compact per-line test map from the pinned, unmodified SQLGlot suite."""
import argparse
import ast
from collections import defaultdict
import gzip
import json
import os
from pathlib import Path
import sys

import coverage
import pytest


class TestContexts:
    def __init__(self, tracker):
        self.tracker = tracker

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.tracker.switch_context(item.nodeid)
        try:
            yield
        finally:
            self.tracker.switch_context("")


def source_facts(checkout, mapping):
    symbols = defaultdict(int)
    scopes = {}
    for name, lines in mapping.items():
        try:
            tree = ast.parse((checkout / name).read_bytes())
        except SyntaxError:
            continue
        intervals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.isupper():
                for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    symbols[node.attr] |= int(lines.get(str(line), "0"), 16)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                mask = 0
                for line in range(node.lineno, node.end_lineno + 1):
                    mask |= int(lines.get(str(line), "0"), 16)
                if mask:
                    intervals.append([node.lineno, node.end_lineno, format(mask, "x")])
        if intervals:
            scopes[name] = sorted(intervals, key=lambda entry: entry[1] - entry[0])
    return {
        "symbols": {name: format(mask, "x") for name, mask in sorted(symbols.items()) if mask},
        "scopes": scopes,
    }


def write_map(output, payload):
    output.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))


def record(checkout, inventory, output):
    tests = json.loads(inventory.read_text())["tests"]
    indices = {test["id"]: index for index, test in enumerate(tests)}
    ids_by_path = defaultdict(set)
    for test in tests:
        ids_by_path[test["file"]].add(test["id"])
    os.chdir(checkout)
    sys.path.insert(0, str(checkout))
    os.environ.setdefault("SKIP_INTEGRATION", "0")
    tracker = coverage.Coverage(source=["sqlglot"], config_file=False, data_file=None)
    tracker.start()
    try:
        result = pytest.main(
            ["-q", "-o", "addopts=", "--disable-warnings", *sorted(ids_by_path)],
            plugins=[TestContexts(tracker)],
        )
    finally:
        tracker.stop()
    if int(result) not in {0, 1}:
        raise SystemExit(f"Coverage collection failed with pytest exit {int(result)}")
    data = tracker.get_data()
    mapping = {}
    observed = set()
    for filename in sorted(data.measured_files()):
        relative = Path(filename).resolve().relative_to(checkout).as_posix()
        lines = {}
        for line, contexts in data.contexts_by_lineno(filename).items():
            mask = 0
            for test in contexts:
                index = indices.get(test)
                if index is not None:
                    observed.add(test)
                    mask |= 1 << index
            if mask:
                lines[str(line)] = format(mask, "x")
        if lines:
            mapping[relative] = lines
    payload = {"ids": list(indices), "lines": mapping, "unobserved": sorted(set(indices) - observed)}
    payload.update(source_facts(checkout, mapping))
    write_map(output, payload)
    print(json.dumps({"mapped_tests": len(observed), "inventory_tests": len(tests),
                      "files": len(mapping), "map_bytes": output.stat().st_size}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--augment", type=Path, help="Add static symbol information to an existing line map.")
    args = parser.parse_args()
    if args.augment:
        with gzip.open(args.augment, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        seen = 0
        for lines in payload["lines"].values():
            for mask in lines.values():
                seen |= int(mask, 16)
        payload["unobserved"] = [name for i, name in enumerate(payload["ids"]) if not (seen & (1 << i))]
        payload.update(source_facts(args.checkout.resolve(), payload["lines"]))
        write_map(args.output, payload)
    else:
        record(args.checkout.resolve(), args.inventory.resolve(), args.output.resolve())
