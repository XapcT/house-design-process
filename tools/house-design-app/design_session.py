"""SQLite-backed authoring sessions for the House Design app.

The caller publishes real design edits through these typed functions. The
module never launches a model, reads a chat transcript, runs a generator, or
writes a project file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "custodian.enshrouded.house.v1"
SCENE_SCHEMA = "custodian.enshrouded.scene.v1"
MAX_PARTS = 100000
MAX_SPEC_BYTES = 10 * 1024 * 1024
MAX_FRAGMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_FRAGMENT_BYTES = 10 * 1024 * 1024
MAX_TITLE = 200
MAX_PHASE = 160
VIEWER_HOUSES = Path("D:/Projects/castle-viewer/src/docs/enshrouded-houses")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_REVISION = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Design update must be finite JSON data") from exc


def _canonical_root(root: Path) -> Path:
    path = Path(root).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Workspace root must be a directory")
    return path


def _db_path(root: Path, create: bool = True) -> Path:
    root = _canonical_root(root)
    parts = (root / "work", root / "work" / "house-app-integration",
             root / "work" / "house-app-integration" / "design-sessions")
    for part in parts:
        if part.is_symlink():
            raise ValueError("Session storage cannot traverse a symlink")
        try:
            part.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ValueError("Session storage must stay inside the workspace") from exc
    directory = parts[-1]
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    elif not directory.is_dir():
        raise FileNotFoundError("No design sessions exist")
    db = directory / "sessions.sqlite3"
    if db.is_symlink() or Path(str(db) + "-wal").is_symlink() or Path(str(db) + "-shm").is_symlink():
        raise ValueError("Session database cannot be a symlink")
    return db


def _connect(root: Path, create: bool = True) -> sqlite3.Connection:
    db = _db_path(root, create)
    connection = sqlite3.connect(str(db), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(
            session_id TEXT PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL,
            phase TEXT NOT NULL, sequence INTEGER NOT NULL, completed_actions INTEGER NOT NULL,
            revision TEXT, snapshot_event_sequence INTEGER, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots(
            session_id TEXT NOT NULL REFERENCES sessions(session_id), revision TEXT NOT NULL,
            body BLOB NOT NULL, event_sequence INTEGER NOT NULL,
            PRIMARY KEY(session_id, revision)
        );
        CREATE TABLE IF NOT EXISTS requests(
            scope TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
            response_state TEXT NOT NULL, PRIMARY KEY(scope, request_id)
        );
        CREATE TABLE IF NOT EXISTS events(
            session_id TEXT NOT NULL REFERENCES sessions(session_id), sequence INTEGER NOT NULL,
            request_id TEXT NOT NULL, event_type TEXT NOT NULL, phase TEXT NOT NULL,
            revision TEXT, created_at TEXT NOT NULL, PRIMARY KEY(session_id, sequence)
        );
    """)
    return connection


def _valid_request_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("request_id must be 1-128 safe characters")
    return value


def _state(row: sqlite3.Row) -> dict:
    revision = row["revision"]
    snapshot = None if revision is None else {
        "revision": revision, "event_sequence": row["snapshot_event_sequence"]
    }
    return {"session_id": row["session_id"], "title": row["title"], "state": row["state"],
            "phase": row["phase"], "sequence": row["sequence"],
            "completed_actions": row["completed_actions"], "revision": revision,
            "snapshot": snapshot, "updated_at": row["updated_at"]}


