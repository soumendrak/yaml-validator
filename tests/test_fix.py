"""Tests for ``--fix`` (issue #1)."""

import json
import os
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
        if Path(path).name == "new":  # temp file, later renamed into place
            creations.append((flags, mode, stat.S_IMODE(os.fstat(fd).st_mode)))
        return fd

    monkeypatch.setattr(os, "open", observe_open)
    fix_file(p, backup=True)
    # One temp for the .bak, one for the fixed file; both start at 0600.
    assert creations == [(os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, 0o600)] * 2
    assert bak.read_bytes() == b"a: 1  \r\n"
    assert stat.S_IMODE(bak.stat().st_mode) == 0o640


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
        if dir_fd is not None:  # resolve the dir_fd-relative temp name
            src = os.path.join(os.readlink(f"/proc/self/fd/{dir_fd}"), src)
        if kwargs.get("dst_dir_fd") is not None:
            dst = Path(os.readlink(f"/proc/self/fd/{kwargs['dst_dir_fd']}")) / dst
        src = Path(src)
        calls.append((src, dst))
        assert src.parent.parent == target.parent
        assert target.read_bytes() == b"a: 1  \n"
        assert src.read_bytes() == b"a: 1\n"
        assert stat.S_IMODE(src.stat().st_mode) == 0o640
        real_replace(src, dst)  # the temp is renamed by full path here

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
        with open(p, "w") as stream:  # same inode, new contents
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
def test_fix_file_keeps_group_on_file_and_backup(tmp_path):
    from yaml_validator.validator import fix_file

    gid = _secondary_group()
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    os.chown(p, -1, gid)
    fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"
    assert p.stat().st_gid == gid
    assert (tmp_path / "f.yaml.bak").stat().st_gid == gid


def test_fix_file_ignores_permission_error_from_chown(tmp_path, monkeypatch):
    from yaml_validator.validator import fix_file

    def deny(*args, **kwargs):
        raise PermissionError("not root")

    monkeypatch.setattr(os, "fchown", deny, raising=False)
    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"


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


def test_fix_file_keeps_extended_attributes(tmp_path):
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    if not _xattrs_supported(p):
        pytest.skip("filesystem has no user xattr support")
    os.setxattr(p, "user.label", b"keep-me")
    fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"
    assert os.getxattr(p, "user.label") == b"keep-me"
    assert os.getxattr(tmp_path / "f.yaml.bak", "user.label") == b"keep-me"


def test_fix_file_skips_xattrs_it_cannot_set(tmp_path, monkeypatch):
    from yaml_validator.validator import fix_file

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    monkeypatch.setattr(
        os, "listxattr", lambda path: ["security.selinux"], raising=False
    )
    monkeypatch.setattr(os, "getxattr", lambda path, name: b"x", raising=False)

    def deny(*args):
        raise PermissionError("no")

    monkeypatch.setattr(os, "setxattr", deny, raising=False)
    fix_file(p)
    assert p.read_text() == "a: 1\n"


def test_fix_file_sets_group_even_when_owner_change_is_denied(tmp_path, monkeypatch):
    """Caller is not the owner but is in the file's group (#10)."""
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    st = p.stat()
    owner, group = st.st_uid + 1, st.st_gid + 1
    real_fstat = os.fstat

    def fake_fstat(fd):
        r = real_fstat(fd)
        return os.stat_result(
            (r.st_mode, r.st_ino, r.st_dev, r.st_nlink, owner, group) + tuple(r)[6:]
        )

    calls = []

    def fake_fchown(fd, uid, gid):
        calls.append((uid, gid))
        if uid != -1:
            raise PermissionError("only root may change the owner")

    monkeypatch.setattr(v.os, "fstat", fake_fstat)
    monkeypatch.setattr(v.os, "fchown", fake_fchown, raising=False)
    monkeypatch.setattr(v, "_identity", lambda st: None)
    v.fix_file(p, backup=True)
    assert p.read_text() == "a: 1\n"
    # bak temp, then fixed temp: group set first, then owner attempted.
    assert calls == [(-1, group), (owner, -1)] * 2


