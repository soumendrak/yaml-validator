"""Tests for ``--fix`` (issue #1)."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "yaml_validator.cli", *args],
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": ""},
    )


def write(tmp_path, text, name="f.yaml"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_fix_trailing_spaces_keeps_comments(tmp_path):
    p = write(tmp_path, "# top comment   \nserver:  \n  host: localhost  # inline  \n")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_text() == "# top comment\nserver:\n  host: localhost  # inline\n"
    assert "Fixed 3 issue(s) in f.yaml (3 trailing-spaces)" in r.stdout


def test_fix_tab_indentation(tmp_path):
    p = write(tmp_path, "server:\n  host: localhost\n\tport: 8080\n")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_text() == "server:\n  host: localhost\n  port: 8080\n"
    assert "(1 no-tabs)" in r.stdout


def test_fix_tab_width(tmp_path):
    p = write(tmp_path, "a:\n\tb:\n\t\tc: 1\n")
    r = run("check", "--fix", "--tab-width", "4", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_text() == "a:\n    b:\n        c: 1\n"


def test_fix_missing_eof_newline(tmp_path):
    p = write(tmp_path, "a: 1\nb: 2")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_text() == "a: 1\nb: 2\n"
    assert "(1 end-of-file-newline)" in r.stdout


def test_fix_tabs_and_trailing_tab_together(tmp_path):
    # Neither fix alone makes this parse (PyYAML rejects the trailing tab too).
    p = write(tmp_path, "a:\n\tb: 1\t\n")
    r = run("lint", "--fix", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_text() == "a:\n  b: 1\n"


def test_fix_keeps_crlf_line_endings(tmp_path):
    p = write(tmp_path, "a: 1  \r\nb: 2\r\n")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_bytes() == b"a: 1\r\nb: 2\r\n"


def test_fix_skips_block_scalar_trailing_spaces(tmp_path):
    # Trailing spaces inside a literal block are data; only the safe line changes.
    p = write(tmp_path, "msg: |\n  keep me   \nother: 1  \n")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert p.read_text() == "msg: |\n  keep me   \nother: 1\n"
    assert (
        "Skipped trailing-spaces fix in f.yaml: 1 not fixed: would change the parsed data"
        in r.stdout
    )
    assert r.returncode == 0  # only a warning is left


def test_fix_backup(tmp_path):
    original = "a: 1   \n"
    p = write(tmp_path, original)
    r = run("check", "--fix", "--backup", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "f.yaml.bak").read_text() == original
    assert p.read_text() == "a: 1\n"


def test_fix_respects_no_trailing_spaces(tmp_path):
    p = write(tmp_path, "a: 1  \nb: 2")
    r = run("check", "--fix", "--no-trailing-spaces", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_text() == "a: 1  \nb: 2\n"


def test_fix_is_idempotent(tmp_path):
    p = write(tmp_path, "# c  \nlist:\n\t- x  \n\t- y\nmsg: |\n  keep   \nend: 1")
    first = run("check", "--fix", p.name, cwd=tmp_path)
    assert "Fixed" in first.stdout, first.stdout + first.stderr
    once = p.read_bytes()
    second = run("check", "--fix", p.name, cwd=tmp_path)
    assert p.read_bytes() == once
    assert "Fixed" not in second.stdout
    assert second.returncode == first.returncode == 0

    from yaml_validator.validator import fix_content

    text = once.decode()
    again = fix_content(text)
    assert again.content == text and again.fixed == {}


def test_fix_unfixable_error_exits_nonzero_and_leaves_file(tmp_path):
    original = "a: [1, 2  \nb: 2"
    p = write(tmp_path, original)
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert r.returncode == 1
    assert p.read_text() == original  # cannot verify fixes on unparseable YAML
    assert "Skipped trailing-spaces fix in f.yaml" in r.stdout
    assert "[ERROR]" in r.stdout
    assert not (tmp_path / "f.yaml.bak").exists()


def test_fix_unfixable_tab_exits_nonzero(tmp_path):
    # The tab fix cannot make this parse (the bracket is never closed), so it
    # is not applied and the tab error is still reported.
    original = "a:\n\tb: [1, 2\n"
    p = write(tmp_path, original)
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert p.read_text() == original
    assert "Skipped no-tabs fix in f.yaml: 1 not fixed" in r.stdout
    assert "Tab character used for indentation" in r.stdout
    assert r.returncode == 1


def test_fix_json_reports_fixes(tmp_path):
    p = write(tmp_path, "a: 1  \nb: 2")
    r = run("check", "--fix", "--json", p.name, cwd=tmp_path)
    payload = json.loads(r.stdout)
    assert payload["fixed"] == {"trailing-spaces": 1, "end-of-file-newline": 1}
    assert payload["fix_skipped"] == {}
    assert payload["valid"] is True


def test_fix_combined_keeps_block_scalar_spaces(tmp_path):
    # Tab + trailing-tab fixes are needed together to parse; the block
    # scalar's significant trailing spaces must survive that combined pass.
    p = write(tmp_path, "msg: |\n  keep   \na:\n\tb: 1\t\n")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert p.read_text() == "msg: |\n  keep   \na:\n  b: 1\n"
    assert r.returncode == 0, r.stdout + r.stderr
    import yaml

    assert yaml.safe_load(p.read_text())["msg"] == "keep   \n"


def test_fix_backup_keeps_permissions(tmp_path):
    import os
    import stat

    p = write(tmp_path, "password: x   \n")
    os.chmod(p, 0o600)
    r = run("check", "--fix", "--backup", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    mode = stat.S_IMODE(os.stat(tmp_path / "f.yaml.bak").st_mode)
    assert mode == 0o600


def test_fix_recursive_alias_does_not_crash():
    from yaml_validator.validator import fix_content

    result = fix_content("a: &x [*x]  \n")
    assert result.content == "a: &x [*x]\n"


def test_fix_many_lines_with_one_unsafe_is_fast():
    import time

    from yaml_validator.validator import fix_content

    body = "".join(f"k{i}: {i}  \n" for i in range(2000))
    text = "msg: |\n  keep   \n" + body
    start = time.perf_counter()
    result = fix_content(text)
    assert time.perf_counter() - start < 10
    assert result.fixed == {"trailing-spaces": 2000}
    assert "  keep   \n" in result.content


def test_fix_combined_keeps_unterminated_block_scalar_eof():
    import yaml

    from yaml_validator.validator import fix_content

    result = fix_content("a:\n\tb: 1\nmsg: |\n  x")
    assert result.content == "a:\n  b: 1\nmsg: |\n  x"
    assert yaml.safe_load(result.content)["msg"] == "x"
    assert "end-of-file-newline" in result.skipped


def test_fix_combined_same_line_tab_and_significant_spaces():
    import yaml

    from yaml_validator.validator import fix_content

    result = fix_content("msg: |\n\tx   \n  y\n")
    assert yaml.safe_load(result.content)["msg"] == "x   \ny\n"
    assert "\t" not in result.content


def test_fix_quiet_hides_skipped_warnings(tmp_path):
    p = write(tmp_path, "msg: |\n  keep me   \n")
    r = run("check", "--fix", "--quiet", p.name, cwd=tmp_path)
    assert "Skipped" not in r.stdout
    assert r.returncode == 0
