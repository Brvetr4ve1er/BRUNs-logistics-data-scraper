from .processor import PipelineProcessor
from ..extraction.llm_client import LLMClient
import os
import json


def get_db_path(module: str) -> str:
    """Resolve db path based on module."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.environ.get("BRUNS_DATA_DIR", os.path.join(base_dir, "data"))

    if module == "logistics":
        return os.path.join(data_dir, "logistics.db")
    elif module == "travel":
        return os.path.join(data_dir, "travel.db")
    else:
        return os.path.join(data_dir, f"{module}.db")


def _resolve_llm_settings() -> dict:
    """Read `data/.llm_config.json` (set by the LLM modal in the UI).

    Falls back to env vars (`OLLAMA_URL`, `OLLAMA_MODEL`), then to safe defaults.
    Returns a dict with everything LLMClient needs:
        provider, model, generate_url, timeout, temperature
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg_path = os.path.join(base_dir, "data", ".llm_config.json")

    cfg: dict = {
        "provider":    "ollama",
        "base_url":    "http://localhost",
        "port":        11434,
        "model":       "llama3",
        "api_key":     "",
        "temperature": 0.1,
        "timeout":     120,
    }

    # 1. Overlay user-saved settings (from the LLM modal)
    if os.path.exists(cfg_path):
        try:
            cfg.update(json.loads(open(cfg_path, encoding="utf-8").read()))
        except Exception:
            pass

    # 2. Env vars override (legacy / Docker / CI)
    if os.environ.get("OLLAMA_MODEL"):
        cfg["model"] = os.environ["OLLAMA_MODEL"]

    legacy_full_url = os.environ.get("OLLAMA_URL")  # full URL incl. /api/generate
    if legacy_full_url:
        cfg["generate_url"] = legacy_full_url
    else:
        base = (cfg["base_url"] or "http://localhost").rstrip("/")
        if not base.startswith("http"):
            base = f"http://{base}"
        if cfg.get("port") and ":" not in base.split("//", 1)[1]:
            base = f"{base}:{cfg['port']}"
        cfg["generate_url"] = base + "/api/generate"

    return cfg


def route_file(file_path: str, module: str, doc_type: str = "UNKNOWN"):
    """Route a file to the pipeline.

    LLM settings come from `data/.llm_config.json` (set via the UI modal),
    with env-var overrides (`OLLAMA_URL`, `OLLAMA_MODEL`) for legacy callers.
    """
    db_path = get_db_path(module)
    cfg = _resolve_llm_settings()

    from ..storage.db import init_schema
    from ..storage.migrations import run_migrations
    if not os.path.exists(db_path):
        init_schema(db_path)
    # Always run migrations — a DB created here mid-session (rather than at
    # startup) would otherwise never get the 004+ dashboard/tan indexes.
    run_migrations(db_path)

    llm_client = LLMClient(
        ollama_url=cfg["generate_url"],
        model=cfg["model"],
        timeout=int(cfg.get("timeout", 120)),
        temperature=float(cfg.get("temperature", 0.1)),
    )
    processor = PipelineProcessor(llm_client, db_path)
    return processor.process_file(file_path, module, doc_type)
