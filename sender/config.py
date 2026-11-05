from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Mock Sender configuration. Neither URL nor key is ever sent to the browser."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    integration_api_url: str = "http://localhost:8000"
    integration_api_key: str = ""
    request_timeout_s: float = 60.0
    poll_timeout_s: float = 60.0
    history_limit: int = 500
