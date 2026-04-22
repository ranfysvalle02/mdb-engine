"""Built-in admin plane modules."""

from .audit import AuditAdminModule
from .health import HealthAdminModule
from .reconciler import ReconcilerAdminModule
from .secrets import SecretsAdminModule
from .trash import TrashAdminModule

__all__ = [
    "AuditAdminModule",
    "HealthAdminModule",
    "ReconcilerAdminModule",
    "SecretsAdminModule",
    "TrashAdminModule",
]
