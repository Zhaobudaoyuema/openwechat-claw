"""
One-off schema migrations for existing databases.
New deploys use create_all(); upgrades run these.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def run_migrations(engine: Engine) -> None:
    """Run lightweight schema upgrades for existing databases."""
    _ensure_users_last_seen_at(engine)
    _drop_registration_log_daily_unique(engine)
    _ensure_messages_attachment_columns(engine)
    _ensure_users_homepage(engine)


def _ensure_users_last_seen_at(engine: Engine) -> None:
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


def _drop_registration_log_daily_unique(engine: Engine) -> None:
    """Allow multiple registrations per IP/day by removing legacy unique constraint."""
    insp = inspect(engine)
    if "registration_logs" not in insp.get_table_names():
        return
    unique_names = {u.get("name") for u in insp.get_unique_constraints("registration_logs")}
    if "uq_reg_log_ip_date" not in unique_names:
        return

    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "mysql":
            conn.execute(text("ALTER TABLE registration_logs DROP INDEX uq_reg_log_ip_date"))
        elif dialect == "postgresql":
            conn.execute(text("ALTER TABLE registration_logs DROP CONSTRAINT uq_reg_log_ip_date"))
        elif dialect == "sqlite":
            conn.execute(text("DROP TABLE IF EXISTS registration_logs_new"))
            conn.execute(
                text(
                    """
                    CREATE TABLE registration_logs_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ip VARCHAR(45) NOT NULL,
                        registration_date DATE NOT NULL,
                        created_at DATETIME
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO registration_logs_new (id, ip, registration_date, created_at)
                    SELECT id, ip, registration_date, created_at FROM registration_logs
                    """
                )
            )
            conn.execute(text("DROP TABLE registration_logs"))
            conn.execute(text("ALTER TABLE registration_logs_new RENAME TO registration_logs"))
            conn.execute(text("CREATE INDEX ix_reg_log_ip_date ON registration_logs (ip, created_at)"))
        conn.commit()


def _ensure_messages_attachment_columns(engine: Engine) -> None:
    """Add attachment_path and attachment_filename to messages for file support."""
    insp = inspect(engine)
    if "messages" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("messages")}
    if "attachment_path" in columns and "attachment_filename" in columns:
        return
    with engine.connect() as conn:
        dialect = engine.dialect.name
        if "attachment_path" not in columns:
            if dialect == "mysql":
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_path VARCHAR(512) NULL"))
            elif dialect == "sqlite":
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_path VARCHAR(512)"))
            else:
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_path VARCHAR(512) NULL"))
        if "attachment_filename" not in columns:
            if dialect == "mysql":
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_filename VARCHAR(256) NULL"))
            elif dialect == "sqlite":
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_filename VARCHAR(256)"))
            else:
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_filename VARCHAR(256) NULL"))
        conn.commit()


def _ensure_users_homepage(engine: Engine) -> None:
    """Add homepage column to users for custom HTML pages."""
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("users")}
    if "homepage" in columns:
        return
    with engine.connect() as conn:
        dialect = engine.dialect.name
        if dialect == "mysql":
            conn.execute(text("ALTER TABLE users ADD COLUMN homepage LONGTEXT NULL"))
        elif dialect == "sqlite":
            conn.execute(text("ALTER TABLE users ADD COLUMN homepage TEXT"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN homepage TEXT NULL"))
        conn.commit()
