# --------------------------------------------------------------------------
# Copyright Commvault Systems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# --------------------------------------------------------------------------

"""
Configuration module for Commvault MCP Server.

This module handles all configuration-related functionality including
environment variable management and server settings.
"""

import sys
from dataclasses import dataclass
from typing import List, Optional

from src.logger import logger
from src.utils import get_env_var


@dataclass
class ServerConfig:
    transport_mode: str
    host: Optional[str] = None
    port: Optional[int] = None
    path: Optional[str] = None
    use_oauth: bool = False
    oauth_authorization_endpoint: Optional[str] = None
    oauth_token_endpoint: Optional[str] = None
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None
    oauth_jwks_uri: Optional[str] = None
    oauth_required_scopes: Optional[List[str]] = None
    oauth_base_url: Optional[str] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.transport_mode not in ["stdio", "streamable-http", "sse"]:
            raise ValueError(f"Invalid transport mode: {self.transport_mode}")
        
        if self.transport_mode != "stdio":
            if not all([self.host, self.port, self.path]):
                raise ValueError("Host, port, and path are required for non-stdio transport modes")

        if self.use_oauth:
            if not all([self.oauth_authorization_endpoint, self.oauth_token_endpoint, self.oauth_client_id, self.oauth_client_secret, self.oauth_jwks_uri]):
                raise ValueError("All OAuth-related fields must be set for custom OAuth provider")


class ConfigManager:
    @staticmethod
    def load_config() -> ServerConfig:
        """
        Load server configuration from environment variables.
        """
        try:
            transport_mode = get_env_var("MCP_TRANSPORT_MODE")
            
            # Only load network config for non-stdio modes
            if transport_mode != "stdio":
                host = get_env_var("MCP_HOST")
                port = int(get_env_var("MCP_PORT"))
                path = get_env_var("MCP_PATH")
                use_oauth = get_env_var("USE_OAUTH", "false").lower() == "true"
                if use_oauth:
                    config = ServerConfig(
                        transport_mode=transport_mode,
                        host=host,
                        port=port,
                        path=path,
                        use_oauth=True,
                        oauth_authorization_endpoint=get_env_var("OAUTH_AUTHORIZATION_ENDPOINT"),
                        oauth_token_endpoint=get_env_var("OAUTH_TOKEN_ENDPOINT"),
                        oauth_client_id=get_env_var("OAUTH_CLIENT_ID"),
                        oauth_client_secret=get_env_var("OAUTH_CLIENT_SECRET"),
                        oauth_jwks_uri=get_env_var("OAUTH_JWKS_URI"),
                        oauth_required_scopes=get_env_var("OAUTH_REQUIRED_SCOPES").split(","),
                        oauth_base_url=get_env_var("OAUTH_BASE_URL")
                    )
                else:
                    config = ServerConfig(
                        transport_mode=transport_mode,
                        host=host,
                        port=port,
                        path=path
                    )
            else:
                config = ServerConfig(transport_mode=transport_mode)

            logger.info(f"Configuration loaded successfully: transport={transport_mode}")
            return config
            
        except (ValueError, TypeError) as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error loading configuration: {e}")
            sys.exit(1)


# Server constants
SERVER_NAME = "Commvault MCP Server"
SERVER_INSTRUCTIONS = """You can use this server to interact with Commvault Product.

## AWS Onboarding and Protection — Output Style Rules

These rules apply to ALL AWS-related flows (onboarding, protection groups, backup):

- **Guided product experience.** Treat every AWS flow as a step-by-step wizard.
  Never dump tool output or API responses directly into user messages.
- **One decision at a time.** Ask a single question or present a single confirmation
  per turn. Do not bundle multiple choices.
- **Hide internal details.** Retain IDs, ARNs, JSON payloads, and raw API fields
  internally for tool calls. Only surface them if two items share the same display
  name, or if the user explicitly asks for technical details.
- **Product-language milestones.** Refer to stages by name: Connect AWS,
  Validate Access, Discover Accounts, Create Connection, Choose Workloads,
  Choose Plan, Create Protection Group, Start Backup.
- **No filler.** Never start a message with "Perfect!", "Excellent!", "Great!", "Sure!",
  "Got it!", or similar acknowledgments. Never end with "I hope this helps!" or similar.
- **No pre-action narration.** Do not write sentences like "Let me…", "Now let me…",
  "I'll begin by…", "Now I'll…" before invoking a tool. Either invoke the tool silently
  or print one short product-language status line.
- **Silent idempotency.** When a tool reports `alreadyDeployed: true`, `inProgress: true`,
  or "already exists", do not narrate it as an observation. Continue to the next step;
  only mention it if it changes the next ask.
- **Silent retries.** Never tell the user about transient/network failures or that you are
  retrying. The user only sees the final outcome.
- **One status format.** Use exactly `**{Stage} — {State}**` on its own line, optionally
  followed by one short sentence. Stages: `Connect AWS`, `Validate Access`,
  `Discover Accounts`, `Create Connection`, `Choose Workloads`, `Choose Plan`,
  `Create Protection Group`, `Start Backup`. States: `Working…`, `Done`, `Skipped`,
  `Needs attention`. Never use the older `Done:` / `Next:` prefix style.
- **Plain-language errors.** On failure, describe what the user should check in AWS
  Console or Commvault. Avoid stack traces and raw error strings.
- **Human-readable RPO.** Convert rpoInMinutes to a readable label when presenting
  plans: 0 → No scheduled RPO, <60 → Every N minutes, <1440 → Every Xh Ym,
  1440 → Daily, 10080 → Weekly, 43800 → Monthly, ≥525600 → Yearly.

For the full step-by-step AWS onboarding workflow, call
`get_aws_onboarding_instructions` once at the start of any AWS onboarding session.
"""
