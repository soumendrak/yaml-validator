"""Tests for ``--fix`` (issue #1)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "yaml_validator.cli", *args],
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PATH": ""},
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
    p = write(tmp_path, "msg: |\n  keep me   \nother: 1  \n")
    r = run("check", "--fix", p.name, cwd=tmp_path)
    assert p.read_text() == "msg: |\n  keep me   \nother: 1\n"
    assert (
        "Skipped trailing-spaces fix in f.yaml: 1 not fixed: would change the parsed data"
        in r.stdout
    )
    assert r.returncode == 0


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
    assert p.read_text() == original
    assert "Skipped trailing-spaces fix in f.yaml" in r.stdout
    assert "[ERROR]" in r.stdout
    assert not (tmp_path / "f.yaml.bak").exists()


def test_fix_unfixable_tab_exits_nonzero(tmp_path):
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


def test_fix_invalid_yaml_keeps_block_scalar_spaces():
    import yaml

    from yaml_validator.validator import fix_content

    result = fix_content("msg: |\n\tx   \na:\n\tb: 1\t\n")
    assert result.content == "msg: |\n  x   \na:\n  b: 1\n"
    assert yaml.safe_load(result.content)["msg"] == "x   \n"
    assert result.fixed["no-tabs"] == 2
    assert result.fixed["trailing-spaces"] == 1
    assert "would change the parsed data" in result.skipped["trailing-spaces"]


def test_fix_invalid_yaml_keeps_block_scalar_trailing_tab():
    import yaml

    from yaml_validator.validator import fix_content

    result = fix_content("msg: |\n\tx\t\na:\n\tb: 1\t\n")
    assert result.content == "msg: |\n  x\t\na:\n  b: 1\n"
    assert yaml.safe_load(result.content)["msg"] == "x\t\n"
    mixed = fix_content("msg: |\n\tx   \t\na:\n\tb: 1\t\n")
    assert mixed.content == "msg: |\n  x   \t\na:\n  b: 1\n"
    assert yaml.safe_load(mixed.content)["msg"] == "x   \t\n"
    plain = fix_content("a:\n\tb: 1\t \n")
    assert plain.content == "a:\n  b: 1\n"


def test_no_trailing_spaces_does_not_strip_syntax_tab():
    from yaml_validator.validator import fix_content

    text = "a:\n\tb: 1\t\n"
    result = fix_content(text, fix_trailing_spaces=False)
    assert result.content == text
    assert result.fixed == {}


def test_breaking_tab_strip_leaves_unparseable_file():
    from yaml_validator.validator import fix_content

    text = "a:\n\tb: [1, 2\t\n"
    result = fix_content(text)
    assert result.content == text
    assert result.fixed == {}


def test_strip_breaking_tab_leaves_blank_lines_and_spaces():
    from yaml_validator.validator import _strip_breaking_tab

    assert _strip_breaking_tab("\t\t") == "\t\t"
    assert _strip_breaking_tab("  x   ") == "  x   "
    assert _strip_breaking_tab("  b: 1\t ") == "  b: 1 "
    assert _strip_breaking_tab("  b: 1 \t") == "  b: 1 "


def test_fix_quiet_hides_skipped_warnings(tmp_path):
    p = write(tmp_path, "msg: |\n  keep me   \n")
    r = run("check", "--fix", "--quiet", p.name, cwd=tmp_path)
    assert "Skipped" not in r.stdout
    assert r.returncode == 0


def test_backup_created_private_before_source_mode_applied(tmp_path, monkeypatch):
    import os
    import stat

    from yaml_validator.validator import fix_file

    p = write(tmp_path, "a: 1  \r\n")
    p.chmod(0o640)
    bak = p.with_suffix(".yaml.bak")
    real_open = os.open
    creations = []

    def observe_open(path, flags, mode=0o777, **kwargs):
        fd = real_open(path, flags, mode, **kwargs)
        if Path(path).name.startswith(".yaml-validator."):
            excl = flags & (os.O_CREAT | os.O_EXCL) == os.O_CREAT | os.O_EXCL
            creations.append((excl, mode, stat.S_IMODE(os.fstat(fd).st_mode)))
        return fd

    monkeypatch.setattr(os, "open", observe_open)
    fix_file(p, backup=True)
    assert creations == [(True, 0o600, 0o600)] * 2
    assert bak.read_bytes() == b"a: 1  \r\n"
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.stat().st_mode) == 0o640


def test_backup_replaces_existing_wider_mode_file(tmp_path):
    import stat

    from yaml_validator.validator import fix_file

    p = write(tmp_path, "secret: x  \n")
    p.chmod(0o600)
    bak = write(tmp_path, "old backup", "f.yaml.bak")
    bak.chmod(0o666)
    fix_file(p, backup=True)
    assert bak.read_bytes() == b"secret: x  \n"
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600


def test_backup_replaces_symlink_without_following(tmp_path):
    from yaml_validator.validator import fix_file

    p = write(tmp_path, "a: 1  \n")
    victim = write(tmp_path, "untouched", "victim")
    bak = tmp_path / "f.yaml.bak"
    bak.symlink_to(victim)
    fix_file(p, backup=True)
    assert not bak.is_symlink()
    assert bak.read_bytes() == b"a: 1  \n"
    assert victim.read_bytes() == b"untouched"


def test_fix_atomically_replaces_resolved_target_preserving_mode(tmp_path, monkeypatch):
    import os
    import stat

    from yaml_validator.validator import fix_file

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = write(target_dir, "a: 1  \n")
    target.chmod(0o640)
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    real_replace = os.replace
    calls = []

    def observe_replace(src, dst, **kwargs):
        dir_fd = kwargs.get("src_dir_fd")
        if dir_fd is not None:
            src = os.path.join(os.readlink(f"/proc/self/fd/{dir_fd}"), src)
        if kwargs.get("dst_dir_fd") is not None:
            dst = Path(os.readlink(f"/proc/self/fd/{kwargs['dst_dir_fd']}")) / dst
        src = Path(src)
        calls.append((src, dst))
        assert src.parent == target.parent
        assert target.read_bytes() == b"a: 1  \n"
        assert src.read_bytes() == b"a: 1\n"
        assert stat.S_IMODE(src.stat().st_mode) == 0o640
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", observe_replace)
    fix_file(link)
    assert len(calls) == 1
    assert calls[0][1] == target
    assert not calls[0][0].exists()
    assert link.is_symlink()
    assert target.read_bytes() == link.read_bytes() == b"a: 1\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_replace_permission_error_is_clean_and_cleans_temp(
    tmp_path, monkeypatch, capsys
):
    import os

    from yaml_validator.cli import main

    p = write(tmp_path, "a: 1  \n")

    def denied(src, dst, **kwargs):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(os, "replace", denied)
    monkeypatch.setattr(sys, "argv", ["yaml-validator", "check", "--fix", str(p)])
    assert main() == 1
    output = capsys.readouterr()
    assert "Error:" in output.err
    assert "Permission denied" in output.err
    assert "Traceback" not in output.out + output.err
    assert p.read_bytes() == b"a: 1  \n"
    assert list(tmp_path.iterdir()) == [p]


def test_fix_no_changes_leaves_mtime_content_and_no_backup(tmp_path):
    import os

    p = write(tmp_path, "a: 1\n")
    os.utime(p, ns=(1_000_000_000, 1_000_000_000))
    before = p.stat().st_mtime_ns
    r = run("check", "--fix", "--backup", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert p.read_bytes() == b"a: 1\n"
    assert p.stat().st_mtime_ns == before
    assert not (tmp_path / "f.yaml.bak").exists()


def test_valid_file_missing_eof_newline_is_reported(tmp_path):
    p = write(tmp_path, "a: 1")
    r = run("check", "--json", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["valid"] is True
    assert [w["rule"] for w in payload["warnings"]] == ["end-of-file-newline"]


def test_empty_file_has_no_eof_warning_or_fix(tmp_path):
    p = write(tmp_path, "")
    r = run("check", "--fix", "--backup", "--json", p.name, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["warnings"] == []
    assert payload["fixed"] == {}
    assert p.read_bytes() == b""
    assert not (tmp_path / "f.yaml.bak").exists()


def test_fix_quiet_suppresses_success_output(tmp_path):
    p = write(tmp_path, "a: 1  \n")
    r = run("check", "--fix", "--quiet", p.name, cwd=tmp_path)
    assert r.returncode == 0
    assert r.stdout == r.stderr == ""
    assert p.read_bytes() == b"a: 1\n"


def test_zero_tab_width_is_rejected(tmp_path):
    p = write(tmp_path, "a:\n\tb: 1\n")
    r = run("check", "--fix", "--tab-width", "0", p.name, cwd=tmp_path)
    assert r.returncode == 2
    assert "--tab-width must be at least 1" in r.stderr
    assert p.read_bytes() == b"a:\n\tb: 1\n"


def test_fix_file_does_not_need_fchmod(tmp_path, monkeypatch):
    """Windows has no os.fchmod; --fix and --backup must still work."""
    from yaml_validator.validator import fix_file

    monkeypatch.delattr(os, "fchmod", raising=False)
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"
    assert (tmp_path / "f.yaml.bak").read_text() == "a: 1  \n"


def test_fix_file_refuses_when_target_replaced_mid_run(tmp_path, monkeypatch):
    import yaml_validator.validator as v
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_fix_content = v.fix_content

    def swap_then_fix(*args, **kwargs):
        other = tmp_path / "other"
        other.write_text("other: 2\n")
        os.replace(other, p)
        return real_fix_content(*args, **kwargs)

    monkeypatch.setattr(v, "fix_content", swap_then_fix)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        fix_file(p)
    assert p.read_text() == "other: 2\n"
    assert [x.name for x in tmp_path.iterdir()] == ["f.yaml"]


def test_fix_file_refuses_when_target_rewritten_in_place(tmp_path, monkeypatch):
    import yaml_validator.validator as v
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_fix_content = v.fix_content

    def rewrite_then_fix(*args, **kwargs):
        with open(p, "w") as stream:
            stream.write("newer: 3\n")
        return real_fix_content(*args, **kwargs)

    monkeypatch.setattr(v, "fix_content", rewrite_then_fix)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        fix_file(p)
    assert p.read_text() == "newer: 3\n"


def test_failed_backup_write_keeps_old_backup(tmp_path, monkeypatch):
    import yaml_validator.validator as v
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    bak = tmp_path / "f.yaml.bak"
    bak.write_text("old backup\n")

    def full_disk(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(v.os, "write", full_disk)
    with pytest.raises(OSError, match="No space"):
        fix_file(p, backup=True)
    assert bak.read_text() == "old backup\n"
    assert p.read_text() == "a: 1  \n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml", "f.yaml.bak"]


def _secondary_group():
    gids = (
        [g for g in os.getgroups() if g != os.getgid()]
        if hasattr(os, "getgroups")
        else []
    )
    return gids[0] if gids else None


@pytest.mark.skipif(_secondary_group() is None, reason="user has no secondary group")
def test_fix_file_keeps_group_on_file_not_backup(tmp_path):
    from yaml_validator.validator import fix_file

    gid = _secondary_group()
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    os.chown(p, -1, gid)
    fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"
    assert p.stat().st_gid == gid
    assert (tmp_path / "f.yaml.bak").stat().st_gid == os.getgid()


def test_fix_file_ignores_permission_error_from_chown(tmp_path, monkeypatch):
    """A group chown that fails is fine when the temp already has that group."""
    from yaml_validator.validator import fix_file

    def deny(*args, **kwargs):
        raise PermissionError("not root")

    monkeypatch.setattr(os, "fchown", deny, raising=False)
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    gid = p.stat().st_gid
    fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"
    assert p.stat().st_gid == gid


def test_valid_block_scalar_without_eof_newline_is_not_rewritten(tmp_path):
    """M3: adding the EOF newline would change the scalar's data."""
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_bytes(b"a: |\n  x\n  y")
    result = fix_file(p, backup=True)
    assert result.fixed == {}
    assert "would change the parsed data" in result.skipped["end-of-file-newline"]
    assert p.read_bytes() == b"a: |\n  x\n  y"
    assert not (tmp_path / "f.yaml.bak").exists()


