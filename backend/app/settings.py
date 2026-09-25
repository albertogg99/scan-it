from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_name: str = "Scan it!"
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/swiss_discover"
    llm_base_url: str = "https://api.publicai.co/v1"
    llm_model: str = "swiss-ai/apertus-v1.5-70b-thinking"
    llm_api_key: str | None = None
    swisscom_api_key: str | None = None
    public_ai_api_key: str | None = None
    llm_user_agent: str = "ScanIt/0.1 (hackathon demo)"
    llm_image_input: Literal["supported", "unsupported", "unknown"] = "unknown"
    llm_audio_input: Literal["supported", "unsupported", "unknown"] = "unknown"
    stt_base_url: str | None = None
    stt_model: str = "whisper-large-v3"
    stt_api_key: str | None = None
    stt_timeout_s: int = 60
    oidc_issuer: str = "http://localhost:8080/realms/swiss-discover"
    oidc_jwks_url: str = "http://keycloak:8080/realms/swiss-discover/protocol/openid-connect/certs"
    oidc_audience: str = "swiss-discover-api"
    cors_origins: str = "http://localhost:3000"
    max_tool_rounds: int = 6
    tool_timeout_s: int = 20
    mcp_connect_timeout_s: int = 8
    tool_output_max_chars: int = 8000
    llm_timeout_s: int = 120
    max_response_chars: int = 40000
    approval_timeout_s: int = 600
    max_upload_bytes: int = 5242880
    allowed_image_mime: str = "image/png,image/jpeg,image/webp,image/gif"
    allowed_audio_mime: str = "audio/wav"
    max_message_chars: int = 8000
    use_os_truststore: bool = False

    @property
    def api_key(self) -> str | None:
        return self.llm_api_key or self.swisscom_api_key or self.public_ai_api_key

    @property
    def audio_input(self) -> str:
        return "supported" if self.stt_base_url else self.llm_audio_input



@lru_cache
def get_settings() -> Settings:
    return Settings()