def _fetch_state(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        raise KeyError("Unknown design session")
    return _state(row)


def _prior_request(conn: sqlite3.Connection, scope: str, request_id: str,
                   payload_hash: str) -> dict | None:
    prior = conn.execute("SELECT payload_hash,response_state FROM requests WHERE scope=? AND request_id=?",
                         (scope, request_id)).fetchone()
    if prior is None:
        return None
    if prior["payload_hash"] != payload_hash:
        raise ValueError("request_id was already used with a different payload")
    return json.loads(prior["response_state"])


def _record_request(conn: sqlite3.Connection, scope: str, request_id: str,
                    payload_hash: str, state: dict) -> None:
    conn.execute("INSERT INTO requests(scope,request_id,payload_hash,response_state) VALUES(?,?,?,?)",
                 (scope, request_id, payload_hash, json.dumps(state, ensure_ascii=False, separators=(",", ":"))))


def _validate_path(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise ValueError("Fragment file must be a non-empty path")
    candidate = Path(raw)
    if any(part == ".." for part in candidate.parts):
        raise ValueError("Fragment path traversal is not allowed")
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Fragment source must be an existing readable file") from exc
    allowed = [root.resolve(strict=True)]
    if VIEWER_HOUSES.is_dir():
        allowed.append(VIEWER_HOUSES.resolve(strict=True))
    if not any(_within(base, resolved) for base in allowed):
        raise ValueError("Fragment file must be inside the workspace or original house data")
    if not resolved.is_file():
        raise ValueError("Fragment file must be a regular file")
    return resolved


def _within(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_spec(root: Path, spec: Any) -> bytes:
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        raise ValueError("spec must use the Enshrouded house schema")
    # Work on a detached JSON tree: freezing fragment dependencies must not mutate
    # the caller's request object.
    spec = json.loads(_json_bytes(spec).decode("utf-8"))
    presentation = spec.get("presentation")
    if presentation is not None:
        if not isinstance(presentation, dict) or presentation.get("stage") not in {"sketch", "form", "materials"}:
            raise ValueError("presentation.stage must be sketch, form, or materials")
        intent = presentation.get("intent")
        if intent is not None and (not isinstance(intent, str) or len(intent) > 300):
            raise ValueError("presentation.intent must be a string of at most 300 characters")
    parts = spec.get("parts")
    if not isinstance(parts, list) or len(parts) > MAX_PARTS:
        raise ValueError(f"spec.parts must contain at most {MAX_PARTS} operations")
    forbidden = {"_folder", "_assist", "source_path", "asset_base", "assets", "path", "file_path"}
    total_fragment_bytes = 0
    fragment_dir = _fragment_directory(root)
    def inspect(value: Any) -> None:
        nonlocal total_fragment_bytes
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden:
                    raise ValueError("Renderer paths and external asset metadata are not accepted")
                if str(key).lower() == "file":
                    if value.get("op") != "fragment":
                        raise ValueError("file is allowed only for fragment operations")
                    source = _validate_path(root, child)
                    try:
                        fragment = source.read_bytes()
                    except OSError as exc:
                        raise ValueError("Fragment source must be readable") from exc
                    if len(fragment) > MAX_FRAGMENT_BYTES:
                        raise ValueError("Fragment source exceeds the per-file size limit")
                    total_fragment_bytes += len(fragment)
                    if total_fragment_bytes > MAX_TOTAL_FRAGMENT_BYTES:
                        raise ValueError("Fragment sources exceed the total size limit")
                    try:
                        data = json.loads(fragment.decode("utf-8"))
                    except (UnicodeError, ValueError) as exc:
                        raise ValueError("Fragment source must be valid UTF-8 JSON") from exc
                    valid_scene = (isinstance(data, dict) and data.get("schema") == SCENE_SCHEMA
                                   and isinstance(data.get("materials"), dict) and isinstance(data.get("runs"), list))
                    valid_capture = (isinstance(data, dict) and isinstance(data.get("blocks_by_material"), list)
                                     and isinstance(data.get("voxel_runs_x_y_z_len_material"), list))
                    if not (valid_scene or valid_capture):
                        raise ValueError("Fragment must be an Enshrouded scene or world capture")
                    value[key] = str(_freeze_fragment(root, fragment_dir, fragment))
                else:
                    inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    if not all(isinstance(part, dict) and isinstance(part.get("op"), str) for part in parts):
        raise ValueError("Each design part must be an operation object")
    inspect(spec)
    raw = _json_bytes(spec)
    if len(raw) > MAX_SPEC_BYTES:
        raise ValueError(f"Serialized design exceeds {MAX_SPEC_BYTES} bytes")
    return raw


def _fragment_directory(root: Path) -> Path:
    db = _db_path(root)
    directory = db.parent / "fragments"
    if directory.is_symlink():
        raise ValueError("Frozen fragment storage cannot be a symlink")
    directory.mkdir(exist_ok=True)
    try:
        directory.resolve(strict=True).relative_to(_canonical_root(root))
    except ValueError as exc:
        raise ValueError("Frozen fragments must stay inside the workspace") from exc
    return directory


def _freeze_fragment(root: Path, directory: Path, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    destination = directory / f"{digest}.json"
    if destination.is_symlink():
        raise ValueError("Frozen fragment destination cannot be a symlink")
    if destination.exists():
        try:
            existing = destination.read_bytes()
        except OSError as exc:
            raise ValueError("Existing frozen fragment is unreadable") from exc
        if hashlib.sha256(existing).hexdigest() != digest or existing != content:
            raise ValueError("Existing content-addressed fragment failed integrity check")
        return destination.resolve(strict=True)
    temp = directory / f".{digest}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, destination)
        frozen = destination.read_bytes()
        if hashlib.sha256(frozen).hexdigest() != digest or frozen != content:
            raise ValueError("Frozen fragment integrity check failed")
        return destination.resolve(strict=True)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def begin_design(root: Path, title: str, request_id: str) -> dict:
    root = _canonical_root(root)
    request_id = _valid_request_id(request_id)
    try:
        parsed_request_id = uuid.UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("begin request_id must be a canonical UUIDv4") from exc
    if parsed_request_id.version != 4 or str(parsed_request_id) != request_id:
        raise ValueError("begin request_id must be a canonical UUIDv4")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
        raise ValueError(f"title must be 1-{MAX_TITLE} characters")
    title = title.strip()
    fingerprint = hashlib.sha256(_json_bytes({"op": "begin", "title": title})).hexdigest()
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = _prior_request(conn, "begin", request_id, fingerprint)
        if prior is not None:
            conn.commit()
            return prior
        session_id = str(uuid.uuid4())
        now = _now()
        phase = "Проектирование начато"
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)",
                     (session_id, title, "designing", phase, 1, 0, None, None, now))
        conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                     (session_id, 1, request_id, "begin", phase, None, now))
        result = _fetch_state(conn, session_id)
        _record_request(conn, "begin", request_id, fingerprint, result)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_design(root: Path, session_id: str, request_id: str, expected_revision: str | None,
                  phase: str, spec: dict | None = None, append_parts: list | None = None) -> dict:
    root = _canonical_root(root)
    request_id = _valid_request_id(request_id)
    _validate_session_id(session_id)
    if (spec is None) == (append_parts is None):
        raise ValueError("Provide exactly one of spec or append_parts")
    if not isinstance(phase, str) or not phase.strip() or len(phase.strip()) > MAX_PHASE:
        raise ValueError(f"phase must be 1-{MAX_PHASE} characters")
    if expected_revision is not None and not _REVISION.fullmatch(expected_revision):
        raise ValueError("expected_revision must be a SHA-256 revision or None")
    payload = {"op": "update", "expected_revision": expected_revision, "phase": phase.strip(),
               "spec": spec, "append_parts": append_parts}
    fingerprint = hashlib.sha256(_json_bytes(payload)).hexdigest()
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = _prior_request(conn, session_id, request_id, fingerprint)
        if prior is not None:
            conn.commit()
            return prior
        current = _fetch_state(conn, session_id)
        if current["state"] != "designing":
            raise ValueError("Design session is finished")
        if current["revision"] != expected_revision:
            raise ValueError("Design revision changed; refresh the session before updating")
        if spec is not None:
            if current["revision"] is not None and expected_revision is None:
                raise ValueError("A full replacement requires the current expected_revision")
            updated_spec = spec
        else:
            if current["revision"] is None:
                raise ValueError("The first design update must provide a full spec")
            prior_row = conn.execute("SELECT body FROM snapshots WHERE session_id=? AND revision=?",
                                     (session_id, current["revision"])).fetchone()
            if prior_row is None:
                raise RuntimeError("Current design snapshot is missing")
            updated_spec = json.loads(bytes(prior_row["body"]).decode("utf-8"))
            if not isinstance(append_parts, list):
                raise ValueError("append_parts must be a list")
            updated_spec["parts"] = list(updated_spec["parts"]) + append_parts
        raw = _validate_spec(root, updated_spec)
        revision = hashlib.sha256(raw).hexdigest()
        sequence = current["sequence"] + 1
        now = _now()
        conn.execute("INSERT OR IGNORE INTO snapshots(session_id,revision,body,event_sequence) VALUES(?,?,?,?)",
                     (session_id, revision, raw, sequence))
        conn.execute("UPDATE sessions SET phase=?,sequence=?,completed_actions=completed_actions+1,revision=?,snapshot_event_sequence=?,updated_at=? WHERE session_id=?",
                     (phase.strip(), sequence, revision, sequence, now, session_id))
        conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                     (session_id, sequence, request_id, "update", phase.strip(), revision, now))
        result = _fetch_state(conn, session_id)
        _record_request(conn, session_id, request_id, fingerprint, result)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_phase(root: Path, session_id: str, request_id: str, phase: str,
              state: str = "designing") -> dict:
    root = _canonical_root(root)
    request_id = _valid_request_id(request_id)
    _validate_session_id(session_id)
    if not isinstance(phase, str) or not phase.strip() or len(phase.strip()) > MAX_PHASE:
        raise ValueError(f"phase must be 1-{MAX_PHASE} characters")
    if state not in {"designing", "waiting", "blocked"}:
        raise ValueError("set_phase state must be designing, waiting, or blocked")
    fingerprint = hashlib.sha256(_json_bytes({"op": "phase", "phase": phase.strip(), "state": state})).hexdigest()
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = _prior_request(conn, session_id, request_id, fingerprint)
        if prior is not None:
            conn.commit()
            return prior
        current = _fetch_state(conn, session_id)
        if current["state"] not in {"designing", "waiting", "blocked"}:
            raise ValueError("Design session is finished")
        sequence, now = current["sequence"] + 1, _now()
        conn.execute("UPDATE sessions SET state=?,phase=?,sequence=?,updated_at=? WHERE session_id=?",
                     (state, phase.strip(), sequence, now, session_id))
        conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                     (session_id, sequence, request_id, "phase", phase.strip(), current["revision"], now))
        result = _fetch_state(conn, session_id)
        _record_request(conn, session_id, request_id, fingerprint, result)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_design(root: Path, session_id: str, request_id: str, status: str = "finished") -> dict:
    root = _canonical_root(root)
    request_id = _valid_request_id(request_id)
    _validate_session_id(session_id)
    if status not in {"finished", "failed", "cancelled", "interrupted"}:
        raise ValueError("Unsupported terminal design session status")
    fingerprint = hashlib.sha256(_json_bytes({"op": "finish", "status": status})).hexdigest()
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = _prior_request(conn, session_id, request_id, fingerprint)
        if prior is not None:
            conn.commit()
            return prior
        current = _fetch_state(conn, session_id)
        if current["state"] in {"finished", "failed", "cancelled", "interrupted"}:
            raise ValueError("Design session is already finished")
        phase = {"finished": "Сессия проектирования завершена", "failed": "Сессия проектирования завершилась с ошибкой",
                 "cancelled": "Сессия проектирования отменена", "interrupted": "Сессия проектирования прервана"}[status]
        sequence, now = current["sequence"] + 1, _now()
        conn.execute("UPDATE sessions SET state=?,phase=?,sequence=?,updated_at=? WHERE session_id=?",
                     (status, phase, sequence, now, session_id))
        conn.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                     (session_id, sequence, request_id, "end", phase, current["revision"], now))
        result = _fetch_state(conn, session_id)
        _record_request(conn, session_id, request_id, fingerprint, result)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_state(root: Path, session_id: str) -> dict | None:
    _validate_session_id(session_id)
    try:
        conn = _connect(_canonical_root(root), create=False)
    except FileNotFoundError:
        return None
    try:
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return _state(row) if row is not None else None
    finally:
        conn.close()