def _xattrs_supported(path):
    try:
        os.setxattr(path, "user.probe", b"1")
    except (AttributeError, OSError):
        return False
    return True


def test_fix_restores_source_owner_before_mode(tmp_path, monkeypatch):
    """Setuid bits are applied only after the temp file has the source uid."""
    import yaml_validator.validator as v

    p = write(tmp_path, "a: 1  \n")
    p.chmod(0o4750)
    before = p.stat()
    owner = before.st_uid + 1
    real_fstat = os.fstat
    real_chmod = os.chmod
    events = []

    class _Owned:
        def __init__(self, result, uid):
            self._result = result
            self.st_uid = uid

        def __getattr__(self, name):
            return getattr(self._result, name)

    def fake_fstat(fd):
        result = real_fstat(fd)
        if result.st_ino == before.st_ino and stat.S_ISREG(result.st_mode):
            return _Owned(result, owner)
        return result

    def fake_fchown(fd, uid, gid):
        events.append(("fchown", uid, gid))

    def fake_chmod(path, mode):
        events.append(("chmod", stat.S_IMODE(mode)))
        real_chmod(path, mode)

    monkeypatch.setattr(v.os, "fstat", fake_fstat)
    monkeypatch.setattr(v.os, "fchown", fake_fchown, raising=False)
    monkeypatch.setattr(v.os, "chmod", fake_chmod)
    v.fix_file(p, backup=True)
    assert events[0] == ("fchown", owner, before.st_gid)
    assert events.index(("fchown", owner, before.st_gid)) < events.index(
        ("chmod", 0o4750)
    )
    assert events.count(("fchown", owner, before.st_gid)) == 1
    assert ("chmod", 0o600) in events
    assert p.read_text() == "a: 1\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o4750
    assert (tmp_path / "f.yaml.bak").read_text() == "a: 1  \n"


