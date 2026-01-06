import os

from constants import *  # noqa: F403

# Backwards-compatible module name with a configurable timeout.
PRINTER_REQUEST_TIMEOUT = float(
    os.environ.get("PRINTER_REQUEST_TIMEOUT", os.environ.get("ERP_LABEL_SERVER_TIMEOUT", "6"))
)
