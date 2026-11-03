-- 詳細設計の追加（2026-11-03 承認）
-- 決まっていること:
--   * 身元情報は「人」(identities) と「ID の列挙」(identity_handles) に分ける
--   * tasks は project で事業を分ける（複数の事業があるため）
--   * 文書の原文は DB に入れない。原文ファイルは VPS 上に置き、DB は locator で参照する

CREATE TABLE IF NOT EXISTS identity_handles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_id UUID NOT NULL REFERENCES identities(id),
    channel     TEXT NOT NULL,  -- slack / email / meet / zoom / github / other
    handle      TEXT NOT NULL,  -- Slack ID、メールアドレス、表示名など
    is_primary  BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (identity_id, channel, handle)
);

ALTER TABLE identities ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';  -- active / inactive

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'normal';  -- low / normal / high
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ;

DO $$ BEGIN
    ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
        CHECK (status IN ('open', 'in_progress', 'done', 'archived'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE tasks ADD CONSTRAINT tasks_priority_check
        CHECK (priority IN ('low', 'normal', 'high'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS task_documents (
    task_id     UUID NOT NULL REFERENCES tasks(id),
    document_id UUID NOT NULL REFERENCES documents(id),
    PRIMARY KEY (task_id, document_id)
);

-- documents.locator は 0001 で定義済み（原文の置き場所。原則として VPS 上のファイルパス）
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source TEXT;       -- slack / gmail / drive / meet / zoom / manual
ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_id TEXT;  -- 取り込み元システム側の ID（重複取り込み防止）
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS documents_source_external_id_key
    ON documents (source, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
    source           TEXT PRIMARY KEY,
    last_seen_at     TIMESTAMPTZ,
    last_external_id TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
