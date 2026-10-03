from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pawspot.geo import PublicLocation


class EncounterPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_id: UUID
    animal_public_id: UUID
    author_public_id: UUID
    city_public_id: UUID
    observed_at: datetime
    comment: str | None
    approximate_location: PublicLocation | None