def get_snapshot(root: Path, session_id: str, revision: str) -> bytes | None:
    _validate_session_id(session_id)
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        return None
    try:
        conn = _connect(_canonical_root(root), create=False)
    except FileNotFoundError:
        return None
    try:
        row = conn.execute("SELECT body FROM snapshots WHERE session_id=? AND revision=?",
                           (session_id, revision)).fetchone()
        if row is None:
            return None
        raw = bytes(row["body"])
        if len(raw) > MAX_SPEC_BYTES or hashlib.sha256(raw).hexdigest() != revision:
            return None
        return raw
    finally:
        conn.close()


def get_snapshot_event(root: Path, session_id: str, event_sequence: int, revision: str) -> bytes | None:
    """Read a snapshot only when the exact event belongs to this session and revision."""
    _validate_session_id(session_id)
    if isinstance(event_sequence, bool) or not isinstance(event_sequence, int) or event_sequence < 1:
        return None
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        return None
    try:
        conn = _connect(_canonical_root(root), create=False)
    except FileNotFoundError:
        return None
    try:
        row = conn.execute(
            "SELECT s.body FROM events e JOIN snapshots s ON s.session_id=e.session_id AND s.revision=e.revision "
            "WHERE e.session_id=? AND e.sequence=? AND e.event_type='update' AND e.revision=?",
            (session_id, event_sequence, revision)).fetchone()
        if row is None:
            return None
        raw = bytes(row["body"])
        if len(raw) > MAX_SPEC_BYTES or hashlib.sha256(raw).hexdigest() != revision:
            return None
        return raw
    finally:
        conn.close()


