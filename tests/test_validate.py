"""Tests for validation, the fix safety net and its speed."""

import errno
import os
import runpy
import sys
import time

import pytest
import yaml

from yaml_validator import validator as v
from yaml_validator.validator import fix_content, fix_file, validate_file


def timed_fix(text):
    start = time.perf_counter()
    result = fix_content(text)
    assert time.perf_counter() - start < 2
    assert yaml.safe_load(result.content) == yaml.safe_load(text)
    return result


def test_many_unsafe_lines_are_fast():
    block = "".join(f"  line {i}   \n" for i in range(2000))
    result = timed_fix("msg: |\n" + block + "".join(f"k{i}: 1  \n" for i in range(9)))
    assert result.fixed == {"trailing-spaces": 9}
    assert block in result.content


def test_valid_trailing_spaces_in_literal_blocks_are_fast():
    text = "".join(f"k{i}: |\n  a  \n  b  \n  c  \n" for i in range(500))
    assert timed_fix(text).content == text


def test_invalid_input_is_fast():
    text = "".join(f"k{i}:\n\tv: |\n\t  a  \n" for i in range(700))
    result = fix_content(text)
    start = time.perf_counter()
    assert fix_content(text).content == result.content
    assert time.perf_counter() - start < 2
    assert result.fixed == {"no-tabs": 1400}
    assert "    a  \n" in result.content


def test_unsafe_edit_outside_block_scalar_rejects_all():
    text = 'a: "x\\   \n y"\nb: 1  \n'
    result = fix_content(text)
    assert result.content == text
    assert result.total == 0


@pytest.mark.parametrize(
    ("text", "fixed"),
    [
        ("m: |  \n  x  \nb: 1  \n", "m: |\n  x  \nb: 1\n"),
        ("m: >\n  x  \n  y  \nb: 1  \n", "m: >\n  x  \n  y  \nb: 1\n"),
        ("b: 1  \nm: |\n  x  \n  y  ", "b: 1\nm: |\n  x  \n  y  "),
        ("m: x  \n  y  \nn: 'a  \n  b'  \n", "m: x\n  y\nn: 'a\n  b'\n"),
        ('m: "a"  \nn: |\n  x  \n', 'm: "a"\nn: |\n  x  \n'),
        ("m: |\n  x  \n\n", "m: |\n  x  \n\n"),
    ],
)
def test_block_scalar_boundaries(text, fixed):
    assert fix_content(text).content == fixed


def test_tab_width_one():
    assert fix_content("a:\n\tb: 1\n", tab_width=1).content == "a:\n b: 1\n"


def test_unsupported_xattrs_are_ignored(tmp_path, monkeypatch):
    def unsupported(fd):
        raise OSError(errno.EOPNOTSUPP, "not supported")

    monkeypatch.setattr(os, "listxattr", unsupported, raising=False)
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    assert fix_file(p).total == 1
    assert p.read_text() == "a: 1\n"


def test_hard_linked_file_is_refused(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    os.link(p, tmp_path / "g.yaml")
    with pytest.raises(OSError, match="2 hard links"):
        fix_file(p)
    assert p.read_text() == "a: 1  \n"


def test_validate_reports_style_and_documents(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text('---\n"a\\tb": 1\n---\n---\n' + "c: " + "x" * 30 + "\n")
    result = validate_file(p, multi_document=True, max_line_length=20)
    rules = sorted(w.rule for w in result.warnings)
    assert rules == ["line-length", "no-tabs"]
    assert "(3 documents)" in result.summary
    assert result.errors == []


def test_schema_validation(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("a: 1\n")
    schema = tmp_path / "s.yaml"
    schema.write_text("required: [b]\n")
    assert validate_file(p, schema_path=schema).errors[0].rule == "schema"
    schema.write_text("type: 5\n")
    assert "definition error" in validate_file(p, schema_path=schema).errors[0].message
    schema.write_text("a: [\n")
    assert validate_file(p, schema_path=schema).errors[0].rule == "schema-syntax"
    p.write_text("- null\n- 5\n")
    schema.write_text("type: integer\n")
    assert validate_file(p, schema_path=schema).errors == []
    p.write_text("- x\n")
    assert validate_file(p, schema_path=schema).errors[0].rule == "schema"
    p.write_text("")
    assert validate_file(p, schema_path=schema, check_line_length=False).errors == []
    missing = validate_file(p, schema_path=tmp_path / "none.yaml").errors
    assert missing[0].rule == "io"


def test_schema_without_jsonschema(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    p = tmp_path / "f.yaml"
    p.write_text("a: 1\n")
    (tmp_path / "s.yaml").write_text("{}\n")
    errors = validate_file(p, schema_path=tmp_path / "s.yaml").errors
    assert errors[0].rule == "dependency"


def test_module_entry_point(tmp_path, monkeypatch):
    (tmp_path / "f.yaml").write_text("a: 1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["yaml-validator", "check", "f.yaml"])
    monkeypatch.delitem(sys.modules, "yaml_validator.cli")
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("yaml_validator.cli", run_name="__main__")
    assert exc.value.code == 0


def test_vanished_xattr_is_skipped(tmp_path, monkeypatch):
    def gone(fd, name):
        if name == "user.gone":
            raise OSError(errno.ENODATA, "gone")
        return b""

    names = ["user.gone"]
    monkeypatch.setattr(os, "listxattr", lambda fd: names, raising=False)
    monkeypatch.setattr(os, "getxattr", gone, raising=False)
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    assert fix_file(p).total == 1
    p.write_text("a: 1  \n")
    names.append("user.real")
    with pytest.raises(OSError, match="user.real"):
        fix_file(p)


def test_strip_breaking_tab_keeps_other_characters():
    assert v._strip_breaking_tab("aX \t") == "aX "
