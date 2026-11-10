#!/usr/bin/env python3
"""エージェント用 API キーの発行（Admin の手作業用）。

生キーを生成し、DB へ入れるための INSERT 文（ハッシュのみ）を標準出力に出す。
生キーはこの出力に一度だけ現れる。DB に生キーは保存しない。

使い方:
    python3 scripts/create_agent_key.py --name muse \
        --scopes tasks:read,tasks:write,identities:read,identities:write,documents:read,ingestion:write

出力された SQL を、サーバー上で psql から流す:
    docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "<SQL>"
"""

import argparse
import hashlib
import secrets

ALL_SCOPES = (
    "tasks:read",
    "tasks:write",
    "identities:read",
    "identities:write",
    "documents:read",
    "documents:write",
    "ingestion:write",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="エージェント名（例: muse）")
    parser.add_argument(
        "--scopes", required=True,
        help="カンマ区切りのスコープ（許可一覧: " + ", ".join(ALL_SCOPES) + "）",
    )
    args = parser.parse_args()

    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    unknown = [s for s in scopes if s not in ALL_SCOPES]
    if unknown:
        parser.error(f"unknown scopes: {unknown}")

    raw_key = "twk_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    scopes_sql = "ARRAY[" + ",".join(f"'{s}'" for s in scopes) + "]"

    print(f"# agent: {args.name}")
    print(f"# scopes: {', '.join(scopes)}")
    print(f"# raw key (shown once, hand it to the agent): {raw_key}")
    print(
        "INSERT INTO agent_api_keys (name, key_hash, scopes) VALUES "
        f"('{args.name}', '{key_hash}', {scopes_sql});"
    )


if __name__ == "__main__":
    main()
