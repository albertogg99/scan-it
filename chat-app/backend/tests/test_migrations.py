from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_sqlite_upgrade_and_downgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}")
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    assert "runs" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO mcp_servers (id, title, description, backend_url, auth_type, enabled, approval_policy, created_at, updated_at) VALUES ('existing-mcp', 'Existing', '', 'http://example-mcp:8000/mcp', 'none', 1, 'ask_destructive', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    command.upgrade(config, "head")
    assert {"mcp_servers", "conversations", "messages", "tool_calls", "attachments", "runs", "run_events"}.issubset(inspect(engine).get_table_names())
    assert "active_run_id" in {column["name"] for column in inspect(engine).get_columns("conversations")}
    assert {"icon_mime", "icon_data"}.issubset({column["name"] for column in inspect(engine).get_columns("mcp_servers")})
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM mcp_servers WHERE id = 'existing-mcp'")).scalar_one() == "Existing"
    engine.dispose()
    command.downgrade(config, "base")