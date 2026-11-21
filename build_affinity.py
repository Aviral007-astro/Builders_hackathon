"""Derive dialect and fixture affinities from the pinned SQLGlot checkout."""
import ast
from collections import defaultdict
import gzip
import json
from pathlib import Path
import re
import sys

from selector import DIALECT_DIRS

checkout, source, destination = map(Path, sys.argv[1:])
with gzip.open(source, "rt") as stream:
    model = json.load(stream)
affinity = defaultdict(int)
readers = defaultdict(set)
fixtures = defaultdict(list)
for path in (checkout / "tests/fixtures").rglob("*.sql"):
    fixtures[path.stem].append(path)
fixture_dialects = re.compile(r"(?im)^\s*#\s*dialect:\s*([a-z0-9_]+)")
for path, lines in model["lines"].items():
    module = Path(path)
    if module.parent.name in DIALECT_DIRS and module.stem not in {"dialect", "__init__"}:
        for mask in lines.values():
            affinity[module.stem] |= int(mask, 16)

by_file = defaultdict(list)
for index, test in enumerate(model["ids"]):
    parts = test.split("::")
    by_file[parts[0]].append((index, test, parts[-1].split("[")[0]))
for file, tests in by_file.items():
    path = checkout / file
    if not path.is_file():
        continue
    functions = defaultdict(set)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            functions[node.name].update(
                value.value for value in ast.walk(node)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
    for index, test, method in tests:
        strings = functions[method] | {method.removeprefix("test_")}
        for value in strings:
            if value in affinity:
                affinity[value] |= 1 << index
            for fixture in fixtures.get(value, ()):
                readers[fixture.relative_to(checkout).as_posix()].add(test)
                for dialect in fixture_dialects.findall(fixture.read_text(encoding="utf-8", errors="replace")):
                    affinity[dialect] |= 1 << index
model["affinity"] = {key: format(mask, "x") for key, mask in affinity.items()}
model["fixtures"] = {key: sorted(ids) for key, ids in readers.items()}
model["sources"] = {
    path.relative_to(checkout).as_posix(): path.read_text(encoding="utf-8")
    for path in sorted((checkout / "sqlglot").rglob("*.py"))
}
with gzip.open(destination, "wt") as stream:
    json.dump(model, stream, separators=(",", ":"))
