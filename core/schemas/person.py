from typing import List, Optional
from pydantic import BaseModel, field_validator, model_validator
from .base import AuditMixin
from ..normalization.names import name_normalize
from ..normalization.dates import date_normalize

class Person(AuditMixin):
    id: Optional[int] = None
    family_id: Optional[int] = None
    full_name: str
    # Defaults to "" so callers may omit it; the model_validator below fills it
    # from full_name using the canonical normalizer, guaranteeing every Person
    # carries a consistent normalized_name instead of relying on each call site
    # to normalize identically.
    normalized_name: str = ""
    dob: Optional[str] = None
    nationality: Optional[str] = None
    gender: Optional[str] = None

    @model_validator(mode="after")
    def _ensure_normalized(self):
        if not self.normalized_name and self.full_name:
            object.__setattr__(self, "normalized_name", name_normalize(self.full_name))
        return self

    @field_validator("dob", mode="before")
    @classmethod
    def norm_date(cls, v):
        return date_normalize(v)

class Family(AuditMixin):
    id: Optional[int] = None
    family_name: str
    head_person_id: Optional[int] = None
    case_reference: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    
    members: List[Person] = []
