"""Manual demo-memory conversion helpers for Colab and local scripts."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid

from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from memory.stm import delete_messages_by_ids, delete_session_messages


REQUIRED_STM_MESSAGE_FIELDS = {
    "session_id",
    "role",
    "content",
    "timestamp",
    "turn_index",
}

RUN_AT_PATTERN = re.compile(r"^\d{2}:\d{2}$")
EPISODIC_LEARNING_EVENT_TYPE = "learning_event"

LTM_TO_EPISODIC_PROMOTION_RULE = {
    "schedule": "daily",
    "run_at": "03:00",
    "eligible_ltm_condition": "LTM created_at calendar date equals target_date",
    "dedupe_condition": (
        "Skip LTM rows already present in ltm_episodic_promotions or sessions "
        "already present in episodic_memory.source_session_ids"
    ),
}

LTM_TO_EPISODIC_SELECTION_SQL = """
SELECT id, session_id, summary, struggles, strengths, confusions,
       topic_tags, embedding, created_at
FROM ltm
WHERE substr(created_at, 1, 10) = ?
  AND NOT EXISTS (
      SELECT 1
      FROM ltm_episodic_promotions AS promotion
      WHERE promotion.ltm_id = ltm.id
  )
  AND NOT EXISTS (
      SELECT 1
      FROM episodic_memory AS episodic,
           json_each(episodic.source_session_ids) AS source_session
      WHERE source_session.value = ltm.session_id
  )
ORDER BY created_at ASC, id ASC
"""

LTM_TO_EPISODIC_SELECTION_SQL_WITHOUT_EPISODIC = """
SELECT id, session_id, summary, struggles, strengths, confusions,
       topic_tags, embedding, created_at
FROM ltm
WHERE substr(created_at, 1, 10) = ?
  AND NOT EXISTS (
      SELECT 1
      FROM ltm_episodic_promotions AS promotion
      WHERE promotion.ltm_id = ltm.id
  )
ORDER BY created_at ASC, id ASC
"""

LTM_TO_EPISODIC_SELECTION_SQL_WITHOUT_METADATA = """
SELECT id, session_id, summary, struggles, strengths, confusions,
       topic_tags, embedding, created_at
FROM ltm
WHERE substr(created_at, 1, 10) = ?
  AND NOT EXISTS (
      SELECT 1
      FROM episodic_memory AS episodic,
           json_each(episodic.source_session_ids) AS source_session
      WHERE source_session.value = ltm.session_id
  )
ORDER BY created_at ASC, id ASC
"""

LTM_TO_EPISODIC_SELECTION_SQL_WITHOUT_EPISODIC_OR_METADATA = """
SELECT id, session_id, summary, struggles, strengths, confusions,
       topic_tags, embedding, created_at
