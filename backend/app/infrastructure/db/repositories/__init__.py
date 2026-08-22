"""SQLAlchemy repository adapters, split by aggregate.

Re-exported so callers can `from app.infrastructure.db.repositories import SqlAlchemy...`.
"""

from app.infrastructure.db.repositories.app_settings import SqlAlchemyAppSettingsRepository
from app.infrastructure.db.repositories.cloud_connections import (
    SqlAlchemyCloudConnectionRepository,
    SqlAlchemyTrackedAlarmRepository,
)
from app.infrastructure.db.repositories.documents import (
    SqlAlchemyDocumentRepository,
    SqlAlchemyRetriever,
)
from app.infrastructure.db.repositories.incidents import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyIncidentRepository,
)
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork

__all__ = [
    "SqlAlchemyIncidentRepository",
    "SqlAlchemyAnalysisCacheRepository",
    "SqlAlchemyDocumentRepository",
    "SqlAlchemyRetriever",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyCloudConnectionRepository",
    "SqlAlchemyTrackedAlarmRepository",
    "SqlAlchemyAppSettingsRepository",
]
