"""Compute item-to-item similarity links from chunk embeddings."""
from __future__ import annotations

from sqlalchemy import text

from .db import engine
from .logging_setup import get_logger

log = get_logger("links")


def rebuild(top_k: int = 10) -> int:
    """For each item, find its top-k most similar peers (by avg chunk embedding)."""
    with engine().begin() as conn:
        conn.execute(text("DELETE FROM item_link"))
        conn.execute(text("""
          WITH item_vec AS (
            SELECT item_id, AVG(embedding)::vector AS v
            FROM chunk WHERE embedding IS NOT NULL
            GROUP BY item_id
          ),
          pairs AS (
            SELECT a.item_id AS a_id, b.item_id AS b_id,
                   1 - (a.v <=> b.v) AS sim,
                   row_number() OVER (
                     PARTITION BY a.item_id ORDER BY a.v <=> b.v
                   ) AS rk
            FROM item_vec a
            JOIN item_vec b ON a.item_id <> b.item_id
          )
          INSERT INTO item_link(a_id, b_id, similarity)
          SELECT a_id, b_id, sim FROM pairs WHERE rk <= :k
        """), {"k": top_k})
        n = conn.execute(text("SELECT COUNT(*) FROM item_link")).scalar()
    log.info("rebuilt %d item links", n)
    return int(n or 0)
