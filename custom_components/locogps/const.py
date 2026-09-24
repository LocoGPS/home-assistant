"""Constants for the LocoGPS integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "locogps"

DEFAULT_HOST = "live.locogps.de"
CONFIGURATION_URL = "https://live.locogps.de"

CONF_SSL = "ssl"

# A token is valid for one year and renewed once less than a month is left,
# so a running installation never runs out of access. If the server refuses to
# extend it, the user is asked to log in again a week before it expires.
TOKEN_LIFETIME = timedelta(days=365)
TOKEN_RENEW_BEFORE = timedelta(days=30)
TOKEN_REAUTH_BEFORE = timedelta(days=7)

# The live socket delivers every change as it happens. The periodic refresh
# only picks up new devices and geofences and anything the socket missed.
UPDATE_INTERVAL = timedelta(minutes=5)

SOCKET_RECONNECT_MIN = 5
SOCKET_RECONNECT_MAX = 300

# homeStatus values an LV2 reports while it is connected to one of its saved
# WLAN networks and saves power. Same mapping as the LocoGPS app.
WIFI_ZONE_SLOTS = {
    1: "slot1",
    2: "slot2",
    3: "slot3",
    49: "slot1",
    50: "slot2",
    51: "slot3",
}
