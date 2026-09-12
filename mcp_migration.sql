CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    server_url TEXT DEFAULT '',
    connector_id TEXT DEFAULT '',
    server_label TEXT DEFAULT '',
    server_description TEXT DEFAULT '',
    authorization TEXT DEFAULT '',
    headers TEXT DEFAULT '',
    allowed_tools TEXT DEFAULT '',
    allowed_tools_read_only INTEGER DEFAULT 0,
    require_approval TEXT DEFAULT 'always',
    require_approval_tools TEXT DEFAULT '',
    require_approval_read_only INTEGER DEFAULT 0,
    require_approval_never_tools TEXT DEFAULT '',
    require_approval_never_read_only INTEGER DEFAULT 0,
    defer_loading INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