def test_fix_aborts_when_source_owner_cannot_be_restored(tmp_path, monkeypatch):
    """A chown to another uid that is not permitted must not replace the file."""
    import yaml_validator.validator as v

    p = write(tmp_path, "a: 1  \n")
    before = p.stat()
    foreign_uid = before.st_uid + 1
    real_fstat = os.fstat

    def fake_fstat(fd):
        result = real_fstat(fd)
        if result.st_ino == before.st_ino and stat.S_ISREG(result.st_mode):
            return os.stat_result(
                (
                    result.st_mode,
                    result.st_ino,
                    result.st_dev,
                    result.st_nlink,
                    foreign_uid,
                    result.st_gid,
                )
                + tuple(result)[6:]
            )
        return result

    def fake_fchown(fd, uid, gid):
        if uid != before.st_uid:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(v.os, "fstat", fake_fstat)
    monkeypatch.setattr(v.os, "fchown", fake_fchown, raising=False)
    with pytest.raises(OSError, match="could not preserve the file's owner") as info:
        v.fix_file(p, backup=True)
    assert info.value.filename == os.path.realpath(p)
    assert p.read_text() == "a: 1  \n"
    assert p.stat().st_uid == before.st_uid
    assert p.stat().st_mode == before.st_mode
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


def test_fix_aborts_when_source_group_cannot_be_restored(tmp_path, monkeypatch):
    """Caller owns the file but cannot set its group onto the replacement.

    The temp inherits a different group; installing it would keep that group
    while restoring the source mode. The original must stay untouched.
    """
    import yaml_validator.validator as v

    p = write(tmp_path, "a: 1  \n")
    before = p.stat()
    foreign_gid = before.st_gid + 1
    real_fstat = os.fstat

    def fake_fstat(fd):
        r = real_fstat(fd)
        if r.st_ino == before.st_ino and stat.S_ISREG(r.st_mode):
            return os.stat_result(
                (r.st_mode, r.st_ino, r.st_dev, r.st_nlink, r.st_uid, foreign_gid)
                + tuple(r)[6:]
            )
        return r

    def fake_fchown(fd, uid, gid):
        if gid != -1:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(v.os, "fstat", fake_fstat)
    monkeypatch.setattr(v.os, "fchown", fake_fchown, raising=False)
    with pytest.raises(OSError, match="could not preserve the file's group") as info:
        v.fix_file(p, backup=True)
    assert info.value.filename == os.path.realpath(p)
    assert p.read_text() == "a: 1  \n"
    assert p.stat().st_gid == before.st_gid
    assert p.stat().st_mode == before.st_mode
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


