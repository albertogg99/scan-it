from app.settings import Settings


def test_api_key_precedence_and_unverified_images(monkeypatch):
    for name in ("LLM_BASE_URL", "LLM_IMAGE_INPUT"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        _env_file=None,
        llm_api_key=None,
        swisscom_api_key="swisscom",
        public_ai_api_key="public",
    )
    assert settings.api_key == "swisscom"
    assert settings.llm_base_url == "https://api.publicai.co/v1"
    assert settings.llm_image_input == "unknown"