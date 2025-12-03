from flask import Blueprint

academy_bp = Blueprint(
    "academy",
    __name__,
    url_prefix="/academy",
    template_folder="../../templates",
)

from . import routes  # noqa: F401
