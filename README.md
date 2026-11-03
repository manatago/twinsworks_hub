# twinsworks_hub

manatago と新谷の共同事業のための共有基盤リポジトリ。
共有タスク・共有文書・身元情報などの「共有情報」の定義と実装をここに置く。

## 基本方針

- 定義と実装はこのリポジトリ（Git）が正本。動く状態（タスクの進捗、取り込み済みデータなど）は DB が持つ。
- 共有 DB に入れるのは共有情報のみ。個人データは個人側（ProjectTitan）に置き、ここには入れない。
- 個人タスクが共有タスクになったら「昇格」させる（正本を共有側へ移し、個人側にはリンクだけ残す）。

## 構成

- Postgres（状態管理）
- サービス層（権限チェックはここで強制する）
- MCP サーバー（エージェント向けの主窓口。公開 REST API は作らない）
- Web ダッシュボード（人間向け。会議で進捗を見る共用管理画面）

配置先は manatago の VPS。公開はせず、Tailscale のテールネット内だけで使う
（Tailscale Serve で `*.ts.net` の HTTPS 名で出す）。

## 動かし方

```bash
cp .env.example .env   # 値を設定する
docker compose up -d --build
bash scripts/apply_migrations.sh
```

ヘルスチェック: http://localhost:8000/health

## ドキュメント

- [アーキテクチャ](docs/architecture.md)
- [ガバナンス（役割・権限・データの境界）](docs/governance.md)
- [ブランチ運用ルール](docs/branch-rules.md)
- [デプロイ手順](docs/deploy.md)
- [DB マイグレーション規約](db/README.md)
