from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "NWO NOC"
    app_env: str = "production"
    api_docs_enabled: bool = False
    secret_key: str
    access_token_expire_minutes: int = 720
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    admin_username: str = "admin"
    admin_password: str
    admin_email: str = "admin@nwohiobb.local"
    uisp_base_url: str = ""
    uisp_token: str = ""
    uisp_verify_tls: bool = True
    uisp_sync_interval_seconds: int = 300
    rf_collection_enabled: bool = False
    rf_collection_min_interval_seconds: int = 300
    rf_collection_max_devices: int = 1000
    rf_history_pruning_enabled: bool = False
    rf_history_retention_days: int = 30
    rf_history_max_delete_per_run: int = 500
    cors_origins: str = "http://localhost:8090"
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def cors_origin_list(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

settings = Settings()
