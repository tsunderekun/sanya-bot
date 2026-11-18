"""Preview or apply curated kent notes to an existing SQLite database."""

import argparse
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from sanya.lore import INITIAL_KENTS_PROFILES

# Exact fingerprints of the previous built-in notes, never of user edits.
PREVIOUS = {
    1005211563: "c2ae2b1253c1c298764bfcdcbf9f01c4bc9bdedf3fd8dbe9593a2677df4ea2ae",
    288125599: "e96aed134fb2037694aaa1b8e5a2a0caa14eeec81baa5d922a3ce7f16b09b19a",
    233155465: "1891dd51177f34de9d20bd5b04034735595ee7acd54d71fb8ff84904f50d691d",
    302072457: "e707603a4b72a9404fa302ce3c5da635bce64dd87487667f216a405666f6e072",
    368426071: "9cb8d6bbc455134ec6b6fd820c93e376f9ca65cc014ad29a2b7dcf75e53b2023",
    207620287: None,
    542364289: "b5b01b574d5ee101b4ec178f8ae6fe02411d69da6daf777cdcbcfeb7ac559c2a",
    454516230: "ee5352993d1049d87da707411b29b358683ef51c48ac6cf6da9bce10aa4b4f86",
}
RECENT = {
    302072457: ("b08f281c8f18aebb2e9d57108c791b65918ee9637210ded19a5ce2b87bd94681",),
    233155465: (
        "912b0eea51ebd1efad959bc417bab5c25ae79dfb709d30b6451fcb387643064e",
        "07b6ca1d656de074e70cd8dba21499e7ead4844360760256b6aa3a4b254f45cc",
    ),
    207620287: ("d104b036eeb84f5cce7b4f562fec838595aef53f783bcb8e949ccec7408026be",),
    542364289: ("c30799ab7b47e4986bd98fffb5a6013fbd6492948ce1fbd307f2dcfbf0610cb4",),
    454516230: ("7f010200dc11aee5ffda62ffb19fde156d7fa885b7efec19eb29899b34048bbb",),
}
RETIRED = {
    970668834: "89dc1bb4ee1909806b46217db34dc1bec6f0132e1366bd0562b11c909d7790a1"
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def update(conn: sqlite3.Connection) -> dict[int, str]:
    """Run inside a transaction. Preserve manual rows, metadata and history."""
    changes = {}
    for uid, fingerprint in (PREVIOUS | RETIRED).items():
        fingerprints = {fingerprint, *RECENT.get(uid, ())}
        profile = INITIAL_KENTS_PROFILES.get(uid)
        note = profile["notes"] if profile else ""
        existing = conn.execute(
            "SELECT persona_notes FROM user_profiles WHERE user_id=?", (uid,)
        ).fetchone()
        rows = conn.execute(
            "SELECT id, content, added_by FROM kent_facts_entries WHERE user_id=? ORDER BY id",
            (uid,),
        ).fetchall()
        old = [row for row in rows if row[2] == 0 and digest(row[1]) in fingerprints]
        mirror = (existing[0] or "").strip() if existing else ""
        combined = "\n".join(
            content.strip() for _, content, _ in rows if content.strip()
        )
        if (
            mirror
            and mirror not in (combined, note)
            and digest(mirror) not in fingerprints
        ):
            changes[uid] = "skip: separate persona_notes requires manual review"
            continue
        if profile is None:
            conn.executemany(
                "DELETE FROM kent_facts_entries WHERE id=?", [(row[0],) for row in old]
            )
            action = (
                "remove retired built-in lore"
                if old or digest(mirror) in fingerprints
                else "unchanged"
            )
        elif not existing:
            conn.execute(
                "INSERT INTO user_profiles (user_id,user_name,first_name,persona_notes) VALUES (?,?,?,?)",
                (uid, profile["user_name"], profile["first_name"], ""),
            )
        if profile is None:
            pass
        elif old:
            conn.executemany(
                "UPDATE kent_facts_entries SET content=? WHERE id=?",
                [(note, row[0]) for row in old],
            )
            action = "replace built-in notes"
        elif any(content == note for _, content, _ in rows):
            action = "unchanged"
        else:
            conn.execute(
                "INSERT INTO kent_facts_entries (user_id,content,added_by) VALUES (?,?,0)",
                (uid, note),
            )
            action = "add curated note; keep unrecognized entries"
        contents = conn.execute(
            "SELECT content FROM kent_facts_entries WHERE user_id=? ORDER BY id", (uid,)
        )
        updated = "\n".join(
            content.strip() for (content,) in contents if content.strip()
        )
        if updated != mirror:
            conn.execute(
                "UPDATE user_profiles SET persona_notes=? WHERE user_id=?",
                (updated, uid),
            )
        changes[uid] = action
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="commit instead of rolling back"
    )
    parser.add_argument(
        "--backup", type=Path, help="new backup path, required with --apply"
    )
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    path = args.db.resolve(strict=True)
    uri = path.as_uri()
    if args.apply:
        backup = args.backup.resolve()
        backup.touch(exist_ok=False)
        with closing(sqlite3.connect(uri + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError(
                        "Backup validation failed; database was not updated"
                    )
    with closing(sqlite3.connect(uri + "?mode=rw", uri=True, timeout=10)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        changes = update(conn)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    for uid, action in changes.items():
        print(f"{uid}: {action}")
    print("Committed" if args.apply else "Preview only; changes rolled back")


if __name__ == "__main__":
    main()
