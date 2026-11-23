"""Public account request schemas. Secret fields never appear in public profiles."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=256, repr=False)


class CreateAccount(Login):
    password: str = Field(min_length=12, max_length=256, repr=False)
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "user"] = "user"


class RegisterAccount(Login):
    password: str = Field(min_length=12, max_length=256, repr=False)
    display_name: str = Field(min_length=1, max_length=120)
    invitation: str = Field(min_length=32, max_length=256, repr=False)


class UpdateAccount(Input):
    active: bool
    expected_revision: int = Field(ge=0)


class Password(Input):
    password: str = Field(min_length=12, max_length=256, repr=False)


class Application(Input):
    id: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    origin: str = Field(max_length=200)
    callback: str = Field(max_length=200)
    bridge_id: str | None = Field(default=None, max_length=100)


class AccountProfile(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["admin", "user"]
    active: bool
    revision: int
    legacy: bool


class AccountList(BaseModel):
    items: list[AccountProfile]


class IdentityMe(BaseModel):
    account: AccountProfile
    permissions: list[str]


class ApplicationRegistrationResult(Application):
    token: str = Field(repr=False)
    account_id: str


class ApplicationDirectory(Input):
    application_id: str = Field(min_length=1, max_length=80)
    session: str | None = Field(default=None, min_length=1, max_length=256, repr=False)
    owner_user_id: str | None = Field(default=None, min_length=1, max_length=100)
    ids: list[str] = Field(default_factory=list, max_length=2000)
    usernames: list[str] = Field(default_factory=list, max_length=2000)


class DirectoryAccount(BaseModel):
    id: str
    username: str
    display_name: str
    active: bool


class DirectoryCaller(DirectoryAccount):
    platform_role: Literal["admin", "user"]
    revision: int


class ApplicationDirectoryResult(BaseModel):
    caller: DirectoryCaller
    profiles: list[DirectoryAccount]
