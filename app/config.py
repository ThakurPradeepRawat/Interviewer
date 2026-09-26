from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    database_url: str = "postgresql+asyncpg://interview:interview@localhost:5432/interview_assistant"

    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 3600

    max_questions_per_interview: int = 6
    code_exec_timeout_seconds: int = 5


settings = Settings()
