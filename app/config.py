from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBAGG_", env_file=".env", extra="ignore")

    data_dir: Path = Path("/data")
    mihomo_bin: str = "/usr/local/bin/mihomo"

    # Внешний адрес сервиса для ссылок на подписки, например https://sub.example.com.
    # Пусто - берется адрес, по которому открыта админка
    public_url: str = ""

    admin_user: str = "admin"
    admin_password: str = "change-me-please"

    fetch_interval_min: int = 60
    check_interval_min: int = 30
    check_concurrency: int = 16
    check_timeout_ms: int = 6000
    dead_after_fails: int = 3

    # Порт API ядра, которое поднимается на время проверки
    checker_api_port: int = 19090
    # DNS, которым ядро проверки резолвит адреса нод
    checker_dns: str = "1.1.1.1,8.8.8.8,77.88.8.8"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "subagg.sqlite3"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.work_dir.mkdir(parents=True, exist_ok=True)
