"""
yaml_validator/validator.py — Core YAML parsing and validation engine.

Provides:
  - parse_yaml()     — safe YAML load with detailed error reporting
  - validate_file()  — syntax + optional schema validation for a YAML file
  - ValidationResult — typed result with errors, warnings, metadata
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

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
