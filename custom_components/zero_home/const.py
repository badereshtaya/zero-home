"""Constants for the Zero Home integration."""
from __future__ import annotations

DOMAIN = "zero_home"
NAME = "Zero Home"
AGENT_VERSION = "1.0.0"

DEFAULT_ADMIN_API = "https://zeroadmin.eshtaya.ps/api.php"

# config entry data
CONF_CODE = "code"
CONF_ADMIN_API = "admin_api"
CONF_HOME_ID = "home_id"
CONF_AGENT_TOKEN = "agent_token"
CONF_RELAY_WS = "relay_ws"
CONF_PUBLIC_URL = "public_url"
CONF_SUBDOMAIN = "subdomain"
CONF_ZERO_WEB_ORIGIN = "zero_web_origin"
CONF_OWNER_NAME = "owner_name"

# options
OPT_EXPOSE_MODE = "expose_mode"          # "all" | "select"
OPT_EXPOSE_AREAS = "expose_areas"
OPT_EXPOSE_DOMAINS = "expose_domains"
OPT_EXPOSE_LABELS = "expose_labels"
OPT_EXPOSE_ENTITIES = "expose_entities"
OPT_REMOTE_UI = "remote_ui"
OPT_SET_EXTERNAL_URL = "set_external_url"
OPT_REPLACE_EXTERNAL_URL = "replace_external_url"

MODE_ALL = "all"
MODE_SELECT = "select"

DEFAULT_OPTIONS = {
    OPT_EXPOSE_MODE: MODE_ALL,
    OPT_EXPOSE_AREAS: [],
    OPT_EXPOSE_DOMAINS: [],
    OPT_EXPOSE_LABELS: [],
    OPT_EXPOSE_ENTITIES: [],
    OPT_REMOTE_UI: True,
    OPT_SET_EXTERNAL_URL: True,
    OPT_REPLACE_EXTERNAL_URL: False,
}

UI_CHECK_INTERVAL_S = 600            # §3.3: at start and every 10 min
RELAY_UNREACHABLE_AFTER_S = 1800     # repair issue after 30 min offline
EXPOSED_DEBOUNCE_S = 2.0

# repair issue ids
ISSUE_UI_NOT_READY = "ui_not_ready"
ISSUE_REVOKED = "link_revoked"
ISSUE_PLAN = "plan_ended"
ISSUE_UNREACHABLE = "relay_unreachable"
ISSUE_EXTERNAL_URL = "external_url_conflict"

TRUSTED_PROXY_YAML = """http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1"""

SIGNAL_STATUS = "zero_home_status_{}"
