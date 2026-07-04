"""Temporary verification script for blog consolidation."""
from kb.db import engine
from sqlalchemy import text

c = engine().connect()
print("=== Sources ===")
for r in c.execute(text("SELECT code, name, kind FROM source ORDER BY code")).fetchall():
    print(f"  {r[0]:20s} {r[1]:30s} kind={r[2]}")

print("\n=== Blog channels ===")
for r in c.execute(text(
    "SELECT c.handle, c.name FROM channel c "
    "JOIN source s ON c.source_id=s.id WHERE s.code='blog' ORDER BY c.name"
)).fetchall():
    print(f"  {r[0]:20s} {r[1]}")

print("\n=== Items per source ===")
for r in c.execute(text(
    "SELECT s.code, COUNT(i.id) AS n FROM item i "
    "JOIN source s ON i.source_id=s.id GROUP BY s.code ORDER BY s.code"
)).fetchall():
    print(f"  {r[0]:20s} {r[1]} items")

print("\n=== Items per blog channel ===")
for r in c.execute(text(
    "SELECT c.handle, c.name, COUNT(i.id) AS n FROM item i "
    "JOIN channel c ON i.channel_id=c.id "
    "JOIN source s ON c.source_id=s.id "
    "WHERE s.code='blog' GROUP BY c.handle, c.name ORDER BY n DESC"
)).fetchall():
    print(f"  {r[0]:20s} {r[1]:20s} {r[2]} items")

# Check no orphaned items
orphan = c.execute(text(
    "SELECT COUNT(*) FROM item i LEFT JOIN source s ON i.source_id=s.id WHERE s.id IS NULL"
)).scalar()
print(f"\n=== Orphaned items (no source): {orphan} ===")