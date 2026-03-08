"""
One-off schema migrations for existing databases.
New deploys use create_all(); upgrades run these.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def run_migrations(engine: Engine) -> None:
    """Ensure users.last_seen_at exists (MySQL upgrade from pre-2.0)."""
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("users")}
    if "last_seen_at" in columns:
        return
    with engine.connect() as conn:
        dialect = engine.dialect.name
        if dialect == "mysql":
            conn.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME NULL"))
        elif dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME NULL"))
        conn.commit()
