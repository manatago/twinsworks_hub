# DB マイグレーション規約

- スキーマ変更は `db/migrations/` に番号付き SQL ファイルを追加して行う（例: `0002_add_task_priority.sql`）。
- 適用は `bash scripts/apply_migrations.sh` で行う。ファイル名の昇順に、未適用のものだけが流れる。
- 適用済みのマイグレーションは編集しない。直したいときは新しい番号で追加する。
- 実データは Git に入れない。DB の中身はデプロイ先の Docker ボリュームが持ち、バックアップは `scripts/backup_db.sh` で取る。
