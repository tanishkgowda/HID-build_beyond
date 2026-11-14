from contextlib import closing
import json
import sqlite3

import numpy as np

from .schemas import Attempt, Participant


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS participants (
                    participant_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enrolled_at TEXT NOT NULL,
                    template TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    participant_id TEXT NOT NULL REFERENCES participants(participant_id),
                    created_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    liveness_score REAL,
                    match_score REAL,
                    frames TEXT NOT NULL,
                    stage_ms TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    deadline_missed INTEGER NOT NULL,
                    scenario TEXT NOT NULL,
                    click_to_render_ms INTEGER,
                    ui_deadline_missed INTEGER
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def enroll(self, participant: Participant, template: np.ndarray):
        with closing(self._connect()) as db, db:
            db.execute("""
                INSERT INTO participants (participant_id, name, enrolled_at, template)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(participant_id) DO UPDATE SET
                    name = excluded.name,
                    enrolled_at = excluded.enrolled_at,
                    template = excluded.template
            """, (participant.participant_id, participant.name, participant.enrolled_at, json.dumps(template.tolist())))

    def template(self, participant_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT template FROM participants WHERE participant_id = ?", (participant_id,)).fetchone()
        return None if row is None else np.array(json.loads(row["template"]), dtype=np.float32)

    def participants(self):
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT participant_id, name, enrolled_at
                FROM participants ORDER BY enrolled_at DESC, participant_id
            """).fetchall()
        return [Participant(**dict(row)) for row in rows]

    def save_attempt(self, attempt: Attempt):
        values = attempt.model_dump(mode="json")
        for field in ("frames", "stage_ms"):
            values[field] = json.dumps(values[field], allow_nan=False)
        with closing(self._connect()) as db, db:
            db.execute("""
                INSERT INTO attempts (
                    attempt_id, participant_id, created_at, outcome, reason,
                    liveness_score, match_score, frames, stage_ms, elapsed_ms, deadline_missed,
                    scenario, click_to_render_ms, ui_deadline_missed
                ) VALUES (
                    :attempt_id, :participant_id, :created_at, :outcome, :reason,
                    :liveness_score, :match_score, :frames, :stage_ms, :elapsed_ms, :deadline_missed,
                    :scenario, :click_to_render_ms, :ui_deadline_missed
                )
            """, values)

    def attempts(self, limit):
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM attempts ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        attempts = []
        for row in rows:
            fields = dict(row)
            for field in ("frames", "stage_ms"):
                fields[field] = json.loads(fields[field])
            attempts.append(Attempt(**fields))
        return attempts

    def set_scenario(self, attempt_id, scenario):
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE attempts SET scenario = ? WHERE attempt_id = ?", (scenario, attempt_id))
            return cursor.rowcount == 1

    def set_client_timing(self, attempt_id, click_to_render_ms, ui_deadline_missed):
        with closing(self._connect()) as db, db:
            cursor = db.execute("""
                UPDATE attempts SET click_to_render_ms = ?, ui_deadline_missed = ?
                WHERE attempt_id = ?
            """, (click_to_render_ms, ui_deadline_missed, attempt_id))
            return cursor.rowcount == 1
