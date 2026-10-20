from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    upload_dir: str = os.path.join(os.path.dirname(__file__), "uploads")
    font_path: str = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "fonts", "Rubik-Bold.ttf")


settings = Settings()
