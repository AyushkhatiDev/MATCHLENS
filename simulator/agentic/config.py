"""Environment configuration; never load or serialize credential material."""
import os
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, model_validator

class AgenticConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mode: Literal['mock', 'foundry'] = 'mock'
    project_endpoint: str | None = Field(default=None, repr=False, exclude=True)
    model: str | None = Field(default=None, repr=False, exclude=True)
    agent_id: str | None = Field(default=None, repr=False, exclude=True)
    azure_client_id: str | None = Field(default=None, repr=False, exclude=True)
    azure_tenant_id: str | None = Field(default=None, repr=False, exclude=True)
    azure_subscription_id: str | None = Field(default=None, repr=False, exclude=True)
    timeout_seconds: float = Field(default=60, gt=0, le=300)

    @model_validator(mode='after')
    def foundry_configuration(self):
        if self.mode == 'foundry':
            if not self.project_endpoint or not self.model:
                raise ValueError('Foundry mode requires FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL.')
            url = urlparse(self.project_endpoint)
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('FOUNDRY_PROJECT_ENDPOINT must be an HTTPS endpoint without embedded credentials.')
            if self.agent_id:
                raise ValueError('FOUNDRY_AGENT_ID is for service-managed agents; this runtime uses five code-defined agents. Unset it.')
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> 'AgenticConfig':
        source = os.environ if env is None else env
        return cls(mode=source.get('AGENTIC_MODE', 'mock'),
            project_endpoint=source.get('FOUNDRY_PROJECT_ENDPOINT') or source.get('FOUNDY_PROJECT_ENDPOINT'),
            model=source.get('FOUNDRY_MODEL'), agent_id=source.get('FOUNDRY_AGENT_ID'),
            azure_client_id=source.get('AZURE_CLIENT_ID'), azure_tenant_id=source.get('AZURE_TENANT_ID'),
            azure_subscription_id=source.get('AZURE_SUBSCRIPTION_ID'),
            timeout_seconds=source.get('AGENTIC_TIMEOUT_SECONDS', '60'))
