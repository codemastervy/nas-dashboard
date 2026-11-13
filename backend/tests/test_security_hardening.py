"""Regression tests for the hardening around previews, names and headers."""
import os

import pytest
from pydantic import ValidationError

from app.models import UserCreate, UserUpdate
from app.routes import files as files_routes
from app.services import fs, samba


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    (root / "data" / "keep").mkdir(parents=True)
    (root / "data" / "keep" / "precious.txt").write_text("precious")
    monkeypatch.setattr(fs, "STORAGE_ROOT", root)
    return root


@pytest.mark.parametrize("media_type,expected", [
    ("image/png", True),
    ("video/mp4", True),
    ("audio/mpeg", True),
    ("application/pdf", True),
    ("text/plain", True),
    ("text/html", False),
    ("image/svg+xml", False),
    ("application/xhtml+xml", False),
    ("text/javascript", False),
    ("application/octet-stream", False),
])
def test_only_inert_types_are_served_inline(media_type, expected):
    assert files_routes._inline_safe(media_type) is expected


def test_deleting_a_symlink_removes_the_link_not_its_target(storage):
    link = storage / "data" / "shortcut"
    os.symlink(storage / "data" / "keep", link)

    result = fs.delete(["/data/shortcut"])

    assert result["deleted"] == ["/data/shortcut"]
    assert not os.path.lexists(link)
    assert (storage / "data" / "keep" / "precious.txt").read_text() == "precious"


def test_deleting_a_dangling_symlink_works(storage):
    link = storage / "data" / "dangling"
    os.symlink(storage / "data" / "gone", link)
    assert fs.delete(["/data/dangling"])["deleted"] == ["/data/dangling"]
    assert not os.path.lexists(link)


def test_volume_root_still_cannot_be_deleted(storage):
    result = fs.delete(["/data"])
    assert result["deleted"] == []
    assert (storage / "data").is_dir()


@pytest.mark.parametrize("name", ["evil\nname", "tab\tname", "bell\x07", "del\x7f"])
def test_control_characters_are_rejected_in_new_names(storage, name):
    with pytest.raises(fs.FsError):
        fs.mkdir("/data", name)


def test_share_path_with_newline_is_refused(storage, monkeypatch):
    evil = storage / "data" / "x\n   root preexec = touch pwned"
    evil.mkdir()
    monkeypatch.setattr(samba, "list_shares", lambda: [])
    with pytest.raises(samba.ShareError):
        samba.create_share("/data/" + evil.name, "evil", [])


def test_rendered_share_never_contains_an_injected_line():
    rendered = samba._render_share({
        "name": "ok", "real_path": "/mnt/storage/data/x\nroot preexec = id",
        "members": [],
    })
    assert not any(line.strip().startswith("root preexec")
                   for line in rendered.splitlines())


@pytest.mark.parametrize("username", ["-x", "--help", "1abc", "Abc", "a b", "a;b", ""])
def test_unsafe_usernames_are_rejected(username):
    with pytest.raises(ValidationError):
        UserCreate(username=username, password="pw")


@pytest.mark.parametrize("username", ["alice", "_svc", "bob-2", "a_b"])
def test_ordinary_usernames_are_accepted(username):
    assert UserCreate(username=username, password="pw").username == username


def test_passwords_with_newlines_are_rejected():
    with pytest.raises(ValidationError):
        UserCreate(username="alice", password="one\ntwo")
    with pytest.raises(ValidationError):
        UserUpdate(password="one\ntwo")
    assert UserUpdate(password=None).password is None
