"""Prove source-only comment edits by checking both reconstructed ASTs."""
import ast
import re

HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
ORDINARY_COMMENT = re.compile(r"^\s*#\s+[A-Za-z]")
DIRECTIVE = re.compile(r"\b(?:type|noqa|pragma|fmt|ruff|pylint|doctest|encoding|coding|mypy)\b", re.I)


def comment_only_files(patch):
    eligible = {}
    file = None
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            file = line.partition(" b/")[2]
            in_hunk = False
            eligible[file] = []
        elif HEADER.match(line):
            in_hunk = True
        elif file is not None and eligible[file] is not None:
            if not in_hunk:
                if line.startswith(("old mode", "new mode", "new file", "deleted file", "rename", "copy")):
                    eligible[file] = None
            elif line.startswith(("+", "-")):
                content = line[1:]
                if not ORDINARY_COMMENT.match(content) or DIRECTIVE.search(content):
                    eligible[file] = None
                else:
                    eligible[file].append(content)
    return {file for file, edits in eligible.items() if edits}


def apply_file_patch(source, patch, wanted):
    original = source.splitlines()
    current = None
    active = False
    result = []
    cursor = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current = line.partition(" b/")[2]
            active = False
        elif current == wanted and (match := HEADER.match(line)):
            start = int(match[1])
            index = start if match[2] == "0" else start - 1
            if index < cursor or index > len(original):
                return None
            result.extend(original[cursor:index])
            cursor = index
            active = True
        elif current == wanted and active:
            if line.startswith((" ", "-")):
                if cursor >= len(original) or original[cursor] != line[1:]:
                    return None
                cursor += 1
            if line.startswith((" ", "+")):
                result.append(line[1:])
    return "\n".join(result + original[cursor:]) + "\n"


def equivalent_comment_edits(patch, sources):
    for file in comment_only_files(patch) & sources.keys():
        before = sources[file]
        after = apply_file_patch(before, patch, file)
        if after is None:
            continue
        try:
            if ast.dump(ast.parse(before, type_comments=True)) == ast.dump(ast.parse(after, type_comments=True)):
                yield file
        except SyntaxError:
            continue
