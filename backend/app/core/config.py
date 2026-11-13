from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "NWO NOC"
    app_env: str = "production"
    api_docs_enabled: bool = False
    offline_mode: bool = False
    synthetic_monitoring_enabled: bool = False
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

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.synthetic_monitoring_enabled and (not self.offline_mode or self.app_env != "staging"):
            raise ValueError("Synthetic monitoring requires offline staging")
        if self.offline_mode:
            if self.uisp_base_url or self.uisp_token or self.rf_collection_enabled or self.rf_history_pruning_enabled:
                raise ValueError("OFFLINE_MODE requires empty UISP settings and RF collection/pruning disabled")
        if self.app_env.lower() == "production":
            if not self.uisp_verify_tls:
                raise ValueError("Production requires UISP TLS certificate verification")
            if self.api_docs_enabled:
                raise ValueError("Production API documentation must remain disabled")
            if not self.secret_key.strip() or self.secret_key.startswith("replace-") or len(self.secret_key) < 32:
                raise ValueError("Production SECRET_KEY must be at least 32 characters and not a placeholder")
            if not self.admin_password.strip() or self.admin_password in {"ChangeThisPassword123!", "replace-admin-password"}:
                raise ValueError("Production ADMIN_PASSWORD must not use the example placeholder")
            if "*" in self.cors_origin_list:
                raise ValueError("Production CORS origins must not contain a wildcard")
        return self

    @property
    def cors_origin_list(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

settings = Settings()
