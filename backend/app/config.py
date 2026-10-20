from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./vb.db"
    storage_dir: str = "./storage"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p



settings = Settings()
