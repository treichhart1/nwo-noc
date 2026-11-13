from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings


class Base(DeclarativeBase):
    pass


# Bound reconnects and half-open sockets; a failed database must not hang the
# monitoring coordinator indefinitely. SQLite test engines keep their defaults.
connection_options = {}
if settings.database_url.startswith("postgresql"):
    connection_options = {
        "pool_timeout": 5,
        "connect_args": {
            "connect_timeout": 3,
            "keepalives_idle": 10,
            "keepalives_interval": 3,
            "keepalives_count": 3,
            "tcp_user_timeout": 10000,
        },
    }
engine = create_engine(settings.database_url, pool_pre_ping=True, **connection_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
