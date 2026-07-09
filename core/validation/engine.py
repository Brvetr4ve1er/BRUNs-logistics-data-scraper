import re
from datetime import date, datetime

from ..normalization.dates import date_normalize
from ..normalization.codes import ALLOWED_CONTAINER_SIZES


def _parse_date(value):
    """Normalize then parse a date value into a datetime.date, or None if unparseable."""
    if value is None:
        return None
    iso = date_normalize(value)
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).date()
    except (ValueError, TypeError):
        return None


class ValidationRule:
    def __init__(self, rule_id: str, description: str, severity: str = "warning"):
        self.rule_id = rule_id
        self.description = description
        self.severity = severity

    def validate(self, data: dict) -> list[str]:
        """Return list of violations (empty if valid)."""
        raise NotImplementedError

class DateSequenceRule(ValidationRule):
    """ETD must be before ETA"""
    def __init__(self):
        super().__init__("date_sequence", "Shipment date must be before arrival date", "error")

    def validate(self, data: dict) -> list[str]:
        etd = data.get("etd")
        eta = data.get("eta")
        etd_d = _parse_date(etd)
        eta_d = _parse_date(eta)
        if etd_d and eta_d and etd_d >= eta_d:
            return [f"ETD ({etd}) should be before ETA ({eta})"]
        return []

class DeliveryDateRule(ValidationRule):
    """Delivery date should be after arrival"""
    def __init__(self):
        super().__init__("delivery_sequence", "Delivery date should be after arrival date", "warning")

    def validate(self, data: dict) -> list[str]:
        eta = data.get("eta")
        delivery = data.get("date_livraison")
        if eta and delivery and delivery < eta:
            return [f"Delivery date ({delivery}) should be after ETA ({eta})"]
        return []

class SurestarioeDaysRule(ValidationRule):
    """Surestarie days should be non-negative"""
    def __init__(self):
        super().__init__("surestarie_positive", "Surestarie days should be non-negative", "warning")

    def validate(self, data: dict) -> list[str]:
        days = data.get("nbr_jours_surestarie_estimes", 0)
        if isinstance(days, (int, float)) and days < 0:
            return [f"Estimated surestarie days ({days}) cannot be negative"]
        return []

class ContainerNumberFormatRule(ValidationRule):
    """Container number must be 4 letters + 7 digits"""
    def __init__(self):
        super().__init__("container_format", "Container number must be valid format (4 letters + 7 digits)", "error")

    def validate(self, data: dict) -> list[str]:
        cnum = data.get("container_number", "")
        if cnum and not re.match(r"^[A-Z]{4}\d{7}$", str(cnum)):
            return [f"Container number '{cnum}' is invalid format"]
        return []

class TanNumberFormatRule(ValidationRule):
    """TAN should follow TAN/XXXX/YYYY format"""
    def __init__(self):
        super().__init__("tan_format", "TAN should be in format TAN/XXXX/YYYY", "warning")

    def validate(self, data: dict) -> list[str]:
        tan = data.get("tan_number")
        if tan and not re.match(r"^TAN/\d{4}/\d{4}$", str(tan)):
            return [f"TAN '{tan}' does not follow standard format"]
        return []

class WeightSanityRule(ValidationRule):
    """Net weight must not exceed gross weight, and gross weight must be within a sane range"""
    MAX_GROSS_KG = 100000  # ISO container payloads top out well below 100t

    def __init__(self):
        super().__init__("weight_sanity", "Net weight must be <= gross weight <= maximum payload", "error")

    def validate(self, data: dict) -> list[str]:
        violations = []
        net = data.get("poids_net")
        gross = data.get("poids_brut")
        if isinstance(net, (int, float)) and isinstance(gross, (int, float)) and net > gross:
            violations.append(f"Net weight ({net}) cannot exceed gross weight ({gross})")
        if isinstance(gross, (int, float)) and gross > self.MAX_GROSS_KG:
            violations.append(f"Gross weight ({gross}) exceeds maximum payload ({self.MAX_GROSS_KG})")
        if isinstance(net, (int, float)) and net < 0:
            violations.append(f"Net weight ({net}) cannot be negative")
        if isinstance(gross, (int, float)) and gross < 0:
            violations.append(f"Gross weight ({gross}) cannot be negative")
        return violations


class ContainerSizeRule(ValidationRule):
    """Container size must be one of the allowed normalized sizes"""
    def __init__(self):
        super().__init__("container_size", "Container size must be a recognised size", "error")

    def validate(self, data: dict) -> list[str]:
        size = data.get("taille")
        if size and str(size) not in ALLOWED_CONTAINER_SIZES:
            return [f"Container size '{size}' is not a recognised size"]
        return []


