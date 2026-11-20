"""Read question slots in chronological order, resolving their published answer revisions."""

# Each revision points directly to its original question, including records from older versions.
# Failed retries do not displace a published answer. Both lookups use the session revision index.
VISIBLE_TURNS = """
SELECT v.*, q.id AS root_id, q.ordinal AS position, latest.status AS attempt_status,
       latest.error_code AS attempt_error
FROM turns q
LEFT JOIN turns latest ON latest.id=(
    SELECT id FROM turns WHERE session_id=q.session_id AND source_turn=q.id AND ordinal<:version_cutoff ORDER BY ordinal DESC LIMIT 1)
JOIN turns v ON v.id=COALESCE(
    (SELECT id FROM turns WHERE session_id=q.session_id AND source_turn=q.id AND ordinal<:version_cutoff AND status='succeeded'
     ORDER BY ordinal DESC LIMIT 1),
    CASE WHEN q.status='succeeded' THEN q.id END, latest.id, q.id)
WHERE q.session_id=:sid AND q.source_turn IS NULL AND q.ordinal<:before
"""


def page(db, sid, cutoff, limit):
    return db.execute(
        VISIBLE_TURNS + " ORDER BY q.ordinal DESC LIMIT :limit",
        {"sid": sid, "before": cutoff, "version_cutoff": 2**63 - 1, "limit": limit},
    ).fetchall()


def last_display(db, sid, cutoff):
    row = db.execute(
        VISIBLE_TURNS + " AND v.display_json IS NOT NULL ORDER BY q.ordinal DESC LIMIT 1",
        {"sid": sid, "before": cutoff, "version_cutoff": cutoff},
    ).fetchone()
    return row["display_json"] if row else None


def history_before(db, sid, cutoff, *, questions_only):
    history, remaining = [], 12000
    rows = db.execute(
        VISIBLE_TURNS + " AND v.status='succeeded' ORDER BY q.ordinal DESC LIMIT 6",
        {"sid": sid, "before": cutoff, "version_cutoff": cutoff},
    )
    for row in rows:
        question, answer = row["input_text"], row["answer_text"]
        remaining -= len(question) + (0 if questions_only else len(answer))
        if remaining < 0:
            break
        history.append((question, answer))
    return history[::-1]
