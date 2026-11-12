"""ワーカーランナー。取得ワーカーを一定間隔で回し続ける常駐ループ。

間隔は環境変数 WORKER_INTERVAL_SEC（既定 3600 秒 = 1時間）。
1回の実行が失敗してもループは止めない（失敗内容は標準出力に残る）。
"""

import os
import time
from datetime import datetime, timezone

from . import feed_fetch


def main() -> int:
    interval = int(os.environ.get("WORKER_INTERVAL_SEC", "3600"))
    print(
        f"worker runner started (interval={interval}s)", flush=True
    )
    while True:
        started = datetime.now(timezone.utc)
        print(f"[{started.isoformat()}] feed_fetch run", flush=True)
        try:
            rc = feed_fetch.main([])
        except Exception as exc:  # ループ自体は生かし続ける
            rc = -1
            print(f"feed_fetch crashed: {type(exc).__name__}: {exc}", flush=True)
        print(f"feed_fetch finished rc={rc}; sleeping {interval}s", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
