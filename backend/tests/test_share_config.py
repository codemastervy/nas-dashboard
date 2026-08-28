"""Generated smb.conf fragments.

The rendering is what actually grants and denies access, so the important
cases are asserted rather than eyeballed.
"""
from app.services import samba


def share(**overrides):
    base = {
        "id": "abc123", "name": "Documents", "path": "/data/Documents",
        "real_path": "/mnt/storage/data/Documents", "members": [],
        "read_only": False, "guest_ok": False, "comment": "", "created_at": 0,
    }
    base.update(overrides)
    return base


def test_per_user_read_and_write_lists():
    rendered = samba._render_share(share(members=[
        {"username": "sam", "access": "rw"},
        {"username": "alex", "access": "ro"},
    ]))
    assert "valid users = alex sam" in rendered
    assert "write list = sam" in rendered
    assert "read only = yes" in rendered      # base RO, write granted per user
    assert "guest ok = no" in rendered


def test_share_wide_read_only_wins():
    rendered = samba._render_share(share(
        read_only=True, members=[{"username": "sam", "access": "rw"}]))
    assert "read only = yes" in rendered
    assert "write list" not in rendered


def test_share_with_no_members_is_not_world_readable():
    """An empty member list must not become an open share."""
    rendered = samba._render_share(share(members=[]))
    assert "guest ok = no" in rendered
    assert "valid users = @nasusers" in rendered


def test_guest_share_is_explicit():
    rendered = samba._render_share(share(guest_ok=True))
    assert "guest ok = yes" in rendered


def test_newlines_cannot_inject_directives():
    """smb.conf is line-oriented; a newline in a comment would be a new setting."""
    rendered = samba._render_share(
        share(comment="harmless\n   admin users = attacker"))
    lines = [l.strip() for l in rendered.splitlines()]
    assert not any(l.startswith("admin users") for l in lines)


def test_empty_registry_renders_an_explicit_no_shares_file():
    rendered = samba.render_config([])
    assert "No shares" in rendered
    assert "[" not in rendered.split("# ---")[-1].replace("# ", "")


def test_generated_file_warns_against_hand_editing():
    assert "DO NOT EDIT BY HAND" in samba.render_config([])
