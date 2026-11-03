# デプロイ手順

対象: manatago の VPS（本番）。開発時は手元の PC でも同じ手順で立てられる。

```bash
git clone https://github.com/manatago/twinsworks_hub.git
cd twinsworks_hub
cp .env.example .env   # パスワード等を設定する（.env は Git に入れない）
docker compose up -d --build
bash scripts/apply_migrations.sh
```

- イメージビルドはデプロイ先で行う（Mac の ARM と VPS の x86 でアーキテクチャが違うため）
- アプリはホストの `127.0.0.1:8000` で待ち受ける。テールネット内への公開は Tailscale Serve で行う
- Postgres のポートは公開しない

## バックアップ

```bash
bash scripts/backup_db.sh   # backups/ にダンプを出す（Git 管理外）
```

定期実行の方法（cron 等）と保管先は、運用開始時に決める。

## 更新

```bash
git pull
docker compose up -d --build
bash scripts/apply_migrations.sh
```
