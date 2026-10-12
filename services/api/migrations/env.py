from alembic import context
from sqlalchemy import create_engine

from geopoc.config import get_settings
from geopoc.db.models import Base

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # PostGIS owns this table.
    return not (type_ == "table" and name == "spatial_ref_sys")


def run_migrations_online() -> None:
    # Tests pass an explicit URL through config.attributes; otherwise use application settings.
    url = context.config.attributes.get("url") or get_settings().database_url()
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
