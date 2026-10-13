import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _configured(value: str) -> bool:
    """True when a value is a real setting rather than a blank or template placeholder."""
    v = value.strip().lower()
    return bool(v) and "<" not in v and v not in {"change-me", "not-set", "none"}


@dataclass(frozen=True)
class Settings:
    # --- AMD vLLM endpoint (OpenAI-compatible) ---
    vllm_base_url: str = _env("VLLM_BASE_URL")
    vllm_api_key: str = _env("VLLM_API_KEY", "not-set")
    vllm_model: str = _env("VLLM_MODEL", "factorypilot-vl")
    vllm_timeout_s: float = float(_env("VLLM_TIMEOUT_S", "90"))
    hardware_label: str = _env("HARDWARE_LABEL", "AMD Instinct MI300X · ROCm · vLLM")

    # --- Evolus (https://api.evolus.ai) ---
    evolus_base_url: str = _env("EVOLUS_BASE_URL", "https://api.evolus.ai/api/v1")
    evolus_chat_base_url: str = _env("EVOLUS_CHAT_BASE_URL", "https://api.evolus.ai/v1")
    evolus_api_key: str = _env("EVOLUS_API_KEY")
    evolus_app_id: str = _env("EVOLUS_APPLICATION_ID")
    evolus_workflow_id: str = _env("EVOLUS_WORKORDER_WORKFLOW_ID")
    evolus_library_id: str = _env("EVOLUS_MANUALS_LIBRARY_ID")
    evolus_chat_model: str = _env("EVOLUS_CHAT_MODEL")  # e.g. the Evolus agent/model id wired to your AMD endpoint
    evolus_path_run_workflow: str = _env("EVOLUS_PATH_RUN_WORKFLOW", "/workflows/{workflow_id}/run")
    evolus_path_search_library: str = _env("EVOLUS_PATH_SEARCH_LIBRARY", "/knowledge-libraries/{library_id}/search")

    # --- Behaviour ---
    mode: str = _env("LLM_BACKEND", "auto").lower()  # auto | vllm | demo
    mock: bool = _env("MOCK_MODE", "0") == "1"
    confidence_floor: float = float(_env("CONFIDENCE_FLOOR", "0.6"))
    db_path: str = _env("DB_PATH", str(ROOT / "data" / "factorypilot.db"))
    data_dir: Path = field(default=ROOT / "data")
    sim_speed: float = float(_env("SIM_SECONDS_PER_SAMPLE", "3"))

    @property
    def use_vllm(self) -> bool:
        if self.mock or self.mode == "demo":
            return False
        return _configured(self.vllm_base_url)

    @property
    def evolus_live(self) -> bool:
        return not self.mock and _configured(self.evolus_api_key)

    @property
    def evolus_library_live(self) -> bool:
        return self.evolus_live and _configured(self.evolus_library_id)

    @property
    def evolus_workflow_live(self) -> bool:
        return self.evolus_live and _configured(self.evolus_workflow_id)

    @property
    def evolus_chat_live(self) -> bool:
        return self.evolus_live and _configured(self.evolus_chat_model)

    @property
    def backend_label(self) -> str:
        return f"{self.vllm_model} on {self.hardware_label}" if self.use_vllm else "Offline demo reasoner (no GPU)"


settings = Settings()
