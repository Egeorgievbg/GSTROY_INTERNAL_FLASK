import sqlite3
from pathlib import Path


DB_PATH = Path("erp_demo.db")


def normalize(name: str | None) -> str:
    return (name or "").strip().lower()


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} not found; run from repo root or adjust path.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, name FROM brands ORDER BY LOWER(name), id")
    rows = cursor.fetchall()

    keep_map: dict[str, int] = {}
    duplicates: list[tuple[int, int, str]] = []

    for row in rows:
        name = row["name"]
        key = normalize(name)
        if not key:
            continue
        if key not in keep_map:
            keep_map[key] = row["id"]
        else:
            duplicates.append((row["id"], keep_map[key], name or ""))

    if not duplicates:
        print("No duplicate brands detected.")
        return

    for duplicate_id, keep_id, name in duplicates:
        cursor.execute(
            "UPDATE products SET brand_id = ? WHERE brand_id = ?",
            (keep_id, duplicate_id),
        )
        cursor.execute("DELETE FROM brands WHERE id = ?", (duplicate_id,))
        print(
            f"Removed duplicate brand id={duplicate_id} ({name})"
            f" merged into id={keep_id}"
        )

    conn.commit()
    conn.close()
    print(f"Processed {len(duplicates)} duplicate brand(s).")


if __name__ == "__main__":
    main()
