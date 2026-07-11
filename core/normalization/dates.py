import re
from datetime import date


def _expand_2digit_year(year: str) -> str:
    """Expand a 2-digit year to 4 digits with a sliding pivot.

    Uses the POSIX-style pivot (00-68 -> 2000-2068, 69-99 -> 1969-1999). This
    keeps near-future logistics dates ('26' -> 2026) in the 2000s while pushing
    plausible dates of birth ('85' -> 1985) back to the 1900s, instead of the
    previous unconditional '20'+YY which mapped every DOB into the future.
    """
    if len(year) != 2:
        return year
    yy = int(year)
    return f"20{yy:02d}" if yy <= 68 else f"19{yy:02d}"


def _iso_or_none(year: str, month: str, day: str) -> str | None:
    """Build a YYYY-MM-DD string, returning None if it isn't a real calendar date."""
    try:
        iso = date(int(year), int(month), int(day)).isoformat()
    except (ValueError, TypeError):
        return None
    return iso


def date_normalize(v: str) -> str | None:
    """Normalize various date formats into ISO8601 (YYYY-MM-DD).

    Returns None for empty/sentinel values AND for strings that parse to an
    impossible calendar date (e.g. '31/13/2026'), so callers never store a
    syntactically-ISO-but-invalid date.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "n/a", "", "none"):
        return None

    # Already YYYY-MM-DD? Validate it's a real date before trusting it.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        y, mo, d = s.split("-")
        return _iso_or_none(y, mo, d)

    # Try DD-MMM-YY (e.g. "11-Mar-26")
    months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
              "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
              "nov": "11", "dec": "12"}
    m = re.match(r"^(\d{1,2})[-/\s]([A-Za-z]{3,})[-/\s](\d{2,4})", s)
    if m:
        day, mon, year = m.groups()
        mon_num = months.get(mon.lower()[:3])
        if mon_num:
            year = _expand_2digit_year(year) if len(year) == 2 else year
            return _iso_or_none(year, mon_num, day)

    # Try DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return _iso_or_none(y, mo, d)

    return None