FROM ltm
WHERE substr(created_at, 1, 10) = ?
ORDER BY created_at ASC, id ASC
"""

DEMO_CONVERSION_MODES = {"load_stm", "stm_to_ltm", "ltm_to_episodic", "all"}


def load_demo_stm_json(path: str | Path) -> dict[str, Any]:
    """Load the raw STM-only demo fixture from *path*.

    The returned dictionary preserves the JSON shape used by
    ``data/demo_memory_stm.json`` so notebook users can inspect and pass it to
    later STM -> LTM -> Episodic conversion steps without extra adapters.
    """

    fixture_path = Path(path)
    with fixture_path.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)

    _validate_demo_stm_fixture(fixture, fixture_path)
    return fixture


def run_demo_memory_conversion(
    input_path: str | Path = "data/demo_memory_stm.json",
    db_path: str | Path = "data/chatbot.db",
    chroma_dir: str | Path = "data/chroma",
    mode: str = "all",
) -> dict[str, Any]:
    """Run the notebook-friendly demo memory conversion pipeline.

    ``mode='all'`` loads raw STM turns, applies the 3-hour idle STM -> LTM
    promotion rule, applies the daily 03:00 LTM -> Episodic rule, and syncs the
    resulting LTM/Episodic rows into the configured Chroma directory.
    """

    normalized_mode = _validate_demo_conversion_mode(mode)
    resolved_input_path = Path(input_path)
    resolved_db_path = Path(db_path)
    resolved_chroma_dir = Path(chroma_dir)
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_chroma_dir.mkdir(parents=True, exist_ok=True)

    fixture = load_demo_stm_json(resolved_input_path)
    conversations = fixture["stm_conversations"]
    logs: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "mode": normalized_mode,
        "input_path": str(resolved_input_path),
        "db_path": str(resolved_db_path),
        "chroma_dir": str(resolved_chroma_dir),
        "loaded_stm_count": 0,
        "stm_to_ltm": None,
        "ltm_to_episodic": None,
        "logs": logs,
    }

    conn = sqlite3.connect(resolved_db_path)
    try:
        conn.row_factory = sqlite3.Row
        _ensure_demo_conversion_tables(conn)

        if normalized_mode in {"load_stm", "stm_to_ltm", "all"}:
            if normalized_mode == "all":
                _reset_demo_conversion_tables(conn)
                _reset_demo_chroma_collections(resolved_chroma_dir)
            loaded_count = _load_stm_fixture_into_db(conn, conversations)
            processed_session_ids = [
                conversation["session_id"] for conversation in conversations
            ]
            result["loaded_stm_count"] = loaded_count
            log_entry = {
                "action": "load_stm_fixture",
                "input_file": str(resolved_input_path),
                "input_path": str(resolved_input_path),
                "session_count": len(conversations),
                "conversation_count": len(conversations),
                "message_count": loaded_count,
                "processed_session_ids": processed_session_ids,
                "db_path": str(resolved_db_path),
            }
            logs.append(log_entry)
            print(
                "[DemoConversion] load_stm_fixture "
                f"input_file={resolved_input_path} "
                f"db_path={resolved_db_path} "
                f"sessions={len(conversations)} "
                f"messages={loaded_count} "
                f"processed_session_ids={processed_session_ids}"
            )

        if normalized_mode in {"stm_to_ltm", "all"}:
            promotion_now = _default_demo_stm_promotion_now(conversations)
            stm_to_ltm = promote_stm_to_ltm_if_idle(
                conn,
                [
                    {
                        "session_id": conversation["session_id"],
                        "recent_topic": conversation["recent_topic"],
                    }
                    for conversation in conversations
                ],
                idle_hours=3,
                now=promotion_now,
            )
            result["stm_to_ltm"] = stm_to_ltm
            _add_sqlite_destination_to_logs(stm_to_ltm["logs"], resolved_db_path)
            logs.extend(stm_to_ltm["logs"])
            ltm_chroma_log = _sync_ltm_rows_to_chroma(conn, resolved_chroma_dir)
            ltm_chroma_log["db_path"] = str(resolved_db_path)
            logs.append(ltm_chroma_log)
            print(
                "[DemoConversion] sync_ltm_chroma "
                f"db_path={resolved_db_path} "
                f"chroma_dir={resolved_chroma_dir} "
                f"chroma_storage_path={ltm_chroma_log['chroma_storage_path']} "
                f"row_count={ltm_chroma_log['row_count']}"
            )

        if normalized_mode in {"ltm_to_episodic", "all"}:
            target_date = _latest_ltm_created_date(conn)
            ltm_to_episodic = promote_ltm_to_episodic_daily(
                conn,
                run_at="03:00",
                date=target_date,
            )
            result["ltm_to_episodic"] = ltm_to_episodic
            _add_sqlite_destination_to_logs(
                ltm_to_episodic["logs"],
                resolved_db_path,
            )
            logs.extend(ltm_to_episodic["logs"])
            episodic_chroma_log = _sync_episodic_rows_to_chroma(
                conn,
                resolved_chroma_dir,
            )
            episodic_chroma_log["db_path"] = str(resolved_db_path)
            logs.append(episodic_chroma_log)
            print(
                "[DemoConversion] sync_episodic_chroma "
                f"db_path={resolved_db_path} "
                f"chroma_dir={resolved_chroma_dir} "
                f"chroma_storage_path={episodic_chroma_log['chroma_storage_path']} "
                f"row_count={episodic_chroma_log['row_count']}"
            )

        _add_timestamps_to_logs(logs)
        print(f"[DemoConversion] completed mode={normalized_mode}")
        return result
    finally:
        conn.close()


def promote_stm_to_ltm_if_idle(
    conn: sqlite3.Connection,
    sessions: list[Mapping[str, Any] | str],
    idle_hours: int | float = 3,
    now: str | datetime | None = None,
    target_date: str | datetime | date_cls | None = None,
) -> dict[str, Any]:
    """Promote idle STM sessions into LTM rows.

    This function is intentionally notebook-friendly: callers can import it in
    Colab, pass an existing SQLite connection plus STM session descriptors, and
    inspect both printed progress and the returned structured log.
    """

    from memory.ltm import create_ltm_table

    create_ltm_table(conn)
    current_time = (
        _coerce_datetime(now) if now is not None else datetime.now(timezone.utc)
    )
    required_idle_seconds = float(idle_hours) * 60 * 60
    normalized_target_date = _normalize_optional_target_date(target_date)
    structured_logs: list[dict[str, Any]] = []

    for session in sessions:
        session_id = _session_id_from_descriptor(session)
        recent_topic = _recent_topic_from_descriptor(session)
        all_messages = _load_stm_messages(conn, session_id)
        if not all_messages:
            log_entry = {
                "session_id": session_id,
                "action": "skip_empty_session",
                "skip_reason": "no_stm_messages",
                "message_count": 0,
            }
            structured_logs.append(log_entry)
            print(
                "[STM->LTM] skip_empty_session "
                f"session_id={session_id} skip_reason=no_stm_messages"
            )
            continue

        messages = all_messages
        if normalized_target_date is not None:
            messages = [
                message
                for message in all_messages
                if _coerce_datetime(message["timestamp"]).date()
                == normalized_target_date
            ]
            if not messages:
                source_dates = sorted(
                    {
                        _coerce_datetime(message["timestamp"]).date().isoformat()
                        for message in all_messages
                    }
                )
                log_entry = {
                    "session_id": session_id,
                    "action": "skip_target_date_mismatch",
                    "skip_reason": "target_date_mismatch",
                    "message_count": 0,
                    "available_message_count": len(all_messages),
                    "target_date": normalized_target_date.isoformat(),
                    "source_dates": source_dates,
                }
                structured_logs.append(log_entry)
                print(
                    "[STM->LTM] skip_target_date_mismatch "
                    f"session_id={session_id} "
                    "skip_reason=target_date_mismatch "
                    f"target_date={normalized_target_date}"
                )
                continue

        last_timestamp = max(
            _coerce_datetime(message["timestamp"]) for message in messages
        )
        idle_seconds = (current_time - last_timestamp).total_seconds()
        idle_hours_elapsed = round(idle_seconds / 3600, 3)
        idle_3h_condition_met = idle_seconds >= required_idle_seconds
        idle_condition_log = {
            "promotion_condition": "idle_3h",
            "idle_3h_condition_met": idle_3h_condition_met,
            "last_message_timestamp": last_timestamp.isoformat(),
            "idle_hours_elapsed": idle_hours_elapsed,
            "required_idle_hours": idle_hours,
            "evaluated_at": current_time.isoformat(),
        }
        if not idle_3h_condition_met:
            log_entry = {
                "session_id": session_id,
                "action": "skip_active_session",
                "skip_reason": "idle_3h_not_met",
                "message_count": len(messages),
                **idle_condition_log,
            }
            if normalized_target_date is not None:
                log_entry["target_date"] = normalized_target_date.isoformat()
            structured_logs.append(log_entry)
            print(
                "[STM->LTM] skip_active_session "
                f"session_id={session_id} "
                "skip_reason=idle_3h_not_met "
                "promotion_condition=idle_3h "
                f"idle_3h_condition_met={idle_3h_condition_met} "
                f"idle_hours={idle_hours_elapsed}"
            )
            continue

        existing = conn.execute(
            "SELECT id FROM ltm WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        if existing is not None:
            log_entry = {
                "session_id": session_id,
                "action": "skip_existing_ltm",
                "skip_reason": "ltm_already_exists",
                "ltm_id": existing[0],
                "message_count": len(messages),
                **idle_condition_log,
            }
            if normalized_target_date is not None:
                log_entry["target_date"] = normalized_target_date.isoformat()
            structured_logs.append(log_entry)
            print(
                "[STM->LTM] skip_existing_ltm "
                f"session_id={session_id} ltm_id={existing[0]} "
                "skip_reason=ltm_already_exists "
                "promotion_condition=idle_3h "
                f"idle_3h_condition_met={idle_3h_condition_met} "
                f"idle_hours={idle_hours_elapsed}"
            )
            continue

        ltm_record = _build_ltm_record(
            session_id=session_id,
            messages=messages,
            recent_topic=recent_topic,
            created_at=current_time.isoformat(),
        )
        conn.execute(
            """
            INSERT INTO ltm (id, session_id, summary, struggles, strengths,
                             confusions, topic_tags, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ltm_record["id"],
                ltm_record["session_id"],
                ltm_record["summary"],
                json.dumps(ltm_record["struggles"], ensure_ascii=False),
                json.dumps(ltm_record["strengths"], ensure_ascii=False),
                json.dumps(ltm_record["confusions"], ensure_ascii=False),
                json.dumps(ltm_record["topic_tags"], ensure_ascii=False),
                json.dumps(ltm_record["embedding"], ensure_ascii=False),
                ltm_record["created_at"],
            ),
        )
        if normalized_target_date is None or len(messages) == len(all_messages):
            deleted_rows = delete_session_messages(conn, session_id)
        else:
            deleted_rows = delete_messages_by_ids(
                conn,
                [str(message["id"]) for message in messages],
            )
        log_entry = {
            "session_id": session_id,
            "action": "promote_stm_to_ltm",
            "ltm_id": ltm_record["id"],
            "message_count": len(messages),
            "deleted_stm_rows": deleted_rows,
            "topic": recent_topic or "Python",
            "topic_tags": ltm_record["topic_tags"],
            "struggles": ltm_record["struggles"],
            "strengths": ltm_record["strengths"],
            "confusions": ltm_record["confusions"],
            **idle_condition_log,
        }
        if normalized_target_date is not None:
            log_entry["target_date"] = normalized_target_date.isoformat()
        structured_logs.append(log_entry)
        print(
            "[STM->LTM] promote_stm_to_ltm "
            f"session_id={session_id} ltm_id={ltm_record['id']} "
            f"messages={len(messages)} "
            f"topic={log_entry['topic']} "
            f"struggles={_format_log_list(ltm_record['struggles'])} "
            f"strengths={_format_log_list(ltm_record['strengths'])} "
            f"confusions={_format_log_list(ltm_record['confusions'])} "
            "promotion_condition=idle_3h "
            f"idle_3h_condition_met={idle_3h_condition_met} "
            f"idle_hours={idle_hours_elapsed}"
        )

    return {
        "promoted_count": sum(
            1 for entry in structured_logs if entry["action"] == "promote_stm_to_ltm"
        ),
        "skipped_count": sum(
            1 for entry in structured_logs if entry["action"] != "promote_stm_to_ltm"
        ),
        "logs": structured_logs,
    }


