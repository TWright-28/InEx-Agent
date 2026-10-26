import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

from inex.errors import InexError

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database: Path
    github_token: str | None
    orchestrator: str
    classifier: str
    temperature: float
    classify_workers: int
    classify_retries: int
    profile: Path
    system_prompt: Path
    exports_dir: Path
    npm_cache_dir: Path
    checkpoints: Path
    logging_config: Path
    ollama_base: str
    num_ctx: int


DEFAULTS = {
    "database": Path("db/database.db"),
    "github_token": None,
    "orchestrator": "google_genai:gemini-2.5-flash",
    "classifier": "google_genai:gemini-2.5-pro",
    "temperature": 0.2,
    "classify_workers": 8,
    "classify_retries": 5,
    "profile": REPO_ROOT / "profiles" / "inex.yaml",
    "system_prompt": REPO_ROOT / "prompts" / "system_prompt.md",
    "exports_dir": Path("exports"),
    "npm_cache_dir": Path(".npm_doc_cache"),
    "checkpoints": Path("db/checkpoints.db"),
    "logging_config": REPO_ROOT / "loggingConfigs" / "config.json",
    "ollama_base": "http://localhost:11434",
    "num_ctx": 131072,
}

ENVIRONMENT = {
    "database": "DATABASE_URL",
    "github_token": "GITHUB_TOKEN",
    "orchestrator": "INEX_ORCHESTRATOR",
    "classifier": "INEX_CLASSIFIER",
    "temperature": "INEX_TEMPERATURE",
    "classify_workers": "INEX_WORKERS",
    "classify_retries": "INEX_RETRIES",
    "profile": "INEX_PROFILE",
    "ollama_base": "OLLAMA_BASE",
    "num_ctx": "INEX_NUM_CTX",
}

CASTS = {
    "database": Path,
    "temperature": float,
    "classify_workers": int,
    "classify_retries": int,
    "profile": Path,
    "system_prompt": Path,
    "exports_dir": Path,
    "npm_cache_dir": Path,
    "checkpoints": Path,
    "logging_config": Path,
    "num_ctx": int,
}


def _cast(name, value, source):
    cast = CASTS.get(name)
    if cast is None or value is None:
        return value
    try:
        return cast(value)
    except (TypeError, ValueError) as error:
        raise InexError(
            "invalid_setting", f"{source} must be a number, got {value!r}", setting=name
        ) from error


def load_settings(env_file: Path | None = Path(".env"), **overrides) -> Settings:
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file, override=False)
    values = dict(DEFAULTS)
    for name, variable in ENVIRONMENT.items():
        raw = os.environ.get(variable)
        if raw not in (None, ""):
            values[name] = _cast(name, raw, variable)
    known = {field.name for field in fields(Settings)}
    for name, value in overrides.items():
        if name not in known:
            raise InexError("invalid_setting", f"unknown setting {name!r}", setting=name)
        if value is not None:
            values[name] = _cast(name, value, name)
    return Settings(**values)
