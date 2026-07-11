"""LLM client — calls the configured LLM endpoint and parses the response.

Resilience features:
  - format="json" sent to Ollama (forces valid-JSON output, large drift reduction)
  - JSON-parse retry: if first response isn't valid JSON, ask the LLM again
    with a corrective prompt that includes the bad output
  - Per-module confidence scoring: each domain weighs different fields, so
    a travel passport doesn't get scored against logistics keys
  - Cleans up markdown code fences if the LLM returns them anyway
"""
import json
import os
import re
import requests
from datetime import datetime, timezone
from .result import ExtractionResult
from .prompt_registry import get_prompt
from ..normalization.dates import date_normalize


# Per-module "important fields" used to compute a confidence score.
# A field counts toward confidence only if it is present AND passes a light
# validity check (see _field_is_valid) — presence alone let obviously-garbage
# values ("size": "999 m", an empty container list of [{}]) score as confident.
_CONFIDENCE_FIELDS = {
    "logistics": ["tan_number", "vessel_name", "etd", "eta",
                  "shipping_company", "containers"],
    "travel":    ["document_type", "document_number", "full_name",
                  "dob", "nationality", "expiry_date"],
}

# Fields whose value must parse as a real date to count.
_DATE_FIELDS = {"etd", "eta", "expiry_date", "dob"}


def _field_is_valid(field: str, value) -> bool:
    """Return True if `value` is a plausible value for `field` (not just present)."""
    if value in (None, "", [], {}):
        return False
    if field == "containers":
        # A list of empty dicts is not real container data.
        if not isinstance(value, list):
            return False
        return any(
            isinstance(c, dict) and str(c.get("container_number") or "").strip()
            for c in value
        )
    if field in _DATE_FIELDS:
        return date_normalize(value) is not None
    return bool(str(value).strip())


def _strip_code_fences(s: str) -> str:
    """Remove ```json ... ``` wrappers if the LLM ignored the no-markdown rule."""
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        body = parts[1] if len(parts) > 1 else s
        body = body.strip()
        if body.startswith(("json", "JSON")):
            body = body[4:].strip()
        if body.endswith("```"):
            body = body[:-3].strip()
        return body
    return s


def _confidence(module: str, data: dict) -> float:
    fields = _CONFIDENCE_FIELDS.get(module)
    if not fields:
        # Unknown module — fall back to "any non-empty field is good"
        fields = list(data.keys()) or [""]
    if not fields:
        return 0.0
    filled = sum(1 for f in fields if _field_is_valid(f, data.get(f)))
    return round(filled / len(fields), 3)


class LLMClient:
    """Generic Ollama-style /api/generate client.

    Future cloud-provider variants (OpenAI, Anthropic) should subclass and
    override `_post_generate` only.
    """

    def __init__(self, ollama_url: str, model: str, timeout: int = 120,
                 temperature: float = 0.1, num_ctx: int | None = None):
        self.ollama_url = ollama_url
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        # Context window defaults to 16384 (was a hardcoded 8192 that silently
        # truncated multi-page documents). Overridable per deployment via
        # BRUNS_LLM_NUM_CTX so operators can match their model's real window.
        if num_ctx is None:
            try:
                num_ctx = int(os.environ.get("BRUNS_LLM_NUM_CTX", "16384") or "16384")
            except ValueError:
                num_ctx = 16384
        self.num_ctx = num_ctx
        # Reuse a single connection pool instead of opening a fresh TCP
        # connection for every generate/retry call.
        self._session = requests.Session()

    def _post_generate(self, prompt: str, force_json: bool = True) -> str:
        """POST to Ollama and return raw response string. Raises on transport error."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }
        if force_json:
            # Ollama-supported JSON-mode hint. Drastically reduces drift.
            payload["format"] = "json"

        r = self._session.post(self.ollama_url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("response", "")

    def extract(self, text: str, module: str, doc_type: str = "UNKNOWN") -> ExtractionResult:
        prompt_version, template = get_prompt(module, doc_type)
        prompt = template.format(text=text)

        # ── Attempt 1: format=json + drift-guard prompt ──
        try:
            raw = self._post_generate(prompt, force_json=True)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"LLM request failed: {e}")

        clean = _strip_code_fences(raw)
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            # ── Attempt 2: corrective retry ──
            retry_prompt = (
                "Your previous response was not valid JSON. "
                "You MUST return ONLY a single valid JSON object — no prose, "
                "no markdown, no code fences. The previous response was:\n\n"
                f"{raw[:2000]}\n\n"
                "Now return the same data as a clean JSON object:"
            )
            try:
                raw_retry = self._post_generate(retry_prompt, force_json=True)
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"LLM retry request failed: {e}")
            clean = _strip_code_fences(raw_retry)
            try:
                data = json.loads(clean)
                raw = raw_retry
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Failed to parse LLM JSON response after retry: {e}\n"
                    f"Original: {raw[:500]}\nRetry: {raw_retry[:500]}"
                )

        return ExtractionResult(
            data=data,
            confidence=_confidence(module, data),
            prompt_version=prompt_version,
            model=self.model,
            timestamp=datetime.now(timezone.utc),
            raw_response=raw,
            doc_type=doc_type,
        )