def promote_ltm_to_episodic_daily(
    conn: sqlite3.Connection,
    run_at: str = "03:00",
    date: str | datetime | date_cls | None = None,
) -> dict[str, Any]:
    """Public LTM -> Episodic daily promotion entrypoint.

    The current conversion step records which LTM rows are eligible for the
    requested daily date and which rows are skipped because their ``created_at``
    date belongs to another day. Later conversion steps can consume the same
    eligible row set to perform the Episodic mutation.
    """

    _validate_sqlite_connection(conn)
    normalized_run_at = _validate_daily_run_at(run_at)
    normalized_date = _normalize_daily_date(date)
    actual_time = _extract_daily_actual_time(date)
    schedule_log = {
        "schedule": "daily",
        "scheduled_run_at": normalized_run_at,
        "scheduled_daily_episodic_conversion_ran": True,
    }
    if actual_time is not None:
        run_window = _daily_run_window(normalized_run_at)
        if not _time_is_inside_daily_window(actual_time, run_window):
            log_entry = {
                "action": "skip_outside_run_at_window",
                **schedule_log,
                "scheduled_daily_episodic_conversion_ran": False,
                "run_at": normalized_run_at,
                "date": normalized_date,
                "target_date": normalized_date,
                "actual_time": _format_minutes_as_hhmm(actual_time),
                "window_start": run_window["window_start"],
                "window_end": run_window["window_end"],
            }
            print(
                "[LTM->Episodic] skip_outside_run_at_window "
                f"run_at={normalized_run_at} "
                f"actual_time={log_entry['actual_time']} "
                f"window={log_entry['window_start']}-{log_entry['window_end']} "
                "scheduled_daily_episodic_conversion_ran=False"
            )
            return {
                "promoted_count": 0,
                "eligible_count": 0,
                "eligible_ltm_ids": [],
                "skipped_count": 1,
                "already_processed_count": 0,
                "audit_counts": _audit_counts([log_entry]),
                "logs": [log_entry],
            }

    _ensure_episodic_table_on_connection(conn)
    _ensure_ltm_episodic_promotion_metadata_table(conn)
    eligible_rows = _select_ltm_rows_for_episodic_promotion(
        conn,
        target_date=normalized_date,
    )
    eligible_row_ids = {row["id"] for row in eligible_rows}
    ltm_rows = _load_ltm_rows_for_daily_promotion(conn)
    structured_logs: list[dict[str, Any]] = []
    eligible_ltm_ids: list[str] = []
    episodic_ids: list[str] = []
    for row in ltm_rows:
        created_date = _coerce_datetime(row["created_at"]).date().isoformat()
        if created_date != normalized_date:
            log_entry = {
                "action": "skip_ltm_date_mismatch",
                **schedule_log,
                "audit_status": "skipped",
                "skip_reason": "target_date_mismatch",
                "ltm_id": row["id"],
                "session_id": row["session_id"],
                "target_date": normalized_date,
                "created_at": row["created_at"],
                "created_date": created_date,
                "run_at": normalized_run_at,
                "date": normalized_date,
            }
            structured_logs.append(log_entry)
            print(
                "[LTM->Episodic] skip_ltm_date_mismatch "
                f"ltm_id={row['id']} target_date={normalized_date} "
                f"created_date={created_date} audit_status=skipped "
                "scheduled_daily_episodic_conversion_ran=True"
            )
            continue

        if row["id"] not in eligible_row_ids:
            log_entry = {
                "action": "skip_existing_episodic",
                **schedule_log,
                "audit_status": "already_processed",
                "skip_reason": "already_processed",
                "ltm_id": row["id"],
                "session_id": row["session_id"],
                "promotion_metadata_found": _ltm_promotion_metadata_exists(
                    conn,
                    str(row["id"]),
                ),
                "target_date": normalized_date,
                "created_at": row["created_at"],
                "created_date": created_date,
                "run_at": normalized_run_at,
                "date": normalized_date,
            }
            structured_logs.append(log_entry)
            print(
                "[LTM->Episodic] skip_existing_episodic "
                f"ltm_id={row['id']} session_id={row['session_id']} "
                "audit_status=already_processed "
                "scheduled_daily_episodic_conversion_ran=True"
            )
            continue

        eligible_ltm_ids.append(row["id"])
        episodic_id = _promote_ltm_row_to_episodic(
            conn,
            row,
            updated_at=_daily_promotion_timestamp(
                target_date=normalized_date,
                run_at=normalized_run_at,
            ),
        )
        episodic_ids.append(episodic_id)
        promotion_recorded = _record_ltm_episodic_promotion(
            conn,
            ltm_id=str(row["id"]),
            session_id=str(row["session_id"]),
            episodic_id=episodic_id,
            promoted_at=_daily_promotion_timestamp(
                target_date=normalized_date,
                run_at=normalized_run_at,
            ),
            target_date=normalized_date,
            run_at=normalized_run_at,
        )
        log_entry = {
            "action": "promote_ltm_to_episodic",
            **schedule_log,
            "audit_status": "promoted",
            "ltm_id": row["id"],
            "episodic_id": episodic_id,
            "session_id": row["session_id"],
            "promotion_metadata_recorded": promotion_recorded,
            "topic": _topic_from_ltm_row(row),
            "episodic_topic_updated": _topic_from_ltm_row(row),
            "topic_tags": _json_string_list(row.get("topic_tags")),
            "strengths": _json_string_list(row.get("strengths")),
            "weaknesses": _json_string_list(row.get("struggles")),
            "questions": _json_string_list(row.get("confusions")),
            "target_date": normalized_date,
            "created_at": row["created_at"],
            "created_date": created_date,
            "run_at": normalized_run_at,
            "date": normalized_date,
        }
        structured_logs.append(log_entry)
        print(
            "[LTM->Episodic] promote_ltm_to_episodic "
            f"ltm_id={row['id']} episodic_id={episodic_id} "
            f"topic={log_entry['topic']} "
            f"episodic_topic_updated={log_entry['episodic_topic_updated']} "
            f"target_date={normalized_date} audit_status=promoted "
            "scheduled_daily_episodic_conversion_ran=True"
        )

    if episodic_ids:
        conn.commit()

    if not structured_logs:
        log_entry = {
            "action": "no_ltm_rows_for_episodic",
            **schedule_log,
            "audit_status": "skipped",
            "skip_reason": "no_ltm_rows",
            "run_at": normalized_run_at,
            "date": normalized_date,
            "target_date": normalized_date,
        }
        structured_logs.append(log_entry)
        print(
            "[LTM->Episodic] no_ltm_rows_for_episodic "
            f"run_at={normalized_run_at} date={normalized_date} "
            "audit_status=skipped "
            "scheduled_daily_episodic_conversion_ran=True"
        )

    audit_counts = _audit_counts(structured_logs)
    return {
        "promoted_count": len(episodic_ids),
        "eligible_count": len(eligible_ltm_ids),
        "eligible_ltm_ids": eligible_ltm_ids,
        "episodic_ids": episodic_ids,
        "skipped_count": sum(
            1 for entry in structured_logs if entry["action"].startswith("skip_")
        ),
        "already_processed_count": audit_counts["already_processed"],
        "audit_counts": audit_counts,
        "logs": structured_logs,
    }


