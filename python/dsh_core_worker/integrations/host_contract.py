from pydantic import BaseModel, ConfigDict, Field

from .portable import Kind


class AdapterDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    application_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    kinds: list[Kind] = Field(min_length=1, max_length=4)
    can_import: bool = True
    can_export: bool = True
