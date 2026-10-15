"""
yaml_validator/validator.py — Core YAML parsing and validation engine.

Provides:
  - parse_yaml()     — safe YAML load with detailed error reporting
  - validate_file()  — syntax + optional schema validation for a YAML file
  - ValidationResult — typed result with errors, warnings, metadata
  - fix_content() / fix_file() — safe auto-fix for tabs, trailing spaces, EOF newline
"""

import os
import secrets
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


def _identity(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _read_xattrs(fd: int) -> Optional[Dict[str, bytes]]:
    """Best effort: the source's extended attributes, which hold POSIX ACLs
    (``system.posix_acl_access``) and SELinux labels (``security.selinux``).

    Returns None when the snapshot is incomplete (an attribute could not be
    listed or read), so absent names are not mistaken for confirmed absence.
    """
    if not hasattr(os, "listxattr"):
        return {}
    attrs: Dict[str, bytes] = {}
    try:
        names = os.listxattr(fd)
        for name in names:
            attrs[name] = os.getxattr(fd, name)
    except OSError:
        return None
    return attrs


def _apply_metadata(
    fd: int, path: str, info: os.stat_result, attrs: Optional[Dict[str, bytes]]
) -> None:
    """Give the open temp file *fd* the source's metadata, best effort.

    Everything goes through the descriptor, so a swapped directory entry
    cannot redirect it. Order: owner/group, then xattrs (while the file is
    still 0600), then the mode.
    """
    if hasattr(os, "fchown"):
        # Group first: a non-owner in the file's group may set the group
        # even though changing the owner needs root.
        for uid, gid in ((-1, info.st_gid), (info.st_uid, -1)):
            try:
                os.fchown(fd, uid, gid)
            except PermissionError:
                pass
    # With an incomplete snapshot (None), leave the temp's attributes alone
    # rather than strip ones the source may have.
    if attrs is not None and hasattr(os, "listxattr"):
        try:
            inherited = os.listxattr(fd)
        except OSError:
            inherited = []
        for name in inherited:  # e.g. an ACL inherited from the directory
            if name not in attrs:
                try:
                    os.removexattr(fd, name)
                except OSError:
                    pass
        for name, value in attrs.items():
            try:
                os.setxattr(fd, name, value)
            except OSError:
                pass
    mode = stat.S_IMODE(info.st_mode)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, mode)
    else:  # Windows
        os.chmod(path, mode)


def _atomic_write(
    dest: Path,
    data: bytes,
    info: os.stat_result,
    attrs: Optional[Dict[str, bytes]],
    before_replace=None,
) -> None:
    """Write *data* with the source's metadata, then rename it over *dest*.

    The temp file lives in a fresh 0700 directory beside *dest*. Where the
    platform allows, *dest*'s directory is opened once and every step (mkdir,
    create, rename, cleanup) goes through descriptors, so neither swapping
    the temp entry nor retargeting a symlinked ancestor can redirect it.
    """
    bound = os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd
    if not bound:  # e.g. Windows: plain paths
        tmpdir = tempfile.mkdtemp(dir=dest.parent, prefix=".yv-")
        name = os.path.join(tmpdir, "new")
        try:
            _write_new(os.open(name, _NEW_FLAGS, 0o600), name, data, info, attrs)
            if before_replace is not None:
                before_replace()
            os.replace(name, dest)
        finally:
            Path(name).unlink(missing_ok=True)
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
        return

    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    pfd = os.open(dest.parent, dir_flags)
    try:
        tmpdir = f".yv-{secrets.token_hex(8)}"
        os.mkdir(tmpdir, 0o700, dir_fd=pfd)
        try:
            dfd = os.open(tmpdir, dir_flags, dir_fd=pfd)
            try:
                fd = os.open("new", _NEW_FLAGS, 0o600, dir_fd=dfd)
                where = os.path.join(dest.parent, tmpdir, "new")
                _write_new(fd, where, data, info, attrs)
                if before_replace is not None:
                    before_replace()
                os.replace("new", dest.name, src_dir_fd=dfd, dst_dir_fd=pfd)
            finally:
                try:
                    os.unlink("new", dir_fd=dfd)
                except FileNotFoundError:
                    pass
                os.close(dfd)
        finally:
            try:
                os.rmdir(tmpdir, dir_fd=pfd)
            except OSError:  # e.g. a look-alike planted under that name
                pass
    finally:
        os.close(pfd)


_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)


def _write_new(
    fd: int,
    path: str,
    data: bytes,
    info: os.stat_result,
    attrs: Optional[Dict[str, bytes]],
) -> None:
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        _apply_metadata(fd, path, info, attrs)
    finally:
        os.close(fd)


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

    The fixed file is written to a temporary file and renamed over the
    original, keeping its mode and, best effort, its owner, group and extended
    attributes (ACLs, SELinux labels).
    This breaks hard links: other names for the file keep the old content.
    """
    path = Path(path)
    # Resolve once, then read and replace that same target, so a symlink
    # retargeted mid-run cannot redirect the write.
    target = Path(os.path.realpath(path))
    with open(target, "rb") as source:
        raw = source.read()
        info = os.fstat(source.fileno())
        attrs = _read_xattrs(source.fileno())
    result = fix_content(
        # Universal newlines, as read_text() would give.
        raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"),
        tab_width=tab_width,
        fix_trailing_spaces=fix_trailing_spaces,
    )
    if result.fixed:
        if backup:
            # Beside the given path, with its directory resolved once.
            bak = Path(os.path.realpath(path.parent)) / (path.name + ".bak")
            # Build the new backup in a temp file, then swap it in, so a
            # failed write leaves the previous backup intact. os.replace
            # replaces a planted .bak symlink rather than following it.
            _atomic_write(bak, raw, info, attrs)
        newline = "\r\n" if b"\r\n" in raw else "\n"
        data = result.content.replace("\n", newline).encode("utf-8")

        def unchanged() -> None:
            # Same file, and not rewritten in place since it was read.
            if _identity(os.stat(target)) != _identity(info):
                raise OSError(
                    f"{target} changed while it was being fixed; not overwritten"
                )

        _atomic_write(target, data, info, attrs, unchanged)
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