def test_source_rewritten_during_read_is_not_fixed(tmp_path, monkeypatch):
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_fstat = os.fstat
    calls = []

    def fstat_then_write(fd):
        calls.append(fd)
        if len(calls) == 1:
            r = real_fstat(fd)
            with open(p, "a") as stream:
                stream.write("b: 2\n")
            return r
        return real_fstat(fd)

    monkeypatch.setattr(v.os, "fstat", fstat_then_write)
    with pytest.raises(OSError, match="changed while it was being read"):
        v.fix_file(p)
    assert p.read_text() == "a: 1  \nb: 2\n"


def test_backup_is_0600_even_for_world_readable_source(tmp_path):
    import stat

    from yaml_validator.validator import fix_file

    p = write(tmp_path, "a: 1  \n")
    p.chmod(0o644)
    fix_file(p, backup=True)
    assert stat.S_IMODE((tmp_path / "f.yaml.bak").stat().st_mode) == 0o600
    assert stat.S_IMODE(p.stat().st_mode) == 0o644


def test_read_only_directory_error_names_the_target(tmp_path, capsys, monkeypatch):
    from yaml_validator.cli import main

    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("needs POSIX permissions as a non-root user")
    d = tmp_path / "ro"
    d.mkdir()
    p = write(d, "a: 1  \n")
    d.chmod(0o500)
    try:
        monkeypatch.setattr(sys, "argv", ["yaml-validator", "check", "--fix", str(p)])
        assert main() == 1
    finally:
        d.chmod(0o700)
    err = capsys.readouterr().err
    assert "Permission denied" in err
    assert f"'{p}'" in err
    assert ".f.yaml." not in err
    assert "Traceback" not in err
    assert p.read_text() == "a: 1  \n"


def _have_setfacl(path):
    import shutil

    if shutil.which("setfacl") is None:
        return False
    probe = subprocess.run(
        ["setfacl", "-m", "u:nobody:r", str(path)], capture_output=True
    )
    return probe.returncode == 0


def test_fchown_other_oserror_is_not_swallowed(tmp_path, monkeypatch):
    """A non-permission fchown failure still aborts the fix."""
    import errno

    import yaml_validator.validator as v

    def broken(fd, uid, gid):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(v.os, "fchown", broken, raising=False)
    p = write(tmp_path, "a: 1  \n")
    with pytest.raises(OSError, match="I/O error"):
        v.fix_file(p)
    assert p.read_text() == "a: 1  \n"


def test_windows_chmod_fallback_sets_mode(tmp_path, monkeypatch):
    """Y10: with no os.fchmod, the mode is applied with os.chmod."""
    import yaml_validator.validator as v

    monkeypatch.delattr(v.os, "fchmod", raising=False)
    p = write(tmp_path, "a: 1  \n")
    p.chmod(0o640)
    v.fix_file(p, backup=True)
    assert stat.S_IMODE(p.stat().st_mode) == 0o640
    assert stat.S_IMODE((tmp_path / "f.yaml.bak").stat().st_mode) == 0o600


