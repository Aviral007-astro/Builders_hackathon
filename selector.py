"""Choose SQLGlot regression tests using coverage from the pinned checkout."""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path, PurePosixPath
import re


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
DIALECT_DIRS = {"dialects", "generators", "parsers", "typing", "tokenizers"}
ATTRIBUTE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*(?::[^=]+)?\s*=")
CONTROL_FLOW = re.compile(r"\b(?:if|elif|and|or|not|while|except|raise)\b")
BOOLEAN_OPERATOR = re.compile(r"\b(?:and|or|not)\b")


def parse_changes(text):
    changes = defaultdict(set)
    additions = defaultdict(set)
    symbols = defaultdict(set)
    branches = defaultdict(set)
    deleted_operators = defaultdict(Counter)
    added_operators = defaultdict(Counter)
    file = None
    old = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            header = line.partition(" b/")[2]
            file = header or None
            old = None
            if file:
                changes[file]
        elif (match := HUNK.match(line)):
            old = int(match[1])
        elif file is not None and old is not None:
            if line.startswith(("-", "+")):
                if symbol := ATTRIBUTE.match(line[1:]):
                    symbols[file].add(symbol[1])
                if CONTROL_FLOW.search(line[1:]):
                    branches[file].add(old)
                operators = BOOLEAN_OPERATOR.findall(line[1:])
                target = deleted_operators if line.startswith("-") else added_operators
                target[file].update(operators)
            if line.startswith("-"):
                changes[file].add(old)
                old += 1
            elif line.startswith("+"):
                additions[file].add(old)
            elif line.startswith(" "):
                old += 1
    changed_logic = {file for file in changes if deleted_operators[file] != added_operators[file]}
    return changes, additions, symbols, branches, changed_logic


class Selector:
    def __init__(self, tests, model, conservative):
        self.tests = tests
        self.all_numbers = {test["number"] for test in tests}
        self.coverage = model["lines"]
        self.number_by_id = {test["id"]: test["number"] for test in tests}
        self.model_numbers = tuple(self.number_by_id.get(test) for test in model["ids"])
        self.symbol_coverage = model.get("symbols", {})
        self.scopes = model.get("scopes", {})
        self.conservative = conservative
        self.unobserved = {self.number_by_id[test] for test in model.get("unobserved", [])
                           if test in self.number_by_id}
        self.by_file = defaultdict(set)
        self.named = defaultdict(set)
        for test in tests:
            self.by_file[test["file"]].add(test["number"])
            self.named[test["id"].split("::")[-1].split("[")[0]].add(test["number"])
        self.optimizers = self.by_file["tests/test_optimizer.py"]
        self.integration = self.optimizers | self.by_file["tests/test_executor.py"]

    def numbers(self, mask):
        chosen = set()
        while mask:
            bit = mask & -mask
            index = bit.bit_length() - 1
            number = self.model_numbers[index]
            if number is not None:
                chosen.add(number)
            mask ^= bit
        return chosen

    def match_fixture(self, path):
        stem = PurePosixPath(path).stem
        if result := self.named.get("test_" + stem):
            return result
        stem = stem.split("__")[0]
        while stem:
            if result := self.named.get("test_" + stem):
                return result
            stem = stem.rpartition("_")[0]
        directory = PurePosixPath(path).parent.name
        test_file = "tests/test_" + directory + ".py"
        return self.by_file.get(test_file) or self.all_numbers

    def test_source(self, path, old, added, symbols, branches):
        part = PurePosixPath(path)
        stem = part.stem
        lines = self.coverage.get(path, {})
        mask = 0
        for line in old:
            mask |= int(lines.get(str(line), "0"), 16)
        for line in added:
            for nearby in range(line - 2, line + 3):
                mask |= int(lines.get(str(nearby), "0"), 16)
        for symbol in symbols:
            mask |= int(self.symbol_coverage.get(symbol, "0"), 16)
        for line in branches:
            for start, end, bits in self.scopes.get(path, ()):
                if start <= line <= end:
                    mask |= int(bits, 16)
                    break
        dialect = part.parent.name in DIALECT_DIRS and stem not in {"dialect", "__init__"}
        if dialect:
            candidates = self.numbers(mask)
            return candidates | self.by_file.get(f"tests/dialects/test_{stem}.py", set()) or self.all_numbers
        if mask and (covered := self.numbers(mask)):
            return covered
        direct = self.by_file.get(f"tests/test_{stem}.py")
        if direct:
            return direct
        if lines:
            for bits in lines.values():
                mask |= int(bits, 16)
            return self.numbers(mask) or self.all_numbers
        return self.all_numbers

    def select(self, patch):
        changes, additions, symbols, branches, changed_logic = parse_changes(patch)
        chosen = set()
        for file, lines in changes.items():
            if file.startswith("tests/fixtures/"):
                chosen.update(self.match_fixture(file))
            elif file in self.by_file:
                chosen.update(self.by_file[file])
            elif file.startswith("sqlglot/") and file.endswith(".py"):
                chosen.update(self.test_source(file, lines, additions[file], symbols[file], branches[file]))
                if self.conservative or file in changed_logic:
                    chosen.update(self.integration)
                elif symbols[file]:
                    chosen.update(self.optimizers)
                chosen.update(self.unobserved)
            else:
                chosen.update(self.all_numbers)
        return chosen if changes else self.all_numbers


def balance(tests):
    ordered = sorted(tests, key=lambda test: (-test["seconds"], test["number"]))
    if not ordered:
        return []
    best = None
    for n in range(1, min(8, len(ordered)) + 1):
        loads = [0.0] * n
        lanes = [[] for _ in loads]
        for test in ordered:
            index = loads.index(min(loads))
            loads[index] += test["seconds"]
            lanes[index].append(test["number"])
        score = max(loads) + 2 * n
        if best is None or score < best[0]:
            best = score, lanes
    return best[1]


def main(data, model, conservative):
    tests = json.loads((data / "tests.json").read_text())["tests"]
    by_number = {test["number"]: test for test in tests}
    with gzip.open(model, "rt", encoding="utf-8") as stream:
        selector = Selector(tests, json.load(stream), conservative)
    for patch in sorted((data / "commits").glob("*.patch")):
        chosen = selector.select(patch.read_text(encoding="utf-8", errors="replace"))
        workers = balance([by_number[number] for number in chosen])
        print(json.dumps({"commit": patch.stem, "workers": workers}, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--map", type=Path, default=Path(__file__).with_name("coverage-map.json.gz"))
    parser.add_argument("--conservative", action="store_true")
    args = parser.parse_args()
    main(args.data, args.map, args.conservative)
