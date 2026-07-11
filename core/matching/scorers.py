from rapidfuzz import fuzz
from typing import Optional

from .thresholds import MISSING_FIELD_SCORE


def score_names(name_a: str, name_b: str) -> float:
    """Return a name-similarity score between 0.0 and 1.0.

    Blends two RapidFuzz metrics to avoid over-merging:
      - token_set_ratio handles reordering and middle names, but returns 100
        for a pure subset ("JOHN" vs "JOHN MICHAEL DOE"), which would merge a
        bare first name into any fuller name.
      - token_sort_ratio compares the full sorted strings and so penalizes that
        length mismatch.
    Weighting token_sort_ratio higher pulls subset "matches" down out of the
    auto-merge band while still tolerating genuine reordering/extra tokens.
    """
    if not name_a or not name_b:
        return 0.0
    a, b = name_a.upper(), name_b.upper()
    set_ratio = fuzz.token_set_ratio(a, b)
    sort_ratio = fuzz.token_sort_ratio(a, b)
    return (0.4 * set_ratio + 0.6 * sort_ratio) / 100.0


def score_dob(dob_a: Optional[str], dob_b: Optional[str]) -> float:
    """Exact match on DOB is a strong indicator; absence is a mild penalty."""
    if not dob_a or not dob_b:
        return MISSING_FIELD_SCORE  # missing corroboration, not neutral
    return 1.0 if dob_a == dob_b else 0.0


def score_nationality(nat_a: Optional[str], nat_b: Optional[str]) -> float:
    if not nat_a or not nat_b:
        return MISSING_FIELD_SCORE
    return 1.0 if nat_a.upper() == nat_b.upper() else 0.0