def test_cleanup_failure_keeps_the_original_error(tmp_path, monkeypatch):
    """A temp that can't be deleted (e.g. read-only on Windows) must not
    mask the error that aborted the fix."""
    import errno

    import yaml_validator.validator as v

    def full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    def undeletable(path, *a, **k):
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(v.os, "write", full)
    monkeypatch.setattr(v.os, "unlink", undeletable)
    p = write(tmp_path, "a: 1  \n")
    with pytest.raises(OSError, match="No space left") as info:
        v.fix_file(p)
    assert info.value.filename == str(p)


def test_tab_only_line_is_not_a_tab_indentation_error():
    """M13: a line holding only a tab is blank, not tab indentation."""
    from yaml_validator.validator import _check_tab_indentation

    errors = []
    _check_tab_indentation("a: 1\n\t\nb: 2\n", "f.yaml", errors)
    assert errors == []
    _check_tab_indentation("a:\n\tb: 1\n", "f.yaml", errors)
    assert [e.line for e in errors] == [2]


def test_whitespace_only_line_is_left_as_is():
    """M14: _fix_trailing_line leaves blank (whitespace-only) lines alone."""
    from yaml_validator.validator import _fix_trailing_line

    assert _fix_trailing_line("   ") == "   "
    assert _fix_trailing_line("\t") == "\t"
    assert _fix_trailing_line("a: 1  ") == "a: 1"


def test_fix_content_empty_string_adds_no_newline():
    """M17: empty content must not gain an EOF newline."""
    from yaml_validator.validator import fix_content

    result = fix_content("")
    assert result.content == ""
    assert "end-of-file-newline" not in result.fixed
    assert "end-of-file-newline" not in result.skipped


def test_lone_cr_line_endings_are_normalised(tmp_path):
    """N10: old-Mac CR-only files are read as separate lines."""
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_bytes(b"a: 1  \rb: 2\r")
    result = fix_file(p)
    assert result.fixed.get("trailing-spaces") == 1
    assert p.read_bytes() == b"a: 1\nb: 2\n"


def test_same_size_rewrite_is_detected(tmp_path, monkeypatch):
    """N11: identity must include mtime/ctime, not just (dev, ino, size)."""
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_fix_content = v.fix_content

    def rewrite_same_size(*args, **kwargs):
        with open(p, "w") as stream:
            stream.write("z: 9  \n")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        return real_fix_content(*args, **kwargs)

    monkeypatch.setattr(v, "fix_content", rewrite_same_size)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        v.fix_file(p)
    assert p.read_text() == "z: 9  \n"


def test_backup_dir_is_resolved_once(tmp_path, monkeypatch):
    """X29: the .bak goes beside the file as resolved at the start, even if
    a symlinked directory is retargeted mid-run."""
    import yaml_validator.validator as v

    real, other = tmp_path / "real", tmp_path / "other"
    real.mkdir()
    other.mkdir()
    link = tmp_path / "dir"
    link.symlink_to(real)
    p = link / "f.yaml"
    p.write_text("a: 1  \n")
    real_fix_content = v.fix_content

    def retarget(*args, **kwargs):
        link.unlink()
        link.symlink_to(other)
        return real_fix_content(*args, **kwargs)

    monkeypatch.setattr(v, "fix_content", retarget)
    v.fix_file(p, backup=True)
    assert (real / "f.yaml.bak").read_text() == "a: 1  \n"
    assert not (other / "f.yaml.bak").exists()


def _fake_xattrs(monkeypatch, v, source, inherited):
    """Source has *source* xattrs; each new temp file inherits *inherited*."""
    temps = {}

    def key(fd):
        return os.fstat(fd).st_ino

    def listxattr(fd):
        return list(temps.setdefault(key(fd), dict(inherited)))

    def removexattr(fd, name):
        temps[key(fd)].pop(name)

    def setxattr(fd, name, value):
        temps.setdefault(key(fd), dict(inherited))[name] = value

    monkeypatch.setattr(v, "_read_xattrs", lambda fd: source)
    monkeypatch.setattr(v.os, "listxattr", listxattr, raising=False)
    monkeypatch.setattr(v.os, "removexattr", removexattr, raising=False)
    monkeypatch.setattr(v.os, "setxattr", setxattr, raising=False)
    return temps


def test_identity_includes_ctime(tmp_path, monkeypatch):
    """N11b: a same-size rewrite with mtime restored still changes ctime."""
    import time

    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_fix_content = v.fix_content

    def sneaky(*args, **kwargs):
        st = p.stat()
        time.sleep(0.01)
        with open(p, "w") as stream:
            stream.write("z: 9  \n")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        return real_fix_content(*args, **kwargs)

    monkeypatch.setattr(v, "fix_content", sneaky)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        v.fix_file(p)
    assert p.read_text() == "z: 9  \n"


def test_identity_includes_dev_and_inode(tmp_path, monkeypatch):
    """N11d: a different inode with identical size and times is a change."""
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_stat = os.stat

    class OtherInode:
        def __init__(self, r):
            self._r = r

        def __getattr__(self, name):
            return getattr(self._r, name)

        @property
        def st_ino(self):
            return self._r.st_ino + 1

    def other_inode(path, *args, **kwargs):
        r = real_stat(path, *args, **kwargs)
        return OtherInode(r) if str(path) == str(p) else r

    monkeypatch.setattr(v.os, "stat", other_inode)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        v.fix_file(p)
    assert p.read_text() == "a: 1  \n"