def _audit_counts(logs: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = {
        "promoted": 0,
        "skipped": 0,
        "already_processed": 0,
    }
    for entry in logs:
        audit_status = entry.get("audit_status")
        if audit_status in counts:
            counts[audit_status] += 1
    return counts


def _add_sqlite_destination_to_logs(
    logs: list[dict[str, Any]],
    db_path: Path,
) -> None:
    for entry in logs:
        entry["db_path"] = str(db_path)


def _add_timestamps_to_logs(logs: list[dict[str, Any]]) -> None:
    for entry in logs:
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())


def _format_log_list(values: list[Any]) -> str:
    return "|".join(str(value) for value in values)


def _validate_demo_conversion_mode(mode: Any) -> str:
    if not isinstance(mode, str):
        raise TypeError("mode must be a string")
    normalized_mode = mode.strip()
    if normalized_mode not in DEMO_CONVERSION_MODES:
        allowed = ", ".join(sorted(DEMO_CONVERSION_MODES))
        raise ValueError(f"mode must be one of: {allowed}")
    return normalized_mode


def _ensure_demo_conversion_tables(conn: sqlite3.Connection) -> None:
    from memory.ltm import create_ltm_table
    from memory.stm import init_stm

    init_stm(conn)
    create_ltm_table(conn)
    _ensure_episodic_table_on_connection(conn)
    _ensure_ltm_episodic_promotion_metadata_table(conn)
    conn.commit()


