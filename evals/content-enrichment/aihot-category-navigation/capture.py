"""Bounded public category-feed capture for future benchmark expansion."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS
from airadar.eval.aihot_dataset import HttpxTransport
from evals._shared import assets


def capture(output: Path, pages: int) -> list[dict]:
    if not 1 <= pages <= 50:
        raise ValueError("pages must be 1..50 per category")
    output.mkdir(parents=True, exist_ok=False)

    def one(category):
        client = HttpxTransport(timeout_seconds=20, user_agent="AI-Radar-Eval/1.0")
        cursor, seen, count = None, set(), 0
        try:
            for page in range(1, pages + 1):
                params = {"mode": "all", "category": category}
                if cursor:
                    params.update(cursorAt=str(cursor["at"]), cursorId=cursor["id"])
                url = "https://aihot.news/api/public/feed?" + urlencode(params)
                prefix = output / f"{category}-p{page}"
                assets.write_json(prefix.with_suffix('.request.json'), {"url": url, "started_at": assets.utc_now()})
                response = client.get(url, params=None, headers={})
                body = prefix.with_suffix('.body')
                with body.open('xb') as stream:
                    stream.write(response.body)
                assets.write_json(prefix.with_suffix('.response.json'), {
                    "url": url, "status": response.status, "finished_at": assets.utc_now(),
                    "sha256": assets.file_digest(body)})
                if response.status != 200:
                    raise ValueError(f"public category capture failed: {category}, HTTP {response.status}")
                payload = assets.read_json(body)
                ids = {r['id'] for r in payload['items']}
                count += len(ids - seen)
                if payload['hasNext'] and (not ids - seen or not payload['nextCursor']):
                    raise ValueError("category pagination did not advance")
                seen.update(ids)
                if not payload['hasNext']:
                    return {"category": category, "pages": page, "items": count, "end_reached": True}
                cursor = payload['nextCursor']
            return {"category": category, "pages": pages, "items": count, "end_reached": False}
        finally:
            client.close()

    # Six independent feeds; each feed's cursor is sequential, no shared mutation.
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, PRIMARY_CATEGORY_SLUGS.values()))
    assets.write_json(output / 'capture-summary.json', results)
    return results


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description="只读抓取六类公开feed；保存实际请求/原件，不更新生产或旧题库。")
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pages', type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(capture(args.output, args.pages), ensure_ascii=False, indent=2))
