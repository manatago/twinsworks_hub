-- 初期スキーマ（詳細な列定義は運用しながら詰める）
-- 決まっていること:
--   * 身元情報は、仕事で使うものをすべて列挙する（個人のアドレスも含む）
--   * 共有文書は当面、無期限で保持する（retention は持たない）
--   * 個人タスクの共有化は「昇格」: 正本をここへ移し、個人側にはリンクだけ残す

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS identities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind        TEXT NOT NULL,  -- person / agent / service
    name        TEXT NOT NULL,
    email       TEXT,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title             TEXT NOT NULL,
    description       TEXT,
    status            TEXT NOT NULL DEFAULT 'open',  -- open / in_progress / done / archived
    owner_identity_id UUID REFERENCES identities(id),
    due_at            TIMESTAMPTZ,
    source            TEXT,  -- 由来（会議、メール、Slack など）
    promoted_from     TEXT,  -- 昇格元（個人側の参照情報）
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    kind        TEXT,  -- 議事録 / 仕様書 / その他
    locator     TEXT,  -- URL または配置場所
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
