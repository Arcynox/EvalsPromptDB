APPLICATION_ID = 0x50524442
SCHEMA_VERSION = 3

EVALUATIONS_BY_RUN = "CREATE INDEX evaluations_by_run ON evaluations(name, run_id, version, created_at)"
PROMPT_KIND = "kind TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'chat'))"
MIGRATIONS = {
    1: ("ALTER TABLE evaluations ADD COLUMN run_id TEXT", EVALUATIONS_BY_RUN),
    2: (f"ALTER TABLE prompts ADD COLUMN {PROMPT_KIND}",),
}

SCHEMA = (
    """
    CREATE TABLE prompt_names (
        name TEXT PRIMARY KEY NOT NULL,
        last_version INTEGER NOT NULL DEFAULT 0
    )
    """,
    f"""
    CREATE TABLE prompts (
        name TEXT NOT NULL REFERENCES prompt_names(name),
        version INTEGER NOT NULL CHECK (version > 0),
        template TEXT NOT NULL,
        variables TEXT NOT NULL,
        model TEXT,
        temperature REAL,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL,
        {PROMPT_KIND},
        PRIMARY KEY (name, version)
    )
    """,
    """
    CREATE TABLE tags (
        name TEXT NOT NULL,
        version INTEGER NOT NULL,
        tag TEXT NOT NULL,
        PRIMARY KEY (name, version, tag),
        FOREIGN KEY (name, version) REFERENCES prompts(name, version) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX tags_by_label ON tags(tag, name, version)",
    """
    CREATE TABLE test_cases (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL REFERENCES prompt_names(name),
        input TEXT NOT NULL,
        expected TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (name, id)
    )
    """,
    """
    CREATE TABLE evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        version INTEGER NOT NULL,
        score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
        model TEXT NOT NULL,
        test_id INTEGER,
        output TEXT,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL,
        run_id TEXT,
        FOREIGN KEY (name, version) REFERENCES prompts(name, version) ON DELETE CASCADE,
        FOREIGN KEY (name, test_id) REFERENCES test_cases(name, id)
    )
    """,
    "CREATE INDEX evaluations_by_version ON evaluations(name, version, created_at)",
    EVALUATIONS_BY_RUN,
)