@pytest.mark.skipif(not hasattr(os, "removexattr"), reason="no xattr support")
def test_fix_file_drops_xattrs_the_source_lacks(tmp_path, monkeypatch):
    """An ACL inherited from the directory must not survive on the temp."""
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    removed = []
    real_listxattr = os.listxattr
    monkeypatch.setattr(v, "_read_xattrs", lambda fd: {})
    monkeypatch.setattr(
        v.os,
        "listxattr",
        lambda fd: ["system.posix_acl_access"]
        if isinstance(fd, int)
        else real_listxattr(fd),
    )
    monkeypatch.setattr(v.os, "removexattr", lambda fd, name: removed.append(name))
    v.fix_file(p, backup=True)
    assert removed == ["system.posix_acl_access"] * 2


@pytest.mark.skipif(
    os.rename not in os.supports_dir_fd, reason="rename via dir_fd unsupported"
)
def test_fix_file_ignores_a_swapped_temp_directory(tmp_path, monkeypatch):
    """Renaming the private temp dir and planting a look-alike cannot
    redirect what gets renamed over the target."""
    import yaml_validator.validator as v

    p = tmp_path / "f.yaml"
    p.write_text("a: 1  \n")
    victim = tmp_path / "victim"
    victim.write_text("evil: 1\n")
    real_close = os.close
    swapped = []

    def close_then_swap(fd):
        real_close(fd)
        for entry in tmp_path.iterdir():
            if entry.name.startswith(".yv-") and not swapped:
                entry.rename(tmp_path / "moved")
                entry.mkdir()
                (entry / "new").symlink_to(victim)
                swapped.append(entry)

    monkeypatch.setattr(v.os, "close", close_then_swap)
    v.fix_file(p)
    assert swapped
    assert not p.is_symlink()
    assert p.read_text() == "a: 1\n"


def test_incomplete_xattr_snapshot_does_not_strip_attributes(monkeypatch):
    import yaml_validator.validator as v

    def boom(fd, name=None):
        raise PermissionError("policy")

    monkeypatch.setattr(
        v.os, "listxattr", lambda fd: ["security.selinux"], raising=False
    )
    monkeypatch.setattr(v.os, "getxattr", boom, raising=False)
    assert v._read_xattrs(0) is None
    removed = []
    monkeypatch.setattr(
        v.os, "removexattr", lambda fd, n: removed.append(n), raising=False
    )
    monkeypatch.setattr(v.os, "fchmod", lambda fd, mode: None, raising=False)
    monkeypatch.setattr(v.os, "fchown", lambda fd, u, g: None, raising=False)
    v._apply_metadata(0, "x", os.stat("."), None)
    assert removed == []


@pytest.mark.skipif(
    os.rename not in os.supports_dir_fd, reason="rename via dir_fd unsupported"
)
def test_backup_dir_is_bound_before_ancestor_symlink_is_retargeted(
    tmp_path, monkeypatch
):
    import yaml_validator.validator as v

    real = tmp_path / "real"
    other = tmp_path / "other"
    real.mkdir()
    other.mkdir()
    link = tmp_path / "dir"
    link.symlink_to(real)
    p = link / "f.yaml"
    p.write_text("a: 1  \n")
    real_mkdir = os.mkdir
    flipped = []

    def mkdir_then_retarget(name, mode=0o777, **kwargs):
        real_mkdir(name, mode, **kwargs)
        if not flipped:
            link.unlink()
            link.symlink_to(other)
            flipped.append(True)

    monkeypatch.setattr(v.os, "mkdir", mkdir_then_retarget)
    v.fix_file(p, backup=True)
    assert (real / "f.yaml.bak").read_text() == "a: 1  \n"
    assert not (other / "f.yaml.bak").exists()
