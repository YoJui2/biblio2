from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_session_factory(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_dependency(factory: sessionmaker[Session]):
    def get_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    return get_session