def test_backup_is_written_only_after_change_check(tmp_path, monkeypatch):
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    bak = tmp_path / "f.yaml.bak"
    bak.write_text("previous\n")
    real_fix_content = v.fix_content

    def rewrite(*args, **kwargs):
        with open(p, "w") as stream:
            stream.write("newer: 1\n")
        return real_fix_content(*args, **kwargs)

    monkeypatch.setattr(v, "fix_content", rewrite)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        v.fix_file(p, backup=True)
    assert bak.read_text() == "previous\n"


def test_target_rechecked_after_backup_write(tmp_path, monkeypatch):
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    real_replace = v._replace

    def replace_then_touch(temporary: str, dest: Path) -> None:
        real_replace(temporary, dest)
        if dest.name == "f.yaml.bak":
            p.write_text("newer: 1\n")

    monkeypatch.setattr(v, "_replace", replace_then_touch)
    with pytest.raises(OSError, match="changed while it was being fixed"):
        v.fix_file(p, backup=True)
    assert p.read_text() == "newer: 1\n"


def test_backup_enospc_names_the_bak(tmp_path, monkeypatch):
    """A failed .bak write names the .bak, not the target."""
    import errno

    import yaml_validator.validator as v

    p = write(tmp_path, "a: 1  \n")
    real_write = v.os.write
    calls = {"n": 0}

    def full_on_second(fd, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    monkeypatch.setattr(v.os, "write", full_on_second)
    with pytest.raises(OSError, match="No space") as info:
        v.fix_file(p, backup=True)
    assert info.value.filename == str(tmp_path / "f.yaml.bak")
    assert p.read_text() == "a: 1  \n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


def test_backup_in_read_only_symlink_dir_names_the_bak(tmp_path):
    """A writable target behind a symlink in a read-only dir: the error is
    about the .bak beside the link, not the (writable) target."""
    from yaml_validator.validator import fix_file

    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("needs POSIX permissions as a non-root user")
    real, ro = tmp_path / "real", tmp_path / "ro"
    real.mkdir()
    ro.mkdir()
    t = write(real, "a: 1  \n", name="t.yaml")
    link = ro / "t.yaml"
    link.symlink_to(t)
    ro.chmod(0o500)
    try:
        with pytest.raises(PermissionError) as info:
            fix_file(link, backup=True)
    finally:
        ro.chmod(0o700)
    assert info.value.filename == str(ro / "t.yaml.bak")
    assert t.read_text() == "a: 1  \n"


def test_backup_is_written_beside_the_symlink(tmp_path):
    """Z27: the .bak goes next to the given path, not next to its target."""
    from yaml_validator.validator import fix_file

    real, links = tmp_path / "real", tmp_path / "links"
    real.mkdir()
    links.mkdir()
    t = write(real, "a: 1  \n", name="t.yaml")
    link = links / "l.yaml"
    link.symlink_to(t)
    fix_file(link, backup=True)
    assert t.read_text() == "a: 1\n"
    assert (links / "l.yaml.bak").read_text() == "a: 1  \n"
    assert sorted(x.name for x in real.iterdir()) == ["t.yaml"]


def test_backup_aliasing_the_target_is_refused(tmp_path):
    from yaml_validator.validator import fix_file

    t = write(tmp_path, "a: 1  \n", name="f.yaml.bak")
    link = tmp_path / "f.yaml"
    link.symlink_to(t)
    with pytest.raises(OSError, match="is the file being fixed"):
        fix_file(link, backup=True)
    assert t.read_text() == "a: 1  \n"


def test_backup_onto_another_input_is_refused(tmp_path):
    a = write(tmp_path, "a: 1  \n", name="a.yaml")
    b = write(tmp_path, "b: 1  \n", name="a.yaml.bak")
    r = run("check", "--fix", "--backup", str(a), str(b), cwd=tmp_path)
    assert r.returncode == 1
    assert "would overwrite an input or schema file" in r.stderr
    assert a.read_text() == "a: 1  \n"
    assert b.read_text() == "b: 1  \n"


def test_backup_onto_schema_is_refused(tmp_path):
    """--schema pointing at an input's .bak must not be replaced."""
    a = write(tmp_path, "a: 1  \n", name="a.yaml")
    earlier = write(tmp_path, "z: 1  \n", name="z.yaml")
    schema = write(tmp_path, "type: object\n", name="a.yaml.bak")
    r = run(
        "check",
        "--fix",
        "--backup",
        "--schema",
        "a.yaml.bak",
        "z.yaml",
        "a.yaml",
        cwd=tmp_path,
    )
    assert r.returncode == 1
    assert "would overwrite an input or schema file" in r.stderr
    assert "a.yaml.bak" in r.stderr
    assert earlier.read_text() == "z: 1  \n"
    assert a.read_text() == "a: 1  \n"
    assert schema.read_text() == "type: object\n"
    assert not (tmp_path / "z.yaml.bak").exists()


def test_backup_onto_schema_symlink_is_refused(tmp_path):
    a = write(tmp_path, "a: 1  \n", name="a.yaml")
    schema = write(tmp_path, "type: object\n", name="schema.yaml")
    link = tmp_path / "a.yaml.bak"
    link.symlink_to(schema.name)
    r = run(
        "check",
        "--fix",
        "--backup",
        "--schema",
        str(link),
        str(a),
        cwd=tmp_path,
    )
    assert r.returncode == 1
    assert "would overwrite an input or schema file" in r.stderr
    assert a.read_text() == "a: 1  \n"
    assert schema.read_text() == "type: object\n"
    assert link.is_symlink()


def test_unrelated_schema_is_not_a_backup_collision(tmp_path):
    a = write(tmp_path, "a: 1  \n", name="a.yaml")
    schema = write(tmp_path, "type: object\n", name="schema.yaml")
    r = run(
        "check",
        "--fix",
        "--backup",
        "--schema",
        "schema.yaml",
        "a.yaml",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert a.read_text() == "a: 1\n"
    assert schema.read_text() == "type: object\n"
    assert (tmp_path / "a.yaml.bak").read_text() == "a: 1  \n"


def test_long_basename_is_fixed_within_name_max(tmp_path: Path) -> None:
    from yaml_validator.validator import fix_file

    p = write(tmp_path, "a: 1  \n", name="x" * 250 + ".yaml")
    fix_file(p)
    assert p.read_text() == "a: 1\n"


def _fake_xattrs(
    monkeypatch: pytest.MonkeyPatch, names: list[str], get_error: int = 0
) -> None:
    import yaml_validator.validator as v

    calls = {"n": 0}

    def listxattr(fd: int) -> list[str]:
        calls["n"] += 1
        return names if calls["n"] == 1 else []

    def getxattr(fd: int, name: str) -> bytes:
        if get_error:
            raise OSError(get_error, os.strerror(get_error))
        return b"v"

    monkeypatch.setattr(v.os, "listxattr", listxattr, raising=False)
    monkeypatch.setattr(v.os, "getxattr", getxattr, raising=False)


@pytest.mark.parametrize(
    ("names", "get_error", "message"),
    [
        (["system.posix_acl_access"], 0, "has a POSIX ACL"),
        (["system.posix_acl_default"], 0, "has a POSIX ACL"),
        (["security.ima"], 0, "has a security xattr"),
        (["security.selinux"], 0, "has a security xattr"),
        (["user.note"], 95, "cannot read xattr user.note"),
        (["user.note"], 5, "cannot read xattr user.note"),
    ],
)
def test_fix_refuses_files_with_risky_xattrs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    names: list[str],
    get_error: int,
    message: str,
) -> None:
    from yaml_validator.validator import fix_file

    p = write(tmp_path, "a: 1  \n")
    _fake_xattrs(monkeypatch, names, get_error)
    with pytest.raises(OSError, match=message) as info:
        fix_file(p, backup=True)
    assert "signature" not in str(info.value)
    assert p.read_text() == "a: 1  \n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


def test_readable_user_xattr_is_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yaml_validator.validator import fix_file

    p = write(tmp_path, "a: 1  \n")
    _fake_xattrs(monkeypatch, ["user.note"])
    fix_file(p)
    assert p.read_text() == "a: 1\n"


@pytest.mark.parametrize(
    "name", ["security.selinux", "security.ima", "security.capability"]
)
def test_inherited_security_xattr_leaves_source_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    import yaml_validator.validator as v

    def listxattr(fd: int) -> list[str]:
        listed.append(fd)
        return ["user.note"] if len(listed) == 1 else [name]

    def getxattr(fd: int, attr: str) -> bytes:
        return b"v"

    listed: list[int] = []
    monkeypatch.setattr(v.os, "listxattr", listxattr, raising=False)
    monkeypatch.setattr(v.os, "getxattr", getxattr, raising=False)
    p = write(tmp_path, "a: 1  \n")
    with pytest.raises(OSError, match=f"inherited security xattr {name}"):
        v.fix_file(p, backup=True)
    assert p.read_text() == "a: 1  \n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


def test_security_xattr_present_on_source_is_not_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yaml_validator.validator as v

    seen: dict[str, list[str]] = {"names": []}

    def names(fd: int) -> list[str]:
        return seen["names"]

    monkeypatch.setattr(v, "_xattr_names", names)
    seen["names"] = ["security.selinux", "user.note"]
    v._reject_inherited(0, Path("f.yaml"), ["security.selinux"])
    seen["names"] = ["security.selinux", "security.ima"]
    with pytest.raises(OSError, match="inherited security xattr security.ima"):
        v._reject_inherited(0, Path("f.yaml"), ["security.selinux"])


def _listxattr_sequence(
    monkeypatch: pytest.MonkeyPatch, source: int, temp: int, temp_names: list[str]
) -> None:
    import yaml_validator.validator as v

    calls = {"n": 0}

    def listxattr(fd: int) -> list[str]:
        calls["n"] += 1
        err = source if calls["n"] == 1 else temp
        if err:
            raise OSError(err, os.strerror(err))
        return [] if calls["n"] == 1 else temp_names

    monkeypatch.setattr(v.os, "listxattr", listxattr, raising=False)
    monkeypatch.setattr(v, "_XATTR_UNSUPPORTED", {95})


@pytest.mark.parametrize(("source", "temp"), [(95, 0), (0, 95)])
def test_unsupported_xattrs_are_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: int, temp: int
) -> None:
    from yaml_validator.validator import fix_file

    _listxattr_sequence(monkeypatch, source, temp, [])
    p = write(tmp_path, "a: 1  \n")
    fix_file(p)
    assert p.read_text() == "a: 1\n"


