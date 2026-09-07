from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


HF_DATASET_REVISION = "b9d0e6c9c14337620e442c93f40f7906942fe893"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, choices=range(9), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)

    mappings = [
        row
        for row in read_jsonl(args.mapping)
        if int(row["shard_index"]) == args.shard_index
    ]
    connection = duckdb.connect()
    connection.execute("PRAGMA disable_progress_bar")
    connection.execute(
        "CREATE TEMP TABLE wanted_items(item_id VARCHAR PRIMARY KEY, product_id VARCHAR UNIQUE)"
    )
    connection.executemany(
        "INSERT INTO wanted_items VALUES (?, ?)",
        [(str(row["item_id"]), str(row["product_id"])) for row in mappings],
    )
    url = (
        "https://hf-mirror.com/datasets/Marqo/fashion200k/resolve/"
        f"{HF_DATASET_REVISION}/data/data-{args.shard_index:05d}-of-00009.parquet"
    )
    rows = connection.execute(
        f"""
        SELECT w.product_id, p.item_ID, p.text
        FROM read_parquet('{url}') AS p
        INNER JOIN wanted_items AS w ON p.item_ID = w.item_id
        ORDER BY w.product_id
        """
    ).fetchall()
    connection.close()
    if len(rows) != len(mappings):
        raise RuntimeError(
            f"shard {args.shard_index}: expected {len(mappings)} captions, got {len(rows)}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as target:
        for product_id, item_id, caption in rows:
            target.write(
                json.dumps(
                    {
                        "product_id": product_id,
                        "item_id": item_id,
                        "image_caption": caption,
                        "shard_index": args.shard_index,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    print(
        json.dumps(
            {"shard_index": args.shard_index, "captions": len(rows)},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
