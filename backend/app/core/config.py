from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    PROJECT_NAME: str = "UrbanAgri-Copilot"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "Intelligent agent-driven platform for urban home vegetable gardening."

    DATABASE_URL: str = "sqlite:///./urban_agri_copilot.db"
    OPEN_METEO_BASE_URL: str = "https://api.open-meteo.com/v1"

    # Hugging Face access token for free plant-disease inference.
    HF_TOKEN: str | None = "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"

    # Alibaba Cloud Model Studio (DashScope) credentials.
    # Set DASHSCOPE_API_KEY in your .env file to enable qwen-vl-plus vision diagnosis.
    DASHSCOPE_API_KEY: str | None = None
    DASHSCOPE_ENDPOINT: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

    # Qoder Vision API credentials.
    # Set QODER_PERSONAL_ACCESS_TOKEN in your .env file to enable the Qoder Vision
    # diagnosis engine. QODER_VISION_ENDPOINT is optional and falls back to the
    # default Qoder Vision API URL when not provided.
    QODER_PERSONAL_ACCESS_TOKEN: str | None = None
    QODER_VISION_ENDPOINT: str | None = None

   # Telegram Bot API token for the Plant Monitoring & Alert System.
    # Create a bot via @BotFather and set TELEGRAM_BOT_TOKEN in your .env file.
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_DEFAULT_CHAT_ID: str | None = None

    ALLOWED_ORIGINS: list[str] = ["*"]


settings = Settings()