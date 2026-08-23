"""SQLAlchemy repository for app_settings — a generic encrypted key-value store for app-wide
secrets that don't fit the per-project cloud_connections shape (e.g. the Claude Code headless
OAuth token entered on the Settings page)."""

from __future__ import annotations

from app.infrastructure.db.orm import AppSettingRow


class SqlAlchemyAppSettingsRepository:
    def __init__(self, session) -> None:
        self._s = session

    async def get(self, key: str) -> str | None:
        row = await self._s.get(AppSettingRow, key)
        return row.encrypted_value if row else None

    async def set(self, key: str, encrypted_value: str) -> None:
        row = await self._s.get(AppSettingRow, key)
        if row is None:
            row = AppSettingRow(key=key, encrypted_value=encrypted_value)
            self._s.add(row)
        else:
            row.encrypted_value = encrypted_value
        await self._s.flush()
