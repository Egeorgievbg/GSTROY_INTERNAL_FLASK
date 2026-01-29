import os

from constants import *  # noqa: F403


def _collect_printer_auth_headers():
    preferences = []
    env_header = os.environ.get("GSTROY_AUTH_HEADER")
    if env_header:
        for segment in env_header.split(","):
            name = segment.strip()
            if name and name not in preferences:
                preferences.append(name)
    else:
        preferences.append("X-API-KEY")
    for fallback in ("X-API-KEY", "X-Printer-Key", "X-Printer-Access-Key"):
        if fallback not in preferences:
            preferences.append(fallback)
    return tuple(preferences)


# Backwards-compatible module name with a configurable timeout.
PRINTER_REQUEST_TIMEOUT = float(
    os.environ.get("PRINTER_REQUEST_TIMEOUT", os.environ.get("ERP_LABEL_SERVER_TIMEOUT", "6"))
)

PRINTER_AUTH_HEADERS = _collect_printer_auth_headers()
