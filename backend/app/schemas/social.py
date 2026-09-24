import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConnectRequest(BaseModel):
    brand_id: uuid.UUID


class ConnectResponse(BaseModel):
    """Send the user's browser to `authorization_url`."""

    authorization_url: str


class DisconnectRequest(BaseModel):
    brand_id: uuid.UUID


class SocialAccountRead(BaseModel):
    """Never includes tokens."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    platform: str
    account_name: str
    status: str
    token_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