def _reset_demo_conversion_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "stm_messages",
        "ltm",
        "episodic_memory",
        "ltm_episodic_promotions",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def _load_stm_fixture_into_db(
    conn: sqlite3.Connection,
    conversations: list[Mapping[str, Any]],
) -> int:
    inserted_count = 0
    for conversation in conversations:
        for message in conversation["messages"]:
            conn.execute(
                """
                INSERT OR REPLACE INTO stm_messages
                    (id, session_id, role, content, timestamp, turn_index)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.get("id") or f"stm-{uuid.uuid4()}"),
                    message["session_id"],
                    message["role"],
                    message["content"],
                    message["timestamp"],
                    int(message["turn_index"]),
                ),
            )
            inserted_count += 1
    conn.commit()
    return inserted_count


def _default_demo_stm_promotion_now(
    conversations: list[Mapping[str, Any]],
) -> datetime:
    latest_timestamp = max(
        _coerce_datetime(message["timestamp"])
        for conversation in conversations
        for message in conversation["messages"]
    )
    return latest_timestamp + timedelta(hours=3, minutes=1)


def _latest_ltm_created_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(created_at) FROM ltm").fetchone()
    if row is None or row[0] is None:
        return _default_daily_date().isoformat()
    return _coerce_datetime(str(row[0])).date().isoformat()


def _sync_ltm_rows_to_chroma(
    conn: sqlite3.Connection,
    chroma_dir: Path,
) -> dict[str, Any]:
    from memory.ltm import _get_chroma_collection

    rows = conn.execute(
        """
        SELECT id, session_id, summary, struggles, strengths, confusions,
               topic_tags, embedding, created_at
        FROM ltm
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    collection = _get_chroma_collection(chroma_dir)
    synced_ids: list[str] = []
    for row in rows:
        row_dict = dict(row)
        embedding = _json_float_list(row_dict.get("embedding"))
        if not embedding:
            continue
        collection.upsert(
            ids=[str(row_dict["id"])],
            embeddings=[embedding],
            documents=[str(row_dict["summary"])],
            metadatas=[
                {
                    "session_id": str(row_dict["session_id"]),
                    "struggles": ",".join(_json_string_list(row_dict["struggles"])),
                    "strengths": ",".join(_json_string_list(row_dict["strengths"])),
                    "confusions": ",".join(_json_string_list(row_dict["confusions"])),
                    "topic_tags": ",".join(_json_string_list(row_dict["topic_tags"])),
                    "created_at": str(row_dict["created_at"]),
                }
            ],
        )
        synced_ids.append(str(row_dict["id"]))
    return {
        "action": "sync_ltm_chroma",
        "chroma_dir": str(chroma_dir),
        "chroma_storage_path": str(_chroma_storage_path(chroma_dir)),
        "row_count": len(synced_ids),
        "ltm_ids": synced_ids,
    }


def _sync_episodic_rows_to_chroma(
    conn: sqlite3.Connection,
    chroma_dir: Path,
) -> dict[str, Any]:
    from episodic_schema import get_episodic_chroma_collection

    rows = conn.execute(
        """
        SELECT episodic_id, topic, topic_tags, source_session_ids,
               source_message_ids, source_turn_indices, last_message_timestamp,
               occurrence_count, memory_item_type, last_updated, topic_embedding
        FROM episodic_memory
        ORDER BY last_updated ASC, episodic_id ASC
        """
    ).fetchall()
    collection = get_episodic_chroma_collection(chroma_dir)
    synced_ids: list[str] = []
    if collection is None:
        return {
            "action": "sync_episodic_chroma",
            "chroma_dir": str(chroma_dir),
            "chroma_storage_path": str(_chroma_storage_path(chroma_dir)),
            "row_count": 0,
            "episodic_ids": [],
            "skipped_reason": "chromadb_unavailable",
        }

    for row in rows:
        row_dict = dict(row)
        embedding = _json_float_list(row_dict.get("topic_embedding"))
        if not embedding:
            continue
        collection.upsert(
            ids=[str(row_dict["episodic_id"])],
            embeddings=[embedding],
            documents=[str(row_dict["topic"])],
            metadatas=[
                {
                    "episodic_id": str(row_dict["episodic_id"]),
                    "topic": str(row_dict["topic"]),
                    "topic_tags": ",".join(_json_string_list(row_dict["topic_tags"])),
                    "source_session_ids": ",".join(
                        _json_string_list(row_dict["source_session_ids"])
                    ),
                    "source_message_ids": ",".join(
                        _json_string_list(row_dict["source_message_ids"])
                    ),
                    "source_turn_indices": ",".join(
                        str(index)
                        for index in _json_list(row_dict["source_turn_indices"])
                    ),
                    "last_message_timestamp": str(
                        row_dict["last_message_timestamp"] or ""
                    ),
                    "occurrence_count": int(row_dict["occurrence_count"] or 1),
                    "memory_item_type": str(row_dict["memory_item_type"]),
                    "last_updated": str(row_dict["last_updated"]),
                }
            ],
        )
        synced_ids.append(str(row_dict["episodic_id"]))
    return {
        "action": "sync_episodic_chroma",
        "chroma_dir": str(chroma_dir),
        "chroma_storage_path": str(_chroma_storage_path(chroma_dir)),
        "row_count": len(synced_ids),
        "episodic_ids": synced_ids,
    }


def _chroma_storage_path(chroma_dir: Path) -> Path:
    return chroma_dir / "chroma.sqlite3"


def _reset_demo_chroma_collections(chroma_dir: Path) -> None:
    if _chroma_storage_path(chroma_dir).exists():
        return

    try:
        import chromadb
        from chromadb.config import Settings
        from episodic_schema import EPISODIC_COLLECTION_NAME
        from memory.ltm import LTM_COLLECTION_NAME
    except ImportError:
        return

    client = chromadb.PersistentClient(
        path=str(chroma_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    for collection_name in (LTM_COLLECTION_NAME, EPISODIC_COLLECTION_NAME):
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass


def _validate_demo_stm_fixture(fixture: Any, fixture_path: Path) -> None:
    if not isinstance(fixture, dict):
        raise ValueError(f"{fixture_path} must contain a JSON object")
    if sorted(fixture) != ["stm_conversations"]:
        raise ValueError(
            f"{fixture_path} must contain only the 'stm_conversations' key"
        )

    conversations = fixture["stm_conversations"]
    if not isinstance(conversations, list) or not conversations:
        raise ValueError("'stm_conversations' must be a non-empty list")

    for index, conversation in enumerate(conversations):
        _validate_stm_conversation(conversation, index)


def _validate_stm_conversation(conversation: Any, index: int) -> None:
    if not isinstance(conversation, Mapping):
        raise ValueError(f"stm_conversations[{index}] must be an object")
    if sorted(conversation) != ["messages", "recent_topic", "session_id"]:
        raise ValueError(
            "each STM conversation must contain only session_id, recent_topic, "
            "and messages"
        )

    session_id = conversation["session_id"]
    messages = conversation["messages"]
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(f"stm_conversations[{index}].session_id is required")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"stm_conversations[{index}].messages must be non-empty")

    for message_index, message in enumerate(messages):
        _validate_stm_message(message, session_id, index, message_index)


def _validate_stm_message(
    message: Any,
    conversation_session_id: str,
    conversation_index: int,
    message_index: int,
) -> None:
    if not isinstance(message, Mapping):
        raise ValueError(
            f"stm_conversations[{conversation_index}].messages[{message_index}] "
            "must be an object"
        )

    missing_fields = REQUIRED_STM_MESSAGE_FIELDS - set(message)
    if missing_fields:
        raise ValueError(
            f"STM message is missing required fields: {sorted(missing_fields)}"
        )
    if message["session_id"] != conversation_session_id:
        raise ValueError("STM message session_id must match its conversation")
    if message.get("memory_type", "stm") != "stm":
        raise ValueError("STM message memory_type must be 'stm' when provided")


def _validate_sqlite_connection(conn: Any) -> sqlite3.Connection:
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("conn must be sqlite3.Connection")
    return conn


def _validate_daily_run_at(run_at: Any) -> str:
    if not isinstance(run_at, str):
        raise TypeError("run_at must be an HH:MM string")
    if RUN_AT_PATTERN.fullmatch(run_at) is None:
        raise ValueError("run_at must use HH:MM format")

    hour_text, minute_text = run_at.split(":")
    hour = int(hour_text)
    minute = int(minute_text)
    if not 0 <= hour <= 23:
        raise ValueError("run_at hour must be 00..23")
    if not 0 <= minute <= 59:
        raise ValueError("run_at minute must be 00..59")
    return run_at


def _normalize_daily_date(value: Any) -> str | None:
    if value is None:
        return _default_daily_date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_cls):
        return value.isoformat()
    if not isinstance(value, str):
        raise TypeError("date must be a datetime, YYYY-MM-DD string, or None")
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as exc:
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            raise ValueError(
                "date must use YYYY-MM-DD or ISO datetime format"
            ) from exc


def _extract_daily_actual_time(value: Any) -> int | None:
    if value is None or (
        isinstance(value, date_cls) and not isinstance(value, datetime)
    ):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if "T" not in value and " " not in value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed.hour * 60 + parsed.minute


def _daily_run_window(run_at: str) -> dict[str, str | int]:
    hour_text, minute_text = run_at.split(":")
    start_minutes = int(hour_text) * 60 + int(minute_text)
    end_minutes = (start_minutes + 59) % (24 * 60)
    return {
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "window_start": _format_minutes_as_hhmm(start_minutes),
        "window_end": _format_minutes_as_hhmm(end_minutes),
    }


def _time_is_inside_daily_window(actual_minutes: int, window: Mapping[str, Any]) -> bool:
    start_minutes = int(window["start_minutes"])
    end_minutes = int(window["end_minutes"])
    if start_minutes <= end_minutes:
        return start_minutes <= actual_minutes <= end_minutes
    return actual_minutes >= start_minutes or actual_minutes <= end_minutes


def _format_minutes_as_hhmm(minutes: int) -> str:
    normalized = minutes % (24 * 60)
    hour, minute = divmod(normalized, 60)
    return f"{hour:02d}:{minute:02d}"


def _normalize_optional_target_date(value: Any) -> date_cls | None:
    if value is None:
        return None
    return date_cls.fromisoformat(_normalize_daily_date(value))


def _default_daily_date() -> date_cls:
    return datetime.now(timezone.utc).date()


def _session_id_from_descriptor(session: Mapping[str, Any] | str) -> str:
    if isinstance(session, str):
        session_id = session
    else:
        session_id = str(session.get("session_id", ""))
    if not session_id.strip():
        raise ValueError("session_id is required for STM promotion")
    return session_id.strip()


def _recent_topic_from_descriptor(session: Mapping[str, Any] | str) -> str:
    if isinstance(session, str):
        return ""
    return str(session.get("recent_topic", "")).strip()


def _coerce_datetime(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_stm_messages(
    conn: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, session_id, role, content, timestamp, turn_index
        FROM stm_messages
        WHERE session_id = ?
        ORDER BY turn_index ASC
        """,
        (session_id,),
    ).fetchall()
    cols = ("id", "session_id", "role", "content", "timestamp", "turn_index")
    return [dict(zip(cols, row)) for row in rows]


def _load_ltm_rows_for_daily_promotion(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm'"
    ).fetchone()
    if table_exists is None:
        return []

    rows = conn.execute(
        """
        SELECT id, session_id, summary, struggles, strengths, confusions,
               topic_tags, embedding, created_at
        FROM ltm
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()
    cols = (
        "id",
        "session_id",
        "summary",
        "struggles",
        "strengths",
        "confusions",
        "topic_tags",
        "embedding",
        "created_at",
    )
    return [dict(zip(cols, row)) for row in rows]


def _ensure_episodic_table_on_connection(conn: sqlite3.Connection) -> None:
    from episodic_schema import EPISODIC_DDL

    conn.execute(EPISODIC_DDL)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(episodic_memory)").fetchall()
    }
    migrations = {
        "topic_tags": "ALTER TABLE episodic_memory ADD COLUMN topic_tags TEXT NOT NULL DEFAULT '[]'",
        "strengths": "ALTER TABLE episodic_memory ADD COLUMN strengths TEXT NOT NULL DEFAULT '[]'",
        "weaknesses": "ALTER TABLE episodic_memory ADD COLUMN weaknesses TEXT NOT NULL DEFAULT '[]'",
        "questions": "ALTER TABLE episodic_memory ADD COLUMN questions TEXT NOT NULL DEFAULT '[]'",
        "source_session_ids": "ALTER TABLE episodic_memory ADD COLUMN source_session_ids TEXT NOT NULL DEFAULT '[]'",
        "source_message_ids": "ALTER TABLE episodic_memory ADD COLUMN source_message_ids TEXT NOT NULL DEFAULT '[]'",
        "source_turn_indices": "ALTER TABLE episodic_memory ADD COLUMN source_turn_indices TEXT NOT NULL DEFAULT '[]'",
        "source_message_timestamps": "ALTER TABLE episodic_memory ADD COLUMN source_message_timestamps TEXT NOT NULL DEFAULT '[]'",
        "topic_contexts": "ALTER TABLE episodic_memory ADD COLUMN topic_contexts TEXT NOT NULL DEFAULT '[]'",
        "occurrence_count": "ALTER TABLE episodic_memory ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1",
        "memory_item_type": "ALTER TABLE episodic_memory ADD COLUMN memory_item_type TEXT NOT NULL DEFAULT 'learning_event'",
        "last_message_timestamp": "ALTER TABLE episodic_memory ADD COLUMN last_message_timestamp TEXT",
        "topic_embedding": "ALTER TABLE episodic_memory ADD COLUMN topic_embedding TEXT",
        "last_updated": "ALTER TABLE episodic_memory ADD COLUMN last_updated TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_episodic_memory_item_type
        ON episodic_memory(memory_item_type)
        """
    )


def _ensure_ltm_episodic_promotion_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ltm_episodic_promotions (
            ltm_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            episodic_id TEXT NOT NULL,
            promoted_at TEXT NOT NULL,
            target_date TEXT NOT NULL,
            run_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ltm_episodic_promotions_session_id
        ON ltm_episodic_promotions(session_id)
        """
    )


def _ltm_episodic_promotion_metadata_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name='ltm_episodic_promotions'
            """
        ).fetchone()
        is not None
    )


def _ltm_promotion_metadata_exists(conn: sqlite3.Connection, ltm_id: str) -> bool:
    if not _ltm_episodic_promotion_metadata_table_exists(conn):
        return False
    return (
        conn.execute(
            """
            SELECT 1
            FROM ltm_episodic_promotions
            WHERE ltm_id = ?
            LIMIT 1
            """,
            (ltm_id,),
        ).fetchone()
        is not None
    )


def _record_ltm_episodic_promotion(
    conn: sqlite3.Connection,
    *,
    ltm_id: str,
    session_id: str,
    episodic_id: str,
    promoted_at: str,
    target_date: str,
    run_at: str,
) -> bool:
    _ensure_ltm_episodic_promotion_metadata_table(conn)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO ltm_episodic_promotions
            (ltm_id, session_id, episodic_id, promoted_at, target_date, run_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ltm_id, session_id, episodic_id, promoted_at, target_date, run_at),
    )
    return cursor.rowcount == 1


def _promote_ltm_row_to_episodic(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    updated_at: str,
) -> str:
    topic_tags = _json_string_list(row.get("topic_tags"))
    topic = _topic_from_ltm_row(row)
    strengths = _json_string_list(row.get("strengths"))
    weaknesses = _json_string_list(row.get("struggles"))
    questions = _json_string_list(row.get("confusions"))
    embedding = _json_float_list(row.get("embedding"))
    source_session_ids = [str(row["session_id"])]
    source_message_ids = [str(row["id"])]
    source_turn_indices: list[int] = []
    source_message_timestamps = [str(row["created_at"])]
    topic_context = {
        "ltm_id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "summary": str(row["summary"]),
        "created_at": str(row["created_at"]),
    }

    existing = conn.execute(
        """
        SELECT *
        FROM episodic_memory
        WHERE topic = ? AND memory_item_type = ?
        """,
        (topic, EPISODIC_LEARNING_EVENT_TYPE),
    ).fetchone()

    if existing is None:
        episodic_id = f"episode-{uuid.uuid4()}"
        conn.execute(
            """
            INSERT INTO episodic_memory
                (episodic_id, topic, topic_tags, strengths, weaknesses,
                 questions, source_session_ids, source_message_ids,
                 source_turn_indices, source_message_timestamps,
                 topic_contexts, occurrence_count, memory_item_type,
                 last_message_timestamp, topic_embedding, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episodic_id,
                topic,
                json.dumps(topic_tags or [topic], ensure_ascii=False),
                json.dumps(strengths, ensure_ascii=False),
                json.dumps(weaknesses, ensure_ascii=False),
                json.dumps(questions, ensure_ascii=False),
                json.dumps(source_session_ids, ensure_ascii=False),
                json.dumps(source_message_ids, ensure_ascii=False),
                json.dumps(source_turn_indices, ensure_ascii=False),
                json.dumps(source_message_timestamps, ensure_ascii=False),
                json.dumps([topic_context], ensure_ascii=False),
                1,
                EPISODIC_LEARNING_EVENT_TYPE,
                str(row["created_at"]),
                json.dumps(embedding, ensure_ascii=False),
                updated_at,
            ),
        )
        return episodic_id

    existing_dict = _sqlite_row_to_plain_dict(existing)
    episodic_id = str(existing_dict["episodic_id"])
    conn.execute(
        """
        UPDATE episodic_memory
        SET topic_tags = ?,
            strengths = ?,
            weaknesses = ?,
            questions = ?,
            source_session_ids = ?,
            source_message_ids = ?,
            source_turn_indices = ?,
            source_message_timestamps = ?,
            topic_contexts = ?,
            occurrence_count = ?,
            last_message_timestamp = ?,
            topic_embedding = COALESCE(?, topic_embedding),
            last_updated = ?
        WHERE episodic_id = ?
        """,
        (
            json.dumps(
                _merge_ordered_json_values(existing_dict["topic_tags"], topic_tags),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(existing_dict["strengths"], strengths),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(existing_dict["weaknesses"], weaknesses),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(existing_dict["questions"], questions),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(
                    existing_dict["source_session_ids"], source_session_ids
                ),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(
                    existing_dict["source_message_ids"], source_message_ids
                ),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(
                    existing_dict["source_turn_indices"], source_turn_indices
                ),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(
                    existing_dict["source_message_timestamps"],
                    source_message_timestamps,
                ),
                ensure_ascii=False,
            ),
            json.dumps(
                _merge_ordered_json_values(
                    existing_dict["topic_contexts"], [topic_context]
                ),
                ensure_ascii=False,
            ),
            int(existing_dict.get("occurrence_count") or 0) + 1,
            max(str(existing_dict.get("last_message_timestamp") or ""), str(row["created_at"])),
            json.dumps(embedding, ensure_ascii=False) if embedding else None,
            updated_at,
            episodic_id,
        ),
    )
    return episodic_id


def _sqlite_row_to_plain_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    columns = (
        "episodic_id",
        "topic",
        "topic_tags",
        "strengths",
        "weaknesses",
        "questions",
        "source_session_ids",
        "source_message_ids",
        "source_turn_indices",
        "source_message_timestamps",
        "topic_contexts",
        "occurrence_count",
        "memory_item_type",
        "last_message_timestamp",
        "topic_embedding",
        "last_updated",
    )
    return dict(zip(columns, row))


def _json_string_list(value: Any) -> list[str]:
    parsed = _json_list(value)
    return [str(item) for item in parsed if str(item).strip()]


def _topic_from_ltm_row(row: Mapping[str, Any]) -> str:
    topic_tags = _json_string_list(row.get("topic_tags"))
    return topic_tags[0] if topic_tags else "programming:python-basics"


def _json_float_list(value: Any) -> list[float]:
    parsed = _json_list(value)
    floats: list[float] = []
    for item in parsed:
        try:
            floats.append(float(item))
        except (TypeError, ValueError):
            continue
    return floats


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value.strip() else []
        return parsed if isinstance(parsed, list) else []
    return []


def _merge_ordered_json_values(existing_json: Any, additions: list[Any]) -> list[Any]:
    merged: list[Any] = []
    for item in [*_json_list(existing_json), *additions]:
        if item not in merged:
            merged.append(item)
    return merged


def _daily_promotion_timestamp(*, target_date: str, run_at: str) -> str:
    return f"{target_date}T{run_at}:00"


def _select_ltm_rows_for_episodic_promotion(
    conn: sqlite3.Connection,
    *,
    target_date: str,
) -> list[dict[str, Any]]:
    """Select LTM rows eligible for the daily LTM -> Episodic promotion.

    Conditions:
    - the ``ltm`` table must exist;
    - ``created_at`` must belong to the requested local calendar date;
    - when ``episodic_memory.source_session_ids`` exists, rows whose session
      already appears there are excluded to keep daily promotion idempotent.
    """

    ltm_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm'"
    ).fetchone()
    if ltm_exists is None:
        return []

    has_episodic_source_sessions = _episodic_source_session_schema_exists(conn)
    has_promotion_metadata = _ltm_episodic_promotion_metadata_table_exists(conn)
    if has_episodic_source_sessions and has_promotion_metadata:
        sql = LTM_TO_EPISODIC_SELECTION_SQL
    elif has_episodic_source_sessions:
        sql = LTM_TO_EPISODIC_SELECTION_SQL_WITHOUT_METADATA
    elif has_promotion_metadata:
        sql = LTM_TO_EPISODIC_SELECTION_SQL_WITHOUT_EPISODIC
    else:
        sql = LTM_TO_EPISODIC_SELECTION_SQL_WITHOUT_EPISODIC_OR_METADATA

    rows = conn.execute(sql, (target_date,)).fetchall()
    cols = (
        "id",
        "session_id",
        "summary",
        "struggles",
        "strengths",
        "confusions",
        "topic_tags",
        "embedding",
        "created_at",
    )
    return [dict(zip(cols, row)) for row in rows]


