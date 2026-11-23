"""Advisory caller authority validation shared by application entry points."""
from collections.abc import Mapping


def require_auth0(authority):
    if type(authority) is not int or authority != 0:
        raise PermissionError("AUTH:0 requires integer zero")


def require_task(task):
    if not isinstance(task, Mapping):
        raise ValueError("task must be a JSON object")
    require_auth0(task.get("authority", 0))
