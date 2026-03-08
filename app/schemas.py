from typing import Literal
from pydantic import BaseModel, Field

# Only request-body schemas remain.
# All API responses are plain structured text (text/plain).


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=200)
    status: Literal["open", "friends_only", "do_not_disturb"] = "open"


class SendRequest(BaseModel):
    to_id: int
    content: str = Field(..., min_length=1, max_length=1000)


class StatusUpdate(BaseModel):
    status: Literal["open", "friends_only", "do_not_disturb"]