def _episodic_source_session_schema_exists(conn: sqlite3.Connection) -> bool:
    table_exists = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name='episodic_memory'
        """
    ).fetchone()
    if table_exists is None:
        return False

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(episodic_memory)").fetchall()
    }
    return "source_session_ids" in columns


def _build_ltm_record(
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    recent_topic: str,
    created_at: str,
) -> dict[str, Any]:
    transcript = "\n".join(
        f"{message['role']}: {message['content']}" for message in messages
    )
    topic_tags = _infer_learning_topic_tags(transcript, recent_topic)
    user_questions = [
        str(message["content"])
        for message in messages
        if message["role"] == "user"
        and ("?" in str(message["content"]) or "헷갈" in str(message["content"]))
    ]
    struggles = _infer_struggles(transcript, recent_topic)
    strengths = _infer_strengths(transcript)
    confusions = user_questions[:3] or struggles[:2]
    summary = (
        f"{recent_topic or 'Python'} 학습 대화에서 학습자는 "
        f"{_compact_transcript(transcript)}"
    )
    return {
        "id": f"ltm-{uuid.uuid4()}",
        "memory_type": "ltm",
        "session_id": session_id,
        "summary": summary,
        "struggles": struggles,
        "strengths": strengths,
        "confusions": confusions,
        "topic_tags": topic_tags,
        "embedding": _deterministic_learning_embedding(topic_tags),
        "created_at": created_at,
    }


def _compact_transcript(transcript: str) -> str:
    compact = " ".join(transcript.split())
    return compact[:420]


def _infer_learning_topic_tags(transcript: str, recent_topic: str) -> list[str]:
    text = f"{recent_topic} {transcript}".lower()
    tags: list[str] = []
    keyword_tags = (
        ("programming:python-loops", ("반복", "for ", "while", "range")),
        ("programming:python-conditionals", ("조건", "if ", "elif", "else")),
        ("programming:python-functions", ("함수", "def ", "return", "매개변수", "인자")),
        ("programming:python-exceptions", ("예외", "try", "except", "error")),
        ("programming:python-modules", ("모듈", "import", "from ")),
        ("programming:python-classes", ("클래스", "class ", "self", "객체")),
    )
    for tag, keywords in keyword_tags:
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return tags or ["programming:python-basics"]


def _infer_struggles(transcript: str, recent_topic: str) -> list[str]:
    text = f"{recent_topic} {transcript}"
    struggles: list[str] = []
    keyword_struggles = (
        ("range 끝값 포함 여부", ("range", "끝")),
        ("조건 분기 흐름", ("조건", "if", "else")),
        ("함수 매개변수와 인자 구분", ("매개변수", "인자")),
        ("예외 처리 흐름", ("예외", "except", "try")),
        ("모듈 import 방식", ("모듈", "import")),
        ("클래스와 인스턴스 관계", ("클래스", "인스턴스", "self")),
    )
    for struggle, keywords in keyword_struggles:
        if any(keyword in text for keyword in keywords):
            struggles.append(struggle)
    return struggles or ["파이썬 입문 개념 정리"]


def _infer_strengths(transcript: str) -> list[str]:
    strengths = []
    if any(token in transcript for token in ("예시", "예제", "직접", "실습")):
        strengths.append("예제로 개념을 확인하려는 태도")
    if any(token in transcript for token in ("왜", "차이", "헷갈")):
        strengths.append("혼동 지점을 구체적으로 질문함")
    return strengths or ["학습 대화를 끝까지 이어감"]


def _deterministic_learning_embedding(topic_tags: list[str]) -> list[float]:
    tag_text = " ".join(topic_tags)
    return [
        1.0 if "loops" in tag_text or "conditionals" in tag_text else 0.0,
        1.0 if "functions" in tag_text or "exceptions" in tag_text else 0.0,
        1.0 if "modules" in tag_text or "classes" in tag_text else 0.0,
    ]


__all__ = [
    "load_demo_stm_json",
    "promote_ltm_to_episodic_daily",
    "promote_stm_to_ltm_if_idle",
    "run_demo_memory_conversion",
]
