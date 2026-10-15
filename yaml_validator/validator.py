"""
yaml_validator/validator.py — Core YAML parsing and validation engine.

Provides:
  - parse_yaml()     — safe YAML load with detailed error reporting
  - validate_file()  — syntax + optional schema validation for a YAML file
  - ValidationResult — typed result with errors, warnings, metadata
  - fix_content() / fix_file() — safe auto-fix for tabs, trailing spaces, EOF newline
"""

import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

import yaml
from yaml.error import MarkedYAMLError


# ── Types ────────────────────────────────────────────────────────────────────


@dataclass
class ValidationError:
    """A single validation finding."""

    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    severity: str = "error"  # "error" | "warning" | "info"
    rule: str = ""

    def __str__(self) -> str:
        loc = ""
        if self.line is not None:
            loc = f"line {self.line}"
            if self.column is not None:
                loc += f":{self.column}"
            loc += ": "
        level = self.severity.upper()
        return f"[{level}] {loc}{self.message}"


@dataclass
class ValidationResult:
    """Outcome of validating one YAML file."""

    path: str
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    document_count: int = 0
    data: Optional[object] = None

    @property
    def summary(self) -> str:
        parts = [f"{'✓' if self.is_valid else '✗'} {self.path}"]
        if self.document_count > 1:
            parts[0] += f" ({self.document_count} documents)"
        if self.errors:
            parts.append(f"   {len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"   {len(self.warnings)} warning(s)")
        return "\n".join(parts)


# ── Parsing ──────────────────────────────────────────────────────────────────


def parse_yaml(
    content: str,
    source: str = "<string>",
    multi_document: bool = False,
) -> Union[object, List[object], "ValidationResult"]:
    """Parse YAML content and return data or a ValidationResult on failure.

    Args:
        content: Raw YAML string.
        source: Label used in error messages (file path or "<string>").
        multi_document: If True, accept YAML with multiple documents (``---``
            separators). Returns ``list`` of parsed documents.

    Returns:
        Parsed Python object (or list of objects if multi_document=True),
        or a ValidationResult with parse errors.
    """
    try:
        if multi_document:
            docs = list(yaml.safe_load_all(content))
            return docs
        return yaml.safe_load(content)
    except MarkedYAMLError as e:
        line = None
        column = None
        if e.problem_mark is not None:
            line = e.problem_mark.line + 1  # PyYAML is 0-indexed
            column = e.problem_mark.column + 1
        msg = str(e.problem) if e.problem else str(e)
        return ValidationResult(
            path=source,
            is_valid=False,
            errors=[
                ValidationError(
                    message=msg,
                    line=line,
                    column=column,
                    rule="yaml-syntax",
                )
            ],
        )
    except yaml.YAMLError as e:
        return ValidationResult(
            path=source,
            is_valid=False,
            errors=[
                ValidationError(
                    message=str(e),
                    rule="yaml-syntax",
                )
            ],
        )


# ── Rules ────────────────────────────────────────────────────────────────────


def _check_trailing_spaces(
    content: str, source: str, errors: List[ValidationError]
) -> None:
    """Flag lines with trailing whitespace."""
    for i, line in enumerate(content.split("\n"), start=1):
        if line.rstrip() != line and line.strip():
            errors.append(
                ValidationError(
                    message="Trailing whitespace",
                    line=i,
                    severity="warning",
                    rule="trailing-spaces",
                )
            )


def _check_line_length(
    content: str, source: str, errors: List[ValidationError], max_len: int = 200
) -> None:
    """Flag lines exceeding *max_len* characters."""
    for i, line in enumerate(content.split("\n"), start=1):
        if len(line) > max_len:
            errors.append(
                ValidationError(
                    message=f"Line too long ({len(line)} > {max_len} chars)",
                    line=i,
                    severity="warning",
                    rule="line-length",
                )
            )


def _check_tab_indentation(
    content: str, source: str, errors: List[ValidationError]
) -> None:
    """Flag lines using tab indentation (invalid in YAML indentation)."""
    for i, line in enumerate(content.split("\n"), start=1):
        if line.startswith("\t") and line.strip():
            errors.append(
                ValidationError(
                    message="Tab character used for indentation (YAML requires spaces)",
                    line=i,
                    severity="error",
                    rule="no-tabs",
                )
            )


def _check_missing_newline(
    content: str, source: str, errors: List[ValidationError]
) -> None:
    """Warn if file doesn't end with a newline."""
    if content and not content.endswith("\n"):
        errors.append(
            ValidationError(
                message="File does not end with a newline",
                line=content.count("\n") + 1,
                severity="warning",
                rule="end-of-file-newline",
            )
        )


def _detect_tab_vs_space(
    data: object, errors: List[ValidationError], path: str = "$"
) -> None:
    """Recursively check for mixed indentation in nested structures
    (spot-check data values for unexpected tabs)."""
    if isinstance(data, dict):
        for key, val in data.items():
            key_str = str(key)
            if "\t" in key_str:
                errors.append(
                    ValidationError(
                        message=f"Tab character in key at {path}.{key_str}",
                        severity="error",
                        rule="no-tabs",
                    )
                )
            _detect_tab_vs_space(val, errors, f"{path}.{key_str}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _detect_tab_vs_space(item, errors, f"{path}[{i}]")


# ── Auto-fix ────────────────────────────────────────────────────────────────


@dataclass
class FixResult:
    """Outcome of auto-fixing one file's content."""

    content: str
    fixed: Dict[str, int] = field(default_factory=dict)  # rule -> issues fixed
    skipped: Dict[str, str] = field(default_factory=dict)  # rule -> reason

    @property
    def total(self) -> int:
        return sum(self.fixed.values())


def _fix_tab_line(line: str, tab_width: int) -> str:
    """Replace tabs in the leading indentation of a tab-indented line."""
    if line.startswith("\t") and line.strip():
        indent = len(line) - len(line.lstrip(" \t"))
        return line[:indent].replace("\t", " " * tab_width) + line[indent:]
    return line


def _fix_trailing_line(line: str) -> str:
    return line.rstrip() if line.strip() else line


def _load_all(content: str):
    """Return ``(ok, documents)``; ``ok`` is False if *content* does not parse."""
    try:
        return True, list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return False, None


def _same_data(a, b) -> bool:
    """Compare parsed YAML, coping with NaN and self-referential aliases."""
    try:
        if a == b:
            return True
    except RecursionError:  # e.g. ``&a [*a]`` loads as a recursive list
        pass
    try:
        return yaml.safe_dump_all(a) == yaml.safe_dump_all(b)
    except (yaml.YAMLError, RecursionError):
        return False


def _with_edits(lines: List[str], edits: Dict[int, str]) -> str:
    out = list(lines)
    for i, new in edits.items():
        out[i] = new
    return "\n".join(out)


def _keep_data_preserving(
    lines: List[str], edits: Dict[int, str], data
) -> Dict[int, str]:
    """Return the subset of line *edits* that keeps the parsed data equal to *data*.

    Edits are tried in bisected batches, so a document with k unsafe lines
    costs O(k log n) parses rather than one parse per line.
    """
    accepted: Dict[int, str] = {}

    def visit(group: List[int]) -> None:
        trial = dict(accepted)
        trial.update({i: edits[i] for i in group})
        ok, parsed = _load_all(_with_edits(lines, trial))
        if ok and _same_data(data, parsed):
            accepted.update({i: edits[i] for i in group})
        elif len(group) > 1:
            mid = len(group) // 2
            visit(group[:mid])
            visit(group[mid:])

    visit(sorted(edits))
    return accepted


def _revert_optional_edits(
    tab_lines: List[str],
    trimmed: Dict[int, str],
    add_eof: bool,
    data,
) -> "tuple[List[str], bool]":
    """Undo optional edits that changed the data of a repaired document.

    Used when the original does not parse. *tab_lines* has the required
    tab repairs applied; *trimmed* maps line index to its trailing-space
    trim and *add_eof* says whether a final newline is added. Together they
    parse to *data*. A trim or the EOF newline is reverted when the file
    still parses without it, which means it wasn't needed to parse and it
    changed a value (e.g. spaces inside a ``|`` block scalar). Each edit is
    tracked separately, so a trim can be undone while the tab repair on the
    same line stays. Bisected like :func:`_keep_data_preserving`.
    """
    units = sorted(trimmed) + (["eof"] if add_eof else [])
    reverted: set = set()
    state = {"data": data}

    def render(extra) -> str:
        undo = reverted | set(extra)
        lines = [
            ln if i in undo or i not in trimmed else trimmed[i]
            for i, ln in enumerate(tab_lines)
        ]
        text = "\n".join(lines)
        if add_eof and "eof" not in undo:
            text += "\n"
        return text

    def visit(group) -> None:
        ok, parsed = _load_all(render(group))
        if not ok and len(group) == 1:
            return  # needed to make the file parse
        if ok and _same_data(state["data"], parsed):
            return  # only cosmetic edits in this group: keep them
        if len(group) == 1:
            reverted.add(group[0])
            state["data"] = parsed
            return
        mid = len(group) // 2
        visit(group[:mid])
        visit(group[mid:])

    if units:
        visit(units)
    return render(()), add_eof and "eof" not in reverted


def _count(check, content: str) -> int:
    found: List[ValidationError] = []
    check(content, "", found)
    return len(found)


def fix_content(
    content: str, tab_width: int = 2, fix_trailing_spaces: bool = True
) -> FixResult:
    """Fix tab indentation, trailing whitespace and a missing EOF newline.

    Edits are line-based text edits, so comments, quoting, key order and
    blank lines are left alone. A fix is kept only if it is provably safe:
    when the content already parses, the parsed data must be unchanged
    (e.g. trailing spaces inside a ``|`` block scalar are kept); when it
    does not parse, the fixes must make it parse, and any edit that is not
    needed for that and changes a value is undone. Running it twice gives
    the same result as running it once.
    """
    line_fixers = [
        ("no-tabs", _check_tab_indentation, lambda ln: _fix_tab_line(ln, tab_width))
    ]
    if fix_trailing_spaces:
        line_fixers.append(
            ("trailing-spaces", _check_trailing_spaces, _fix_trailing_line)
        )
    eof = ("end-of-file-newline", _check_missing_newline)
    checks = [(rule, check) for rule, check, _ in line_fixers] + [eof]

    ok, data = _load_all(content)
    new = content
    if ok:
        reason = "would change the parsed data"
        for _, _, fix_line in line_fixers:
            lines = new.split("\n")
            edits = {i: fix_line(ln) for i, ln in enumerate(lines)}
            edits = {i: v for i, v in edits.items() if v != lines[i]}
            if edits:
                new = _with_edits(lines, _keep_data_preserving(lines, edits, data))
        if new and not new.endswith("\n"):
            ok_eof, parsed = _load_all(new + "\n")
            if ok_eof and _same_data(data, parsed):
                new += "\n"
    else:
        reason = "file is not valid YAML, so the fix cannot be verified"
        tab_fix = line_fixers[0][2]
        tab_lines = [tab_fix(ln) for ln in content.split("\n")]
        trimmed: Dict[int, str] = {}
        if fix_trailing_spaces:
            for i, ln in enumerate(tab_lines):
                if _fix_trailing_line(ln) != ln:
                    trimmed[i] = _fix_trailing_line(ln)
        add_eof = bool(content) and not content.endswith("\n")
        candidate = "\n".join(trimmed.get(i, ln) for i, ln in enumerate(tab_lines))
        ok_fixed, fixed_data = _load_all(candidate + ("\n" if add_eof else ""))
        if ok_fixed:
            new, _ = _revert_optional_edits(tab_lines, trimmed, add_eof, fixed_data)
            reason = "would change the parsed data"

    result = FixResult(content=new)
    for rule, check in checks:
        before, after = _count(check, content), _count(check, new)
        if before > after:
            result.fixed[rule] = before - after
        if after:
            result.skipped[rule] = f"{after} not fixed: {reason}"
    return result


def fix_file(
    path: Union[str, Path],
    tab_width: int = 2,
    fix_trailing_spaces: bool = True,
    backup: bool = False,
) -> FixResult:
    """Apply :func:`fix_content` to *path* in place.

    The file is only rewritten if something was fixed. CRLF line endings
    are kept. With ``backup=True`` the original bytes are first saved to
    ``<name>.bak`` (e.g. ``config.yaml.bak``).
    """
    path = Path(path)
    # Resolve once, then read and replace that same target, so a symlink
    # retargeted mid-run cannot redirect the write.
    target = Path(os.path.realpath(path))
    with open(target, "rb") as source:
        raw = source.read()
        info = os.fstat(source.fileno())
    result = fix_content(
        # Universal newlines, as read_text() would give.
        raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"),
        tab_width=tab_width,
        fix_trailing_spaces=fix_trailing_spaces,
    )
    if result.fixed:
        mode = stat.S_IMODE(info.st_mode)
        if backup:
            bak = path.with_name(path.name + ".bak")
            bak.unlink(missing_ok=True)  # Remove symlinks rather than following them.
            fd = os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            os.chmod(bak, mode)
        newline = "\r\n" if b"\r\n" in raw else "\n"
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as stream:
                stream.write(result.content)
            os.chmod(temporary, mode)
            current = os.stat(target)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise OSError(
                    f"{target} changed while it was being fixed; not overwritten"
                )
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return result


# ── High-level API ──────────────────────────────────────────────────────────


def validate_file(
    path: Union[str, Path],
    schema_path: Optional[Union[str, Path]] = None,
    multi_document: bool = False,
    check_line_length: bool = True,
    check_trailing_spaces: bool = True,
    max_line_length: int = 200,
) -> ValidationResult:
    """Validate a YAML file for syntax, style, and optional JSON Schema.

    Args:
        path: Path to the YAML file.
        schema_path: Optional path to a JSON/YAML schema file.
        multi_document: Accept multi-document YAML (``---`` separator).
        check_line_length: Warn on long lines.
        check_trailing_spaces: Warn on trailing whitespace.
        max_line_length: Threshold for line-length warnings.

    Returns:
        A :class:`ValidationResult` summarising all findings.
    """
    path = Path(path)
    if not path.exists():
        return ValidationResult(
            path=str(path),
            is_valid=False,
            errors=[ValidationError(message=f"File not found: {path}", rule="io")],
        )

    content = path.read_text(encoding="utf-8")
    result = ValidationResult(path=str(path), is_valid=True, document_count=0)

    warnings: List[ValidationError] = []

    # ── Style checks (always run, before parse) ──
    if check_trailing_spaces:
        _check_trailing_spaces(content, str(path), warnings)
    if check_line_length:
        _check_line_length(content, str(path), warnings, max_len=max_line_length)
    _check_tab_indentation(content, str(path), warnings)
    _check_missing_newline(content, str(path), warnings)

    # ── Parse ──
    parsed = parse_yaml(content, source=str(path), multi_document=multi_document)

    if isinstance(parsed, ValidationResult):
        # Parse failed
        result.is_valid = False
        result.errors = parsed.errors
        result.warnings = warnings
        return result

    # Parse succeeded
    if multi_document:
        result.document_count = len(parsed)
        result.data = parsed
        for doc in parsed:
            if doc is None:
                continue
            _detect_tab_vs_space(doc, warnings)
    else:
        result.document_count = 1 if parsed is not None else 0
        result.data = parsed
        if parsed is not None:
            _detect_tab_vs_space(parsed, warnings)

    # ── Schema validation ──
    if schema_path is not None:
        schema_result = _validate_schema(content, path, schema_path)
        result.errors.extend(schema_result.errors)
        result.warnings.extend(schema_result.warnings)

    result.warnings = warnings

    # Final valid?
    result.is_valid = len(result.errors) == 0

    return result


def _validate_schema(
    content: str,
    yaml_path: Path,
    schema_path: Union[str, Path],
) -> ValidationResult:
    """Validate parsed YAML data against a JSON Schema (or YAML schema)."""
    schema_path = Path(schema_path)
    sub = ValidationResult(path=str(yaml_path), is_valid=True)

    if not schema_path.exists():
        sub.errors.append(
            ValidationError(
                message=f"Schema file not found: {schema_path}", rule="io"
            )
        )
        return sub

    try:
        import jsonschema
        import jsonschema.exceptions
    except ImportError:
        sub.errors.append(
            ValidationError(
                message="jsonschema is not installed. Run: pip install jsonschema",
                rule="dependency",
            )
        )
        return sub

    try:
        schema_raw = schema_path.read_text(encoding="utf-8")
        schema = yaml.safe_load(schema_raw)
    except yaml.YAMLError as e:
        sub.errors.append(
            ValidationError(
                message=f"Schema file is not valid YAML/JSON: {e}",
                rule="schema-syntax",
            )
        )
        return sub

    try:
        data = yaml.safe_load(content)
        # Validate each document individually for multi-doc
        if isinstance(data, list):
            for i, doc in enumerate(data):
                if doc is None:
                    continue
                jsonschema.validate(instance=doc, schema=schema)
        else:
            if data is not None:
                jsonschema.validate(instance=data, schema=schema)
    except jsonschema.exceptions.ValidationError as e:
        sub.errors.append(
            ValidationError(
                message=e.message,
                rule="schema",
            )
        )
    except jsonschema.exceptions.SchemaError as e:
        sub.errors.append(
            ValidationError(message=f"Schema definition error: {e}", rule="schema")
        )

    return sub
