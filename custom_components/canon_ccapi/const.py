DOMAIN = "canon_ccapi"

DEFAULT_PORT = 8080
DEFAULT_SAVE_PATH = "canon_photos"

CCAPI_BASE = "ccapi"
CCAPI_VER = "ver100"

CONF_HOST = "host"
CONF_PORT = "port"

# Coordinator data keys
KEY_CONNECTED = "connected"
KEY_CAPS = "capabilities"
KEY_BEST_VER = "best_ver"
KEY_BATTERY = "battery"
KEY_STORAGE = "storage"
KEY_TEMPERATURE = "temperature"
KEY_DEVICE_INFO = "device_info"
KEY_SETTINGS = "settings"
KEY_RECORDABLE = "recordable"
KEY_LENS = "lens"

# Config entry option: every shooting setting the user last chose in HA,
# name -> value. Shooting settings do not survive a camera power-cycle
# (identity settings do), so the chosen values are remembered and re-asserted
# on reconnect. Only values set through HA land here - the camera's own menu is
# never second-guessed.
OPT_STICKY_SETTINGS = "sticky_shooting_settings"

# Superseded by OPT_STICKY_SETTINGS, which held stillimagequality alone. Kept
# so an existing entry can be migrated; nothing writes it any more.
OPT_STICKY_QUALITY = "sticky_stillimagequality"
