import pytest

from solar_kiosk.auth import Auth, AuthError


def test_create_check_and_change(tmp_path):
    a = Auth(tmp_path / "auth.json")
    assert not a.has_password and not a.check("anything") and not a.session_ok("x")
    key = a.create("example-settings-pw")
    assert a.session_ok(key) and a.check("example-settings-pw") and not a.check("wrong-password")
    with pytest.raises(AuthError):
        a.create("another-password")
    with pytest.raises(AuthError, match="current password"):
        a.change("wrong-password", "another-password")
    new_key = a.change("example-settings-pw", "another-password")
    assert new_key != key and not a.session_ok(key) and a.session_ok(new_key)


def test_stored_as_a_hash_readable_only_by_the_owner(tmp_path):
    Auth(tmp_path / "auth.json").create("example-settings-pw")
    path = tmp_path / "auth.json"
    assert "example-settings-pw" not in path.read_text() and (path.stat().st_mode & 0o777) == 0o600
    assert Auth(path).check("example-settings-pw")  # survives a restart


def test_too_short(tmp_path):
    with pytest.raises(AuthError, match="8 characters"):
        Auth(tmp_path / "auth.json").create("short")


def test_reset_and_unreadable_file(tmp_path):
    a = Auth(tmp_path / "auth.json")
    key = a.create("example-settings-pw")
    a.reset()
    assert not a.has_password and not a.session_ok(key) and not (tmp_path / "auth.json").exists()
    (tmp_path / "auth.json").write_text("{broken")
    assert not Auth(tmp_path / "auth.json").has_password
