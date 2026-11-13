"""Pydantic request/response models shared across routes."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Access = Literal["ro", "rw"]


# Starts with a letter or underscore: a leading '-' would be read as an option
# by useradd/smbpasswd, and a leading digit confuses tools that accept uids.
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _no_control_chars(value: str) -> str:
    # Passwords are fed to smbpasswd on stdin, one per line; a newline would
    # silently truncate the password that actually gets set.
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("must not contain control characters")
    return value


class LoginRequest(BaseModel):
    password: str = Field(max_length=1024)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=128)

    _password_clean = field_validator("password")(_no_control_chars)

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        v = v.strip()
        # Samba usernames map to real Unix accounts; keep them boring.
        if not _USERNAME_RE.fullmatch(v):
            raise ValueError("username must be lowercase letters, digits, - "
                             "and _, and start with a letter or _")
        return v


class UserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, max_length=256)
    display_name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_clean(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _no_control_chars(v)


class ShareMember(BaseModel):
    username: str
    access: Access = "rw"


class ShareCreate(BaseModel):
    path: str
    name: str = Field(min_length=1, max_length=64)
    members: list[ShareMember] = []
    # Share-wide fallback when per-user access isn't wanted.
    read_only: bool = False
    guest_ok: bool = False
    comment: str = ""

    @field_validator("name")
    @classmethod
    def _valid_share_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("share name is required")
        # These break smb.conf section headers or confuse clients.
        bad = set('[]"\'\\/:*?<>|\n\r\t')
        if bad & set(v):
            raise ValueError("share name contains an illegal character")
        return v


class ShareUpdate(BaseModel):
    members: Optional[list[ShareMember]] = None
    read_only: Optional[bool] = None
    guest_ok: Optional[bool] = None
    comment: Optional[str] = None


class PathRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    path: str
    new_name: str


class TransferRequest(BaseModel):
    sources: list[str]
    destination: str
    overwrite: bool = False


class DeleteRequest(BaseModel):
    paths: list[str]


class MkdirRequest(BaseModel):
    parent: str
    name: str