def get_updates(root: Path, session_id: str, after_sequence: int, limit: int = 16) -> list[dict]:
    """Return ordered lifecycle events, including immutable design snapshots, after a cursor."""
    _validate_session_id(session_id)
    if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
        raise ValueError("after_sequence must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
        raise ValueError("limit must be between 1 and 32")
    try:
        conn = _connect(_canonical_root(root), create=False)
    except FileNotFoundError:
        return []
    try:
        rows = conn.execute(
            "SELECT e.sequence AS event_sequence,e.event_type,e.revision,e.phase,s.body FROM events e "
            "LEFT JOIN snapshots s ON s.session_id=e.session_id AND s.revision=e.revision "
            "WHERE e.session_id=? AND e.sequence>? "
            "ORDER BY e.sequence LIMIT ?", (session_id, after_sequence, limit)).fetchall()
        result = []
        for row in rows:
            raw = bytes(row["body"]) if row["body"] is not None else None
            if row["event_type"] == "update" and (raw is None or len(raw) > MAX_SPEC_BYTES or hashlib.sha256(raw).hexdigest() != row["revision"]):
                raise ValueError("Stored design snapshot failed integrity check")
            result.append({"event_sequence": row["event_sequence"], "event_type": row["event_type"],
                           "revision": row["revision"], "phase": row["phase"], "raw": raw})
        return result
    finally:
        conn.close()


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _UUID.fullmatch(session_id):
        raise ValueError("Invalid design session_id")
