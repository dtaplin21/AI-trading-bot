"""SQL migration helpers."""

from data.storage.migrate import split_sql, _is_timescale_statement


def test_split_sql_ignores_comments_and_splits_on_semicolon():
    script = """
    -- comment
    CREATE TABLE IF NOT EXISTS foo (id INT);
    CREATE TABLE IF NOT EXISTS bar (id INT);
    """
    parts = split_sql(script)
    assert len(parts) == 2
    assert "foo" in parts[0]
    assert "bar" in parts[1]


def test_is_timescale_statement():
    assert _is_timescale_statement("SELECT create_hypertable('t', 'time');")
    assert not _is_timescale_statement("CREATE TABLE foo (id INT);")


def test_split_sql_keeps_do_blocks_intact():
    script = """
    CREATE TABLE foo (id INT);
    DO $$
    BEGIN
        PERFORM 1;
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'x';
    END $$;
    """
    parts = split_sql(script)
    assert len(parts) == 2
    assert parts[0].startswith("CREATE TABLE")
    assert "DO $$" in parts[1] and "END $$;" in parts[1]
