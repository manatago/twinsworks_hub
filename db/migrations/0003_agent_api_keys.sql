-- エージェント用 API キー（2026-11-10）
-- 決まっていること:
--   * キーはエージェントごとに発行し、スコープで権限を絞る（取り消し可能）
--   * DB に保存するのはハッシュ（SHA-256）のみ。生キーは保存しない
--   * 発行は Admin の手作業（scripts/create_agent_key.py が発行用 SQL を出す）

CREATE TABLE IF NOT EXISTS agent_api_keys (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,  -- どのエージェントのキーか（例: muse）
    key_hash     TEXT NOT NULL UNIQUE,  -- 生キーの SHA-256（hex）
    scopes       TEXT[] NOT NULL,  -- tasks:read / tasks:write / identities:read / identities:write / documents:read / documents:write / ingestion:write
    revoked      BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);
