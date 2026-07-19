from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Значения зашиты в код — .env не обязателен.
    # Если .env есть, его переменные могут переопределить дефолты.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    database_url: str = Field(default="sqlite:///./bot.db", alias="DATABASE_URL")

    telegram_api_id: int = Field(default=33132026, alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(
        default="87e91441ec4557fab28615acae8f4d52",
        alias="TELEGRAM_API_HASH",
    )
    telegram_session_name: str = Field(default="tg_user_session", alias="TELEGRAM_SESSION_NAME")

    # Bot 1 (основной канал)
    telegram_channel_id: int = Field(default=-1001838832012, alias="TELEGRAM_CHANNEL_ID")
    telegram_notify_bot_token: str = Field(
        default="8963889265:AAFafWk4X7nFHtgImme4n4sLc3lBAYRHVqI",
        alias="TELEGRAM_NOTIFY_BOT_TOKEN",
    )
    telegram_notify_username: str = Field(default="fetwjdf", alias="TELEGRAM_NOTIFY_USERNAME")

    # Bot 2 (второй канал)
    telegram_channel_id_2: int | None = Field(default=-1001732065792, alias="TELEGRAM_CHANNEL_ID_2")
    telegram_notify_bot_token_2: str = Field(
        default="8937694223:AAGpCITzpuMWwtChWNXXScJ211MiQFnMJXE",
        alias="TELEGRAM_NOTIFY_BOT_TOKEN_2",
    )
    telegram_notify_username_2: str = Field(default="fetwjdf", alias="TELEGRAM_NOTIFY_USERNAME_2")

    bybit_api_key: str = Field(default="a0ZbuhqnUTNDhAtjI8", alias="BYBIT_API_KEY")
    bybit_api_secret: str = Field(
        default="uJd2nH4HtYVz2K1Qs0ljh5MIf7BxoUVlzROq",
        alias="BYBIT_API_SECRET",
    )
    bybit_testnet: bool = Field(default=False, alias="BYBIT_TESTNET")

    default_margin_usdt: float = Field(default=20.0, alias="DEFAULT_MARGIN_USDT")
    default_take_profit_adjust_pct: float = Field(default=0.05, alias="DEFAULT_TAKE_PROFIT_ADJUST_PCT")
    default_close_at_tp1_pct: float = Field(default=100.0, alias="DEFAULT_CLOSE_AT_TP1_PCT")

    openai_api_key: str = Field(
        default=(
            "sk-proj-iuVeYDmA4XJ-mKk_fABu9dmuTB2ZAkWOa3e0MtfgKVCUphmbOIF58hdnoUZWSdu7XAy64O5SIYT3Blbk"
            "FJI1nJF4Nw15wlPmZUIahBR13C08jMmEX1HWINp8WbeW7trb8gwAuK0CfhskRxLE1k4fMN5jqtsA"
        ),
        alias="OPENAI_API_KEY",
    )
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # Вход на сайт / админку
    site_password: str = Field(default="1958", alias="SITE_PASSWORD")
    site_auth_secret: str = Field(
        default="bybit-bot-site-auth-secret-1958",
        alias="SITE_AUTH_SECRET",
    )

    @field_validator("telegram_channel_id_2", mode="before")
    @classmethod
    def empty_channel_to_none(cls, value):
        if value is None or value == "" or value == "None":
            return None
        return value


settings = Settings()

BOT_IDS = ("bot1", "bot2")
BOT_LABELS = {
    "bot1": "Bot 1",
    "bot2": "Bot 2",
}
