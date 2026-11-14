# MCP ツール一覧（v1）

エージェント向けの主窓口は MCP。公開 REST API は作らない。
各ツールは API キーのスコープで制御し、権限チェックはサービス層（API 側）で強制する。

スコープ: `tasks:read` / `tasks:write` / `identities:read` / `identities:write` /
`documents:read` / `documents:write` / `ingestion:write`

| ツール | すること | 必要スコープ |
|---|---|---|
| tasks.list | 共有タスク一覧（status・project で絞り込み） | tasks:read |
| tasks.get | 1件＋紐付く documents | tasks:read |
| tasks.create | 共有タスク作成（project を指定） | tasks:write |
| tasks.update_status | open / in_progress / done / archived の遷移 | tasks:write |
| tasks.promote | 個人側の参照を受け取り、共有側に正本を作る（個人側にリンクだけ残す処理は呼び出し側で行う） | tasks:write |
| identities.list | 身元一覧（handles を含む） | identities:read |
| identities.resolve | handle（Slack ID・メール等）から identity を引く | identities:read |
| identities.create | 身元＋handles の登録 | identities:write |
| documents.list | 共有文書一覧（source・kind で絞り込み） | documents:read |
| documents.get | 1件のメタ情報＋locator＋本文（基盤内で生成した文書のみ） | documents:read |
| documents.ingest | 文書の登録（source＋external_id で重複排除。本文を渡すと DB に保存） | ingestion:write |
| ingestion.record_checkpoint | 定期取得の最終位置を記録 | ingestion:write |

## 文書の置き方

- 取り込む文書の原文は DB に入れない。原文ファイルは VPS 上に置き、DB の `documents.locator` がその場所を指す。
- `documents.ingest` の前に、呼び出し側（エージェント）が原文ファイルを VPS 上へ配置しておく。
- 基盤の工程が生成する文書（朝の速報など）は例外で、本文を `documents.ingest` の body に渡して DB に保存する。`documents.get` はその本文を返す。一覧（documents.list）には本文を含めない。

## v1 に入れないもの

- 文書の物理削除（共有文書は無期限保持・追記のみ。誤りは訂正文書で対応する）
- ダッシュボードからの書き込み
- 管理者キーの発行（Admin の手作業とする）
- 公開 REST API
