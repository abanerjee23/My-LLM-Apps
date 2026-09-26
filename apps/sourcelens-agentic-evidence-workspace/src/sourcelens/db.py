from __future__ import annotations

import atexit

from sqlalchemy import Engine, create_engine

from .config import Settings


class DatabaseNotConfigured(RuntimeError):
    pass


def create_database_engine(settings: Settings) -> Engine:
    """Cloud SQL through the Cloud SQL Python Connector, or an explicit URL.

    Cloud Run connects over the private network (SOURCELENS_DB_IP_TYPE=private). A laptop
    connects through the instance's locked public address; the connector authenticates with
    Google credentials, so no network is allow-listed.
    """
    if settings.sourcelens_database_url:
        return create_engine(settings.sourcelens_database_url, pool_pre_ping=True)
    if not settings.sourcelens_db_password:
        raise DatabaseNotConfigured(
            "Set SOURCELENS_DB_PASSWORD (Secret Manager: sourcelens-db-password) "
            "or SOURCELENS_DATABASE_URL"
        )
    from google.cloud.sql.connector import Connector, IPTypes

    ip_type = IPTypes.PRIVATE if settings.sourcelens_db_ip_type == "private" else IPTypes.PUBLIC
    connector = Connector(ip_type=ip_type, refresh_strategy="lazy")
    atexit.register(connector.close)

    def connect():
        return connector.connect(
            settings.sourcelens_cloud_sql_instance,
            "pg8000",
            user=settings.sourcelens_db_user,
            password=settings.sourcelens_db_password,
            db=settings.sourcelens_db_name,
            timeout=30,
        )

    return create_engine(
        "postgresql+pg8000://",
        creator=connect,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_recycle=1800,
    )