@pytest.mark.parametrize(
    ("source", "temp", "temp_names", "message"),
    [
        (5, 0, [], "cannot read extended attributes"),
        (0, 5, [], "Input/output error"),
        (0, 0, ["system.posix_acl_access"], "default ACL"),
    ],
)
def test_xattr_failures_leave_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: int,
    temp: int,
    temp_names: list[str],
    message: str,
) -> None:
    from yaml_validator.validator import fix_file

    _listxattr_sequence(monkeypatch, source, temp, temp_names)
    p = write(tmp_path, "a: 1  \n")
    with pytest.raises(OSError, match=message):
        fix_file(p)
    assert p.read_text() == "a: 1  \n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


@pytest.mark.parametrize("attr", ["listxattr", "fchown"])
def test_platform_without_the_call_is_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attr: str
) -> None:
    import yaml_validator.validator as v

    monkeypatch.delattr(v.os, attr, raising=False)
    p = write(tmp_path, "a: 1  \n")
    v.fix_file(p)
    assert p.read_text() == "a: 1\n"


def test_os_replace_failure_names_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml_validator.validator as v

    def denied(src: str, dst: object) -> None:
        raise PermissionError(13, "Permission denied", src)

    monkeypatch.setattr(v.os, "replace", denied)
    p = write(tmp_path, "a: 1  \n")
    with pytest.raises(PermissionError) as info:
        v.fix_file(p)
    assert info.value.filename == str(p)
    assert str(p) in str(info.value)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.yaml"]


