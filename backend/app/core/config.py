from typing import Literal
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "NWO NOC"
    app_env: str = "production"
    api_docs_enabled: bool = False
    offline_mode: bool = False
    synthetic_monitoring_enabled: bool = False
    powercode_fixture_enabled: bool = False
    powercode_live_enabled: bool = False
    notifications_worker_enabled: bool = False
    notifications_auto_drafts_enabled: bool = False
    notifications_live_delivery_enabled: bool = False
    notifications_vault_key_file: str = Field(default='',repr=False,exclude=True)

    @model_validator(mode='after')
    def notification_isolation(self):
        if self.notifications_live_delivery_enabled:
            raise ValueError('External notification delivery is unavailable in NOC-96; explicit future provider/security approval required')
        if self.notifications_worker_enabled and (not self.offline_mode or self.app_env!='staging'):
            raise ValueError('Mock notification worker requires offline staging')
        return self


    @model_validator(mode="after")
    def powercode_isolation(self):
        if self.powercode_live_enabled:
            raise ValueError("Live Powercode requires separately validated API/schema mappings; unavailable in NOC-95")
        if self.powercode_fixture_enabled and (not self.offline_mode or self.app_env != "staging"):
            raise ValueError("Powercode fixtures require offline staging")
        return self

    secret_key: str = Field(repr=False,exclude=True)
    access_token_expire_minutes: int = Field(15,ge=1,le=30)
    database_url: str = Field(repr=False,exclude=True)
    redis_url: str = "redis://redis:6379/0"
    admin_username: str = "admin"
    admin_password: str = Field(repr=False,exclude=True)
    admin_email: str = "admin@nwohiobb.local"
    uisp_base_url: str = ""
    uisp_token: SecretStr = SecretStr("")
    uisp_token_file: str = Field(default="",repr=False,exclude=True)
    mikrotik_password_file: str = Field(default="",repr=False,exclude=True)
    uisp_enabled: bool = False
    uisp_allowed_origins: str = ""
    integrations_admin_approved: bool = False
    mikrotik_enabled: bool = False
    mikrotik_base_url: str = ""
    mikrotik_allowed_origins: str = ""
    mikrotik_username: str = ""
    mikrotik_password: SecretStr = SecretStr("")
    mikrotik_device_id: int | None = Field(default=None, ge=1)
    mikrotik_sync_interval_seconds: int = 300
    uisp_verify_tls: bool = True
    uisp_sync_interval_seconds: int = 300
    rf_scheduler_enabled: bool = False
    rf_scheduler_spool_dir: str = '/var/lib/nwo-noc/monitoring'
    rf_scheduler_manifest_file: str = Field(default='',repr=False,exclude=True)
    rf_scheduler_concurrency: int = Field(8,ge=1,le=16)
    rf_scheduler_batch_size: int = Field(32,ge=1,le=100)
    rf_reliability_policy: dict = Field(default_factory=dict)
    rf_collection_enabled: bool = False
    rf_snmp_enabled: bool = False
    rf_snmp_config_file: str = Field(default='', repr=False, exclude=True)
    rf_radio_thresholds: dict = Field(default_factory=dict)
    rf_microwave_enabled: bool = False
    rf_microwave_config_file: str = Field(default='',repr=False,exclude=True)
    rf_microwave_thresholds: dict = Field(default_factory=dict)
    rf_analytics_projection_enabled: bool = False
    rf_analytics_policy: dict = Field(default_factory=dict)
    rf_explanation_provider: Literal['disabled','local_ollama'] = 'disabled'
    rf_explanation_admin_approved: bool = False
    rf_explanation_model: str = Field(default='local-rf',pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}$')
    rf_troubleshooting_temperature_warning_c: float = Field(default=80,ge=40,le=150,allow_inf_nan=False)
    rf_analytics_retention_enabled: bool = False
    rf_analytics_retention_days: int = Field(90,ge=31,le=365)
    rf_analytics_retention_batch: int = Field(1000,ge=1,le=5000)
    rf_collection_min_interval_seconds: int = 300
    rf_collection_max_devices: int = 1000
    rf_history_pruning_enabled: bool = False
    rf_history_retention_days: int = 30
    rf_history_max_delete_per_run: int = 500
    cors_origins: str = "http://localhost:8090"
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_production_security(self):
        import os,stat
        from app.services.monitoring_policy import ReliabilityPolicy
        ReliabilityPolicy(**self.rf_reliability_policy)
        if self.rf_scheduler_enabled and not ((self.offline_mode and self.app_env=='staging' and self.synthetic_monitoring_enabled) or (not self.offline_mode and self.integrations_admin_approved)):
            raise ValueError('Monitoring scheduler requires offline synthetic staging or explicit online administrator approval')
        from app.services.radio_policy import RadioThresholds
        RadioThresholds(**self.rf_radio_thresholds)
        from app.services.microwave_policy import MicrowaveThresholds
        MicrowaveThresholds(**self.rf_microwave_thresholds)
        from app.services.rf_analytics_policy import AnalyticsPolicy
        AnalyticsPolicy(**self.rf_analytics_policy)
        if self.rf_explanation_provider!='disabled' and (self.offline_mode or not self.rf_explanation_admin_approved):
            raise ValueError('Local explanation requires explicit administrator approval and is forbidden in offline mode')
        if self.rf_microwave_enabled and (self.offline_mode or not self.integrations_admin_approved or not self.rf_microwave_config_file):
            raise ValueError('Microwave activation requires online administrator approval and private configuration')
        if self.rf_snmp_enabled and (self.offline_mode or not self.integrations_admin_approved or not self.rf_snmp_config_file):
            raise ValueError('SNMP RF activation requires online administrator approval and private configuration')
        for field in ("uisp_token","mikrotik_password"):
            path=getattr(self,field+"_file")
            if path:
                if getattr(self,field).get_secret_value():raise ValueError("Use exactly one credential source")
                try:
                    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                    with os.fdopen(fd) as handle:
                        info=os.fstat(handle.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size>8192:
                            raise ValueError("Secret files require mode 0400/0600 and bounded regular-file content")
                        value=handle.read(8193).strip()
                        if not value:raise ValueError("Empty secret file")
                        setattr(self,field,SecretStr(value))
                except OSError:raise ValueError("Secret file is unavailable or unsafe") from None
        if self.synthetic_monitoring_enabled and (not self.offline_mode or self.app_env != "staging"):
            raise ValueError("Synthetic monitoring requires offline staging")
        if self.offline_mode:
            if self.uisp_base_url or self.uisp_token.get_secret_value() or self.uisp_enabled or self.mikrotik_enabled or self.mikrotik_base_url or self.mikrotik_password.get_secret_value() or self.rf_collection_enabled or self.rf_history_pruning_enabled:
                raise ValueError("OFFLINE_MODE requires empty UISP settings and RF collection/pruning disabled")
        if self.uisp_enabled or self.mikrotik_enabled:
            from app.integrations.read_only import ReadPolicy, UISP_PATHS, MIKROTIK_PATHS
            if not self.integrations_admin_approved:
                raise ValueError("Integration activation requires explicit administrator approval")
            if self.uisp_enabled:
                if not self.uisp_verify_tls or not self.uisp_token.get_secret_value():
                    raise ValueError("UISP requires TLS verification and a read-only token")
                ReadPolicy(self.uisp_base_url,tuple(self.uisp_allowed_origins.split(',')),UISP_PATHS,True,True).validate()
            if self.mikrotik_enabled:
                if not self.mikrotik_username or not self.mikrotik_password.get_secret_value() or self.mikrotik_device_id is None:
                    raise ValueError("MikroTik requires read-only credentials and an explicit inventory device ID")
                ReadPolicy(self.mikrotik_base_url,tuple(self.mikrotik_allowed_origins.split(',')),MIKROTIK_PATHS,True,True).validate()
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
