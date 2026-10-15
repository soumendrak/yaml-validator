"""
yaml_validator/validator.py — Core YAML parsing and validation engine.

Provides:
  - parse_yaml()     — safe YAML load with detailed error reporting
  - validate_file()  — syntax + optional schema validation for a YAML file
  - ValidationResult — typed result with errors, warnings, metadata
  - fix_content() / fix_file() — safe auto-fix for tabs, trailing spaces, EOF newline
"""

import errno
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Union

import yaml
from yaml.error import MarkedYAMLError

# ── Types ────────────────────────────────────────────────────────────────────


@dataclass
class ValidationError:
    """A single validation finding."""

    message: str
    line: int | None = None
    column: int | None = None
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
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    document_count: int = 0
    data: object | None = None

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
) -> Union[object, list[object], "ValidationResult"]:
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
            docs: list[object] = list(yaml.safe_load_all(content))
            return docs
        loaded: object = yaml.safe_load(content)
        return loaded
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
    except (yaml.YAMLError, ValueError, TypeError) as e:
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
    content: str, source: str, errors: list[ValidationError]
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
    content: str, source: str, errors: list[ValidationError], max_len: int = 200
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
    content: str, source: str, errors: list[ValidationError]
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
    content: str, source: str, errors: list[ValidationError]
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
    data: object, errors: list[ValidationError], path: str = "$"
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


@dataclass
class FixResult:
    """Outcome of auto-fixing one file's content."""

    content: str
    fixed: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.fixed.values())


LineFixer = Callable[[str], str]
Check = Callable[[str, str, list[ValidationError]], None]


def _fix_tab_line(line: str, tab_width: int) -> str:
    if line.startswith("\t") and line.strip():
        indent = len(line) - len(line.lstrip(" \t"))
        return line[:indent].replace("\t", " " * tab_width) + line[indent:]
    return line


def _fix_trailing_line(line: str) -> str:
    return line.rstrip() if line.strip() else line


def _load_all(content: str) -> tuple[bool, list[Any]]:
    try:
        return True, list(yaml.safe_load_all(content))
    except (yaml.YAMLError, ValueError, TypeError):
        return False, []


def _same_data(a: list[Any], b: list[Any]) -> bool:
    with suppress(RecursionError):
        if a == b:
            return True
    try:
        return bool(yaml.safe_dump_all(a) == yaml.safe_dump_all(b))
    except (yaml.YAMLError, RecursionError):
        return False


def _parses_to(content: str, data: list[Any]) -> bool:
    ok, parsed = _load_all(content)
    return ok and _same_data(data, parsed)


def _with_edits(lines: list[str], edits: dict[int, str]) -> str:
    return "\n".join(edits.get(i, line) for i, line in enumerate(lines))