class PortCodeFormatRule(ValidationRule):
    """Port codes should follow the UN/LOCODE format (5 letters, e.g. CIABJ)"""
    def __init__(self):
        super().__init__("port_code_format", "Port code should follow UN/LOCODE format (5 letters)", "warning")

    def validate(self, data: dict) -> list[str]:
        violations = []
        for field in ("port_chargement", "port_dechargement"):
            code = data.get(field)
            if code and not re.match(r"^[A-Z]{5}$", str(code)):
                violations.append(f"Port code '{code}' for {field} does not follow UN/LOCODE format")
        return violations


LOGISTICS_RULES = [
    DateSequenceRule(),
    DeliveryDateRule(),
    SurestarioeDaysRule(),
    ContainerNumberFormatRule(),
    TanNumberFormatRule(),
    WeightSanityRule(),
    ContainerSizeRule(),
    PortCodeFormatRule(),
]

# Document-number formats per travel document type (ISO/ICAO conventions, kept lenient
# but non-empty/structured). Unknown doc types only require a non-empty value.
TRAVEL_DOC_NUMBER_PATTERNS = {
    "passport": r"^[A-Z0-9]{6,9}$",
    "id": r"^[A-Z0-9]{5,15}$",
    "visa": r"^[A-Z0-9]{6,12}$",
    "bank": r"^[A-Z0-9]{6,34}$",
    "registry": r"^[A-Z0-9/\-]{4,20}$",
}


class ExpiryAfterIssueRule(ValidationRule):
    """Expiry date must be after issue date"""
    def __init__(self):
        super().__init__("expiry_after_issue", "Expiry date must be after issue date", "error")

    def validate(self, data: dict) -> list[str]:
        issue = data.get("issue_date")
        expiry = data.get("expiry_date")
        issue_d = _parse_date(issue)
        expiry_d = _parse_date(expiry)
        if issue_d and expiry_d and expiry_d <= issue_d:
            return [f"Expiry date ({expiry}) must be after issue date ({issue})"]
        return []


class DobInPastRule(ValidationRule):
    """Date of birth must be in the past"""
    def __init__(self):
        super().__init__("dob_in_past", "Date of birth must be in the past", "error")

    def validate(self, data: dict) -> list[str]:
        dob = data.get("dob")
        dob_d = _parse_date(dob)
        if dob_d and dob_d > date.today():
            return [f"Date of birth ({dob}) cannot be in the future"]
        return []


class DobBeforeExpiryRule(ValidationRule):
    """Date of birth must be before document expiry"""
    def __init__(self):
        super().__init__("dob_before_expiry", "Date of birth must be before document expiry", "error")

    def validate(self, data: dict) -> list[str]:
        dob = data.get("dob")
        expiry = data.get("expiry_date")
        dob_d = _parse_date(dob)
        expiry_d = _parse_date(expiry)
        if dob_d and expiry_d and dob_d >= expiry_d:
            return [f"Date of birth ({dob}) must be before document expiry ({expiry})"]
        return []


class DocumentNumberRule(ValidationRule):
    """Document number must be present and well-formed for its type"""
    def __init__(self):
        super().__init__("document_number", "Document number must be present and well-formed", "error")

    def validate(self, data: dict) -> list[str]:
        num = data.get("document_number")
        if num is None or str(num).strip() == "":
            return ["Document number is missing"]
        doc_type = str(data.get("doc_type", "")).strip().lower()
        pattern = TRAVEL_DOC_NUMBER_PATTERNS.get(doc_type)
        if pattern and not re.match(pattern, str(num).strip().upper()):
            return [f"Document number '{num}' is invalid for doc_type '{doc_type}'"]
        return []


TRAVEL_RULES = [
    ExpiryAfterIssueRule(),
    DobInPastRule(),
    DobBeforeExpiryRule(),
    DocumentNumberRule(),
]


def validate_extraction(data: dict, module: str = "logistics") -> dict:
    """
    Run validation rules and return issues. Pure function, no DB side effects.
    """
    issues = []
    
    if module == "logistics":
        rules = LOGISTICS_RULES
    elif module == "travel":
        rules = TRAVEL_RULES
    else:
        rules = []

    # Could be shipment data or container data
    # To handle lists of containers in a shipment:
    if "containers" in data and isinstance(data["containers"], list):
        for c in data["containers"]:
            for rule in rules:
                for violation in rule.validate(c):
                    issues.append({
                        "rule_id": rule.rule_id,
                        "description": violation,
                        "severity": rule.severity,
                        "entity": c.get("container_number", "Unknown Container")
                    })
    
    # Also run on the top-level data
    for rule in rules:
        for violation in rule.validate(data):
            issues.append({
                "rule_id": rule.rule_id,
                "description": violation,
                "severity": rule.severity,
                "entity": data.get("tan_number", "Shipment")
            })

    return {
        "is_valid": len([i for i in issues if i["severity"] == "error"]) == 0,
        "error_count": len([i for i in issues if i["severity"] == "error"]),
        "warning_count": len([i for i in issues if i["severity"] == "warning"]),
        "issues": issues,
    }