def test_mkstemp_failure_names_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml_validator.validator as v

    def full(**kwargs: object) -> tuple[int, str]:
        raise OSError(28, "No space left on device", "/tmp/x")

    monkeypatch.setattr(v.tempfile, "mkstemp", full)
    p = write(tmp_path, "a: 1  \n")
    with pytest.raises(OSError) as info:
        v.fix_file(p)
    assert info.value.filename == str(p)


def test_group_that_cannot_be_kept_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml_validator.validator as v

    real_fstat = os.fstat

    def denied(fd: int, uid: int, gid: int) -> None:
        raise PermissionError(1, "denied")

    def other_group(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        values = list(st)
        values[5] = st.st_gid + 1
        return os.stat_result(values)

    def check_then_patch(target: Path, info: os.stat_result) -> None:
        raise AssertionError("unreachable")

    p = write(tmp_path, "a: 1  \n")
    real_refuse = v._refuse_xattrs

    def refuse_then_patch(fd: int, target: Path) -> None:
        real_refuse(fd, target)
        monkeypatch.setattr(v.os, "fstat", other_group)

    monkeypatch.setattr(v.os, "fchown", denied)
    monkeypatch.setattr(v, "_refuse_xattrs", refuse_then_patch)
    monkeypatch.setattr(v, "_check_unchanged", check_then_patch)
    with pytest.raises(OSError, match="could not preserve the file's group"):
        v.fix_file(p)
    assert p.read_text() == "a: 1  \n"


def test_multi_document_files_are_counted(tmp_path: Path) -> None:
    from yaml_validator.validator import validate_file

    p = write(tmp_path, "a: 1\n---\nb: 2\n")
    assert validate_file(p, multi_document=True).document_count == 2


def test_unrepresentable_data_is_not_the_same() -> None:
    from yaml_validator.validator import _same_data

    assert not _same_data([object()], [object()])


def test_cli_reports_fix_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml_validator.cli as cli

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("disk on fire")

    p = write(tmp_path, "a: 1  \n")
    monkeypatch.setattr(cli, "fix_file", broken)
    monkeypatch.setattr(sys, "argv", ["yaml-validator", "check", "--fix", str(p)])
    assert cli.main() == 1
    assert f"Error: {p}: disk on fire" in capsys.readouterr().err


def test_cli_missing_file_is_an_error(tmp_path: Path) -> None:
    r = run("check", "--fix", "missing.yaml", cwd=tmp_path)
    assert r.returncode == 1
    assert "file not found" in r.stderr