def _keep_data_preserving(
    lines: list[str], edits: dict[int, str], data: list[Any]
) -> dict[int, str]:
    accepted: dict[int, str] = {}

    def visit(group: list[int]) -> None:
        trial = {**accepted, **{i: edits[i] for i in group}}
        if _parses_to(_with_edits(lines, trial), data):
            accepted.update(trial)
        elif len(group) > 1:
            visit(group[: len(group) // 2])
            visit(group[len(group) // 2 :])

    visit(sorted(edits))
    return accepted


def _preserving(content: str, data: list[Any], fixers: list[LineFixer]) -> str:
    new = content
    for fix_line in fixers:
        lines = new.split("\n")
        edits = {i: fix_line(ln) for i, ln in enumerate(lines) if fix_line(ln) != ln}
        new = _with_edits(lines, _keep_data_preserving(lines, edits, data))
    if new and not new.endswith("\n") and _parses_to(new + "\n", data):
        new += "\n"
    return new


def _strip_breaking_tab(line: str) -> str:
    body = line.rstrip(" \t")
    trail = line[len(body) :]
    if "\t" not in trail or not body:
        return line
    return body + trail.replace("\t", "")


def _block_scalar_lines(text: str) -> set[int]:
    inside: set[int] = set()
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        start, end = event.start_mark, event.end_mark
        block = isinstance(event, yaml.ScalarEvent) and event.style in ("|", ">")
        if block and start and end:
            inside.update(range(start.line + 1, end.line + bool(end.column)))
    return inside


def _baseline(
    content: str, fix_tab: LineFixer, strip_tabs: bool
) -> tuple[bool, str, list[Any]]:
    lines = [fix_tab(line) for line in content.split("\n")]
    ok, data = _load_all("\n".join(lines))
    if ok or not strip_tabs:
        return ok, "\n".join(lines) if ok else content, data
    edits = {i: _strip_breaking_tab(ln) for i, ln in enumerate(lines)}
    edits = {i: ln for i, ln in edits.items() if ln != lines[i]}
    if edits and _load_all(_with_edits(lines, edits))[0]:
        inside = _block_scalar_lines(_with_edits(lines, edits))
        text = _with_edits(lines, {i: edits[i] for i in edits if i not in inside})
        ok, data = _load_all(text)
        if ok:
            return True, text, data
    return False, content, []


def _count(check: Check, content: str) -> int:
    found: list[ValidationError] = []
    check(content, "", found)
    return len(found)


def fix_content(
    content: str, tab_width: int = 2, fix_trailing_spaces: bool = True
) -> FixResult:
    """Fix tabs, trailing whitespace and a missing EOF newline, keeping only
    fixes that leave the parsed data unchanged. Invalid YAML is fixed only
    when replacing tabs (and dropping syntax-breaking trailing tabs) makes
    it parse."""
    fix_tab: LineFixer = partial(_fix_tab_line, tab_width=tab_width)
    checks: list[tuple[str, Check]] = [("no-tabs", _check_tab_indentation)]
    fixers: list[LineFixer] = [fix_tab]
    if fix_trailing_spaces:
        checks.append(("trailing-spaces", _check_trailing_spaces))
        fixers.append(_fix_trailing_line)
    checks.append(("end-of-file-newline", _check_missing_newline))
    ok, data = _load_all(content)
    new = content
    reason = "file is not valid YAML, so the fix cannot be verified"
    if not ok:
        ok, new, data = _baseline(content, fix_tab, fix_trailing_spaces)
    if ok:
        new = _preserving(new, data, fixers)
        reason = "would change the parsed data"
    result = FixResult(content=new)
    for rule, check in checks:
        before, after = _count(check, content), _count(check, new)
        if before > after:
            result.fixed[rule] = before - after
        if after:
            result.skipped[rule] = f"{after} not fixed: {reason}"
    return result


def _identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


_XATTR_UNSUPPORTED = {errno.ENOTSUP, errno.EOPNOTSUPP}


def _xattr_names(fd: int) -> list[str]:
    if not hasattr(os, "listxattr"):
        return []
    try:
        return os.listxattr(fd)
    except OSError as exc:
        if exc.errno in _XATTR_UNSUPPORTED:
            return []
        raise


def _refuse_xattrs(fd: int, target: Path) -> None:
    try:
        names = _xattr_names(fd)
    except OSError as exc:
        raise OSError(f"{target}: cannot read extended attributes ({exc})") from exc
    for name in names:
        try:
            os.getxattr(fd, name)
        except OSError as exc:
            if exc.errno == errno.ENODATA:
                continue
            raise OSError(f"{target}: cannot read xattr {name} ({exc})") from exc
        kept = "--fix keeps only mode, group and owner"
        raise OSError(f"{target} has extended attribute {name} ({kept}); not fixed")


def _reject_inherited(fd: int, dest: Path) -> None:
    for name in _xattr_names(fd):
        if name.startswith("system.posix_acl_"):
            raise OSError(f"{dest}: the directory's default ACL would apply")
        if name.startswith("security."):
            raise OSError(f"{dest}: inherited security xattr {name}; not fixed")


def _named(exc: OSError, dest: Path) -> OSError:
    if exc.errno is None:
        return exc
    return type(exc)(exc.errno, exc.strerror, str(dest))


def _discard(path: str) -> None:
    with suppress(OSError):
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


def _write_temp(
    dest: Path, data: bytes, info: os.stat_result, mode: int, keep_owner: bool
) -> str:
    try:
        fd, temporary = tempfile.mkstemp(dir=dest.parent, prefix=".yaml-validator.")
    except OSError as exc:
        raise _named(exc, dest) from exc
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            if keep_owner:
                _keep_owner(fd, info, dest)
            _reject_inherited(fd, dest)
        finally:
            os.close(fd)
        os.chmod(temporary, mode)
    except OSError as exc:
        _discard(temporary)
        raise _named(exc, dest) from exc
    return temporary


def _keep_owner(fd: int, info: os.stat_result, dest: Path) -> None:
    if not hasattr(os, "fchown"):
        return
    try:
        os.fchown(fd, info.st_uid, info.st_gid)
    except PermissionError as exc:
        current = os.fstat(fd)
        if current.st_uid != info.st_uid:
            what = "owner; run --fix as the file's owner (or as root)"
        elif current.st_gid != info.st_gid:
            what = "group"
        else:
            return
        message = f"could not preserve the file's {what}; not fixed"
        raise OSError(errno.EPERM, message, str(dest)) from exc


def _replace(temporary: str, dest: Path) -> None:
    try:
        os.replace(temporary, dest)
    except OSError as exc:
        _discard(temporary)
        raise _named(exc, dest) from exc


def fix_file(
    path: str | Path,
    tab_width: int = 2,
    fix_trailing_spaces: bool = True,
    backup: bool = False,
) -> FixResult:
    """Apply :func:`fix_content` to *path* in place via an atomic rename, keeping
    mode, group and owner; files with extended attributes are refused."""
    path = Path(path)
    target = Path(os.path.realpath(path))
    bak = Path(os.path.realpath(path.parent)) / (path.name + ".bak")
    if backup and os.path.realpath(bak) == str(target):
        raise OSError(f"{bak} is the file being fixed; not fixed")
    with open(target, "rb") as source:
        before = os.fstat(source.fileno())
        raw = source.read()
        info = os.fstat(source.fileno())
        if _identity(before) != _identity(info):
            raise OSError(f"{target} changed while it was being read; not fixed")
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        result = fix_content(text, tab_width, fix_trailing_spaces)
        if not result.fixed:
            return result
        _refuse_xattrs(source.fileno(), target)
    newline = "\r\n" if b"\r\n" in raw else "\n"
    data = result.content.replace("\n", newline).encode("utf-8")
    temporary = _write_temp(target, data, info, stat.S_IMODE(info.st_mode), True)
    try:
        _check_unchanged(target, info)
        if backup:
            _replace(_write_temp(bak, raw, info, 0o600, False), bak)
            _check_unchanged(target, info)
    except OSError:
        _discard(temporary)
        raise
    _replace(temporary, target)
    return result


def _check_unchanged(target: Path, info: os.stat_result) -> None:
    if _identity(os.stat(target)) != _identity(info):
        raise OSError(f"{target} changed while it was being fixed; not overwritten")


# ── High-level API ──────────────────────────────────────────────────────────


def validate_file(
    path: str | Path,
    schema_path: str | Path | None = None,
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

    warnings: list[ValidationError] = []

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
    if multi_document and isinstance(parsed, list):
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
    schema_path: str | Path,
) -> ValidationResult:
    """Validate parsed YAML data against a JSON Schema (or YAML schema)."""
    schema_path = Path(schema_path)
    sub = ValidationResult(path=str(yaml_path), is_valid=True)

    if not schema_path.exists():
        sub.errors.append(
            ValidationError(message=f"Schema file not found: {schema_path}", rule="io")
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
