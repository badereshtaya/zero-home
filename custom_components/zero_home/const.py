"""Constants for the Zero Home integration."""
from __future__ import annotations

DOMAIN = "zero_home"
NAME = "Zero Home"
AGENT_VERSION = "1.3.0"

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
# 1.3.0: the device token of the Zero account (never expires; ends only when the account signs this integration out)
CONF_DEVICE_TOKEN = "device_token"
CONF_DISPLAY_NAME = "display_name"
CONF_USER_ID = "user_id"
RELAY_KEYS = ("home_id", "agent_token", "relay_ws", "public_url", "subdomain", "zero_web_origin", "owner_name")
QR_PATH = "/api/zero_home/qr"
MANAGE_URL = "https://zero.eshtaya.ps/account.html#home"   # the account page (rebranded per deployment)
CONFIG_REFRESH_S = 60                 # ha_agent_config: the settings managed on the account page

# options
OPT_EXPOSE_MODE = "expose_mode"          # "all" | "select"
OPT_EXPOSE_AREAS = "expose_areas"
OPT_EXPOSE_DOMAINS = "expose_domains"
OPT_EXPOSE_LABELS = "expose_labels"
OPT_EXPOSE_ENTITIES = "expose_entities"
OPT_EXPOSE_UI = "expose_ui"              # the wizard's choice: all | rooms | types | custom
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
    # the web interface from outside is the person's own choice: off until they turn it on (2026-11-19)
    OPT_REMOTE_UI: False,
    OPT_SET_EXTERNAL_URL: False,
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
ISSUE_SIGNED_OUT = "signed_out"

TRUSTED_PROXY_YAML = """http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1"""

SIGNAL_STATUS = "zero_home_status_{}"
