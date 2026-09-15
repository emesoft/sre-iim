"""SQLAlchemy repository adapters, split by aggregate.

Re-exported so callers can `from app.infrastructure.db.repositories import SqlAlchemy...`.
"""

from app.infrastructure.db.repositories.app_settings import SqlAlchemyAppSettingsRepository
from app.infrastructure.db.repositories.chat import SqlAlchemyChatRepository
from app.infrastructure.db.repositories.daily_reports import SqlAlchemyDailyReportRepository
from app.infrastructure.db.repositories.documents import (
    SqlAlchemyDocumentRepository,
    SqlAlchemyRetriever,
)
from app.infrastructure.db.repositories.integrations import (
    SqlAlchemyIntegrationRepository,
    SqlAlchemyTrackedAlarmRepository,
)
from app.infrastructure.db.repositories.incidents import (
    SqlAlchemyAnalysisCacheRepository,
    SqlAlchemyIncidentRepository,
)
from app.infrastructure.db.repositories.projects import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.db.repositories.users import SqlAlchemyUserRepository

__all__ = [
    "SqlAlchemyIncidentRepository",
    "SqlAlchemyAnalysisCacheRepository",
    "SqlAlchemyDocumentRepository",
    "SqlAlchemyRetriever",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyIntegrationRepository",
    "SqlAlchemyTrackedAlarmRepository",
    "SqlAlchemyAppSettingsRepository",
    "SqlAlchemyChatRepository",
    "SqlAlchemyProjectRepository",
    "SqlAlchemyUserRepository",
    "SqlAlchemyDailyReportRepository",
]
