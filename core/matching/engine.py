from typing import List, Dict, Tuple
from pydantic import BaseModel
from .scorers import score_names, score_dob, score_nationality
from .thresholds import (
    AUTO_MERGE_THRESHOLD, REVIEW_THRESHOLD,
    WEIGHT_NAME, WEIGHT_DOB, WEIGHT_NATIONALITY,
)
from ..schemas.person import Person

class MatchResult(BaseModel):
    score: float
    status: str  # "AUTO_MERGED", "REVIEW", "NEW_IDENTITY"
    matched_person_id: int | None
    breakdown: Dict[str, float]

def calculate_similarity(person_a: Person, person_b: Person) -> Tuple[float, Dict[str, float]]:
    """Calculate overall similarity score between two persons."""
    name_score = score_names(person_a.normalized_name, person_b.normalized_name)
    dob_score = score_dob(person_a.dob, person_b.dob)
    nat_score = score_nationality(person_a.nationality, person_b.nationality)
    
    total_score = (name_score * WEIGHT_NAME) + (dob_score * WEIGHT_DOB) + (nat_score * WEIGHT_NATIONALITY)
    
    breakdown = {
        "name": name_score,
        "dob": dob_score,
        "nationality": nat_score
    }
    
    return total_score, breakdown

def resolve_identity(new_person: Person, existing_persons: List[Person]) -> MatchResult:
    """Compare a new person against existing persons and decide action."""
    if not existing_persons:
        return MatchResult(
            score=0.0,
            status="NEW_IDENTITY",
            matched_person_id=None,
            breakdown={}
        )
        
    best_score = 0.0
    best_match = None
    best_breakdown = {}
    
    for candidate in existing_persons:
        score, breakdown = calculate_similarity(new_person, candidate)
        if score > best_score:
            best_score = score
            best_match = candidate
            best_breakdown = breakdown
            
    if best_score >= AUTO_MERGE_THRESHOLD:
        status = "AUTO_MERGED"
    elif best_score >= REVIEW_THRESHOLD:
        status = "REVIEW"
    else:
        status = "NEW_IDENTITY"
        best_match = None
        
    return MatchResult(
        score=best_score,
        status=status,
        matched_person_id=best_match.id if best_match else None,
        breakdown=best_breakdown
    )
