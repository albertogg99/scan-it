from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_sqlite_upgrade_and_downgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}")
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    assert "runs" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert {"mcp_servers", "conversations", "messages", "tool_calls", "attachments", "runs", "run_events"}.issubset(inspect(engine).get_table_names())
    assert "active_run_id" in {column["name"] for column in inspect(engine).get_columns("conversations")}
    engine.dispose()
    command.downgrade(config, "base")