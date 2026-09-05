from datetime import datetime
from pydantic import BaseModel, Field
class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh: str = Field(min_length=1, max_length=500)
    auth: str = Field(min_length=1, max_length=500)
    expiration_time: datetime | None = None
class PushSubscriptionResponse(BaseModel):
    endpoint: str
    enabled: bool

class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
