"""フィード取得ワーカー（朝の速報の取得・登録レイヤー）。

watchlist.json の各フィードを取得し、記事を documents に登録する。
重複排除はサービス層 documents.ingest の (source, external_id) で行う。
取得位置は ingestion_checkpoints に記録する。

意味処理（要約・スコア・エンティティ抽出）と配信はこのワーカーの範囲外。
そちらは軽量 LLM API の契約が決まってから別ワーカーとして足す。

実行は基盤（VPS）側の合成エージェント "platform-worker" として、
ダッシュボードと同じ方式でサービス層を直接呼ぶ（スコープは ingestion:write のみ）。

使い方: python -m workers.feed_fetch [--watchlist PATH]
"""

import argparse
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import auth, services  # noqa: E402

USER_AGENT = "twinsworks-hub-worker/1.0 (+https://github.com/manatago/twinsworks_hub)"
FETCH_TIMEOUT_SEC = 30
MAX_AGE_DAYS = 14

WORKER_AGENT = {"name": "platform-worker", "scopes": ["ingestion:write"]}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el, names) -> str | None:
    for child in el:
        if _local(child.tag) in names:
            text = (child.text or "").strip()
            if text:
                return text
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 / RSS 1.0 (RDF) / Atom を共通の形に寄せる。新しい順は呼び出し側で担保しない。"""
    root = ET.fromstring(data)
    items: list[dict] = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind == "item":  # RSS 2.0 / 1.0
            title = _child_text(el, ("title",))
            link = _child_text(el, ("link",))
            guid = _child_text(el, ("guid", "id"))
            published = _parse_date(
                _child_text(el, ("pubDate", "date", "published", "updated"))
            )
        elif kind == "entry":  # Atom
            title = _child_text(el, ("title",))
            link = None
            for child in el:
                if _local(child.tag) == "link":
                    rel = child.get("rel")
                    if rel in (None, "alternate") and child.get("href"):
                        link = child.get("href")
                        break
            guid = _child_text(el, ("id",))
            published = _parse_date(_child_text(el, ("published", "updated")))
        else:
            continue
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "external_id": guid or link,
                "published": published,
            }
        )
    return items


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
        return resp.read()


def run_source(source: dict, now: datetime) -> dict:
    key = source["key"]
    raw = fetch(source["url"])
    items = parse_feed(raw)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    fresh = [
        it for it in items if it["published"] is None or it["published"] >= cutoff
    ]
    fresh.sort(key=lambda it: it["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    cap = int(source.get("max_items_per_run", 40))
    selected = fresh[:cap]
    created = 0
    for it in selected:
        result = services.documents_ingest(
            title=it["title"],
            locator=it["link"],
            kind="article",
            source=key,
            external_id=it["link"],
        )
        if result.get("created"):
            created += 1
    if selected:
        newest = selected[0]
        services.ingestion_record_checkpoint(
            key,
            last_external_id=newest["link"],
            last_seen_at=(newest["published"] or now).isoformat(),
        )
    return {
        "source": key,
        "fetched": len(items),
        "selected": len(selected),
        "created": created,
        "duplicates": len(selected) - created,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="フィード取得ワーカー")
    parser.add_argument(
        "--watchlist",
        default=os.environ.get(
            "WATCHLIST_PATH",
            os.path.join(os.path.dirname(__file__), "watchlist.json"),
        ),
    )
    args = parser.parse_args(argv)

    with open(args.watchlist, encoding="utf-8") as f:
        config = json.load(f)

    token = auth.set_current_agent(WORKER_AGENT)
    now = datetime.now(timezone.utc)
    failures = 0
    total_created = 0
    try:
        for source in config["sources"]:
            try:
                stats = run_source(source, now)
                total_created += stats["created"]
                print(
                    "{source}: fetched={fetched} selected={selected} "
                    "created={created} duplicates={duplicates}".format(**stats),
                    flush=True,
                )
            except Exception as exc:  # 1媒体の失敗で全体を止めない
                failures += 1
                print(
                    f"{source['key']}: ERROR {type(exc).__name__}: {exc}",
                    flush=True,
                )
    finally:
        auth.reset_current_agent(token)
    print(f"total created={total_created} failures={failures}", flush=True)
    return 1 if failures == len(config["sources"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
