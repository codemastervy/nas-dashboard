"""Pydantic request/response models shared across routes."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Access = Literal["ro", "rw"]


class LoginRequest(BaseModel):
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1)
    display_name: str = ""

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        v = v.strip()
        # Samba usernames map to real Unix accounts; keep them boring.
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("username may only contain letters, digits, - and _")
        if v != v.lower():
            raise ValueError("username must be lowercase")
        return v


class UserUpdate(BaseModel):
    password: Optional[str] = None
    display_name: Optional[str] = None


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
