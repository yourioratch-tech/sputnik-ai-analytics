from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("data/runtime/sputnik.sqlite3")
    reports_dir: Path = Path("reports/jobs")
    config_dir: Path = Path("configs")
    tv_webhook_id: str | None = None
    tv_shared_secret: str | None = None
    news_webhook_id: str | None = None
    news_shared_secret: str | None = None
    news_allowed_domains: tuple[str, ...] = ()
    api_key: str | None = None
    operator_key: str | None = None
    admin_key: str | None = None
    public_base_url: str | None = None
    max_data_age_seconds: int = 900
    log_level: str = "INFO"
    scheduler_enabled: bool = True
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    granite_worker_count: int = 4
    granite_timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_path=Path(
                os.getenv("SPUTNIK_DATABASE_PATH", "data/runtime/sputnik.sqlite3")
            ),
            reports_dir=Path(os.getenv("SPUTNIK_REPORTS_DIR", "reports/jobs")),
            config_dir=Path(os.getenv("SPUTNIK_CONFIG_DIR", "configs")),
            tv_webhook_id=os.getenv("SPUTNIK_TV_WEBHOOK_ID"),
            tv_shared_secret=os.getenv("SPUTNIK_TV_SHARED_SECRET"),
            news_webhook_id=os.getenv("SPUTNIK_NEWS_WEBHOOK_ID"),
            news_shared_secret=os.getenv("SPUTNIK_NEWS_SHARED_SECRET"),
            news_allowed_domains=tuple(
                domain.strip().lower()
                for domain in os.getenv("SPUTNIK_NEWS_ALLOWED_DOMAINS", "").split(",")
                if domain.strip()
            ),
            api_key=os.getenv("SPUTNIK_API_KEY"),
            operator_key=os.getenv("SPUTNIK_OPERATOR_KEY"),
            admin_key=os.getenv("SPUTNIK_ADMIN_KEY"),
            public_base_url=os.getenv("SPUTNIK_PUBLIC_BASE_URL"),
            max_data_age_seconds=int(os.getenv("SPUTNIK_MAX_DATA_AGE_SECONDS", "900")),
            log_level=os.getenv("SPUTNIK_LOG_LEVEL", "INFO"),
            scheduler_enabled=os.getenv("SPUTNIK_SCHEDULER_ENABLED", "true").lower()
            in {"1", "true", "yes", "on"},
            lmstudio_base_url=os.getenv(
                "SPUTNIK_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"
            ).rstrip("/"),
            granite_worker_count=max(
                1, min(4, int(os.getenv("SPUTNIK_GRANITE_WORKERS", "4")))
            ),
            granite_timeout_seconds=max(
                10, min(600, int(os.getenv("SPUTNIK_GRANITE_TIMEOUT_SECONDS", "180")))
            ),
        )

    def missing_runtime_secrets(self) -> list[str]:
        names = []
        for name, value in (
            ("SPUTNIK_TV_WEBHOOK_ID", self.tv_webhook_id),
            ("SPUTNIK_TV_SHARED_SECRET", self.tv_shared_secret),
            ("SPUTNIK_NEWS_WEBHOOK_ID", self.news_webhook_id),
            ("SPUTNIK_NEWS_SHARED_SECRET", self.news_shared_secret),
            ("SPUTNIK_API_KEY", self.api_key),
            ("SPUTNIK_OPERATOR_KEY", self.operator_key),
            ("SPUTNIK_ADMIN_KEY", self.admin_key),
        ):
            if not value:
                names.append(name)
        return names
