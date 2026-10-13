"""Fail closed on invalid deployment configuration without exposing input values."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from simulator.agentic.config import AgenticConfig
from simulator.realtime.azure import AzureConfig

class AppConfig(BaseModel):
    environment: Literal['local','demo','staging','azure'] = 'local'
    event_source: Literal['local','eventhub'] = 'local'
    agent: AgenticConfig = Field(default_factory=lambda:AgenticConfig(mode='mock'))
    azure: AzureConfig | None = Field(default=None,exclude=True,repr=False)
    frontend_origins: list[str] = ['http://127.0.0.1:3000','http://localhost:3000']
    demo_controls: bool = True
    autostart: bool = False
    foundry_hero_only: bool = False
    log_level: Literal['DEBUG','INFO','WARNING','ERROR'] = 'INFO'
    queue_size: int = Field(default=64,ge=1,le=10000)
    max_subscribers: int = Field(default=100,ge=1,le=1000)
    stream_seconds: int = Field(default=180,ge=15,le=210)
    diagnostics: bool = True
    release: str = '0.1.0-phase7'

    @model_validator(mode='after')
    def valid(self):
        for origin in self.frontend_origins:
            url=urlsplit(origin)
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
                raise ValueError('APP_FRONTEND_ORIGINS requires bare HTTP(S) origins')
            if self.environment in ('staging','azure') and (url.scheme!='https' or url.hostname in ('localhost','127.0.0.1')):
                raise ValueError('Cloud origins must use HTTPS and cannot use developer localhost')
        if self.event_source=='eventhub' and self.azure is None:raise ValueError('Event Hubs configuration required')
        if self.environment in ('staging','azure') and self.demo_controls:
            raise ValueError('Public cloud replay controls must be disabled')
        if self.foundry_hero_only and (self.environment not in ('local','demo') or self.event_source!='local' or self.agent.mode!='foundry'):
            raise ValueError('Hero demo requires local replay and Foundry in local/demo environment')
        return self

    @classmethod
    def from_env(cls,env=None):
        env=os.environ if env is None else env
        environment=env.get('APP_ENV','local')
        def boolean(key,default):
            value=env.get(key,str(default)).lower()
            if value not in ('true','false','1','0'):raise ValueError(key)
            return value in ('true','1')
        try:
            agent=AgenticConfig.from_env(env)
            azure=None
            source=env.get('EVENT_SOURCE','local')
            if source=='eventhub':
                azure=AzureConfig(namespace=env.get('AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE') or None,
                    eventhub_name=env.get('AZURE_EVENT_HUB_NAME') or None,
                    connection_string=env.get('AZURE_EVENT_HUB_CONNECTION_STRING') or None,
                    consumer_group=env.get('AZURE_EVENT_HUB_CONSUMER_GROUP','$Default'),
                    queue_size=env.get('REALTIME_QUEUE_SIZE','64'),max_attempts=env.get('REALTIME_MAX_ATTEMPTS','3'),
                    backoff_seconds=env.get('REALTIME_BACKOFF_SECONDS','.5'),receive_timeout_seconds=env.get('REALTIME_RECEIVE_TIMEOUT_SECONDS','60'),
                    publish_timeout_seconds=env.get('REALTIME_PUBLISH_TIMEOUT_SECONDS','30'),
                    batch_size=env.get('REALTIME_BATCH_SIZE','32'),flush_seconds=env.get('REALTIME_FLUSH_SECONDS','.25'))
            return cls(environment=environment,event_source=source,agent=agent,azure=azure,
                frontend_origins=env.get('APP_FRONTEND_ORIGINS','http://127.0.0.1:3000,http://localhost:3000').split(','),
                demo_controls=boolean('APP_DEMO_CONTROLS',environment in ('local','demo')),
                autostart=boolean('APP_AUTOSTART',source=='eventhub'),
                foundry_hero_only=boolean('APP_FOUNDRY_HERO_ONLY',False),
                diagnostics=boolean('APP_DIAGNOSTICS',environment in ('local','demo')),
                log_level=env.get('LOG_LEVEL','INFO'),queue_size=env.get('REALTIME_QUEUE_SIZE','64'),
                max_subscribers=env.get('APP_MAX_SSE_SUBSCRIBERS','100'),stream_seconds=env.get('APP_SSE_SECONDS','180'))
        except Exception:
            # Pydantic exceptions contain input values: never propagate them at startup.
            raise ValueError('Invalid application configuration. Check APP_ENV, APP_FRONTEND_ORIGINS, APP_DEMO_CONTROLS, EVENT_SOURCE, AGENTIC_MODE and required cloud settings; values withheld.') from None
