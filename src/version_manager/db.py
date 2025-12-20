from __future__ import annotations

import json
import os
import sqlite3
import threading
import fnmatch
import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


@dataclass(frozen=True)
class DeviceAuth:
    type: str  # none | bearer | x-device-token
    token: Optional[str] = None


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        _ensure_parent_dir(db_path)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clusters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cluster_id INTEGER NOT NULL,
                    device_key TEXT NOT NULL UNIQUE,
                    vendor TEXT NOT NULL,
                    model TEXT NOT NULL,
                    line_no TEXT,
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    path TEXT NOT NULL,
                    auth_type TEXT NOT NULL,
                    auth_token TEXT,
                    enabled INTEGER NOT NULL,
                    last_state TEXT,
                    last_state_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_devices_cluster ON devices(cluster_id);

                CREATE TABLE IF NOT EXISTS baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cluster_id INTEGER NOT NULL,
                    vendor TEXT NOT NULL,
                    model TEXT NOT NULL,
                    expected_main_version TEXT NOT NULL,
                    allowed_main_globs_json TEXT,
                    note TEXT,
                    effective_from TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(cluster_id, vendor, model),
                    FOREIGN KEY(cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_baselines_lookup ON baselines(cluster_id, vendor, model);

                CREATE TABLE IF NOT EXISTS version_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vendor TEXT NOT NULL,
                    model TEXT NOT NULL,
                    main_version TEXT NOT NULL,
                    changelog_md TEXT,
                    released_at TEXT,
                    risk_level TEXT,
                    checksum TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(vendor, model, main_version)
                );

                CREATE INDEX IF NOT EXISTS idx_version_catalog_lookup ON version_catalog(vendor, model, main_version);

                CREATE TABLE IF NOT EXISTS device_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    http_status INTEGER,
                    latency_ms INTEGER,
                    error TEXT,
                    protocol_version INTEGER,
                    main_version TEXT,
                    firmware_version TEXT,
                    payload_json TEXT,
                    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_device_snapshots_device_time ON device_snapshots(device_id, observed_at DESC);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    old_state TEXT,
                    new_state TEXT,
                    message TEXT,
                    payload_json TEXT,
                    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_device_created_at ON events(device_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    password_salt_b64 TEXT NOT NULL,
                    password_hash_b64 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                """
            )
            self._migrate()

    def _migrate(self) -> None:
        def has_column(table: str, col: str) -> bool:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(r[1] == col for r in rows)

        if not has_column("devices", "last_state"):
            self._conn.execute("ALTER TABLE devices ADD COLUMN last_state TEXT;")
        if not has_column("devices", "last_state_at"):
            self._conn.execute("ALTER TABLE devices ADD COLUMN last_state_at TEXT;")
        if not has_column("devices", "line_no"):
            self._conn.execute("ALTER TABLE devices ADD COLUMN line_no TEXT;")
        if not has_column("baselines", "allowed_main_globs_json"):
            self._conn.execute("ALTER TABLE baselines ADD COLUMN allowed_main_globs_json TEXT;")

    def has_any_user(self) -> bool:
        rows = self._query("SELECT 1 AS x FROM users LIMIT 1")
        return bool(rows)

    @staticmethod
    def _hash_password(password: str, *, salt: bytes, iterations: int = 200_000) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)

    def create_user(self, *, username: str, password: str, role: str = "admin") -> int:
        username = str(username).strip()
        if not username:
            raise ValueError("missing_username")
        if not password:
            raise ValueError("missing_password")
        salt = secrets.token_bytes(16)
        pwd_hash = self._hash_password(password, salt=salt)
        return self._execute(
            """
            INSERT INTO users(username, role, password_salt_b64, password_hash_b64, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username,
                role,
                base64.b64encode(salt).decode("ascii"),
                base64.b64encode(pwd_hash).decode("ascii"),
                _utc_now_iso(),
            ),
        )

    def verify_user(self, *, username: str, password: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            "SELECT id, username, role, password_salt_b64, password_hash_b64, created_at FROM users WHERE username = ?",
            (str(username).strip(),),
        )
        if not rows:
            return None
        u = dict(rows[0])
        try:
            salt = base64.b64decode(u["password_salt_b64"])
            expected = base64.b64decode(u["password_hash_b64"])
        except Exception:
            return None
        got = self._hash_password(password, salt=salt)
        if not secrets.compare_digest(got, expected):
            return None
        return {"id": int(u["id"]), "username": u["username"], "role": u["role"]}

    def create_session(self, *, user_id: int, ttl_seconds: int = 12 * 3600) -> str:
        token = secrets.token_urlsafe(32)
        now = _utc_now_iso()
        # store expires_at as ISO; keep comparison logic in python (simple)
        expires_at = datetime.now(timezone.utc).timestamp() + int(ttl_seconds)
        expires_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self._execute(
            """
            INSERT INTO sessions(token, user_id, created_at, expires_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, int(user_id), now, expires_iso, now),
        )
        return token

    def delete_session(self, *, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def get_session_user(self, *, token: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT s.token, s.user_id, s.expires_at, s.last_seen_at, u.username, u.role
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        )
        if not rows:
            return None
        r = dict(rows[0])
        # expire check
        try:
            exp = r["expires_at"]
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= exp_dt:
                self.delete_session(token=token)
                return None
        except Exception:
            return None
        # touch
        with self._lock, self._conn:
            self._conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (_utc_now_iso(), token))
        return {"user_id": int(r["user_id"]), "username": r["username"], "role": r["role"]}

    def _query(self, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    def _execute(self, sql: str, params: Tuple[Any, ...] = ()) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
            return int(cur.lastrowid)

    def create_cluster(self, name: str, description: Optional[str] = None) -> int:
        return self._execute(
            "INSERT INTO clusters(name, description, created_at) VALUES (?, ?, ?)",
            (name, description, _utc_now_iso()),
        )

    def get_cluster(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        rows = self._query(
            "SELECT id, name, description, created_at FROM clusters WHERE id = ?",
            (cluster_id,),
        )
        return dict(rows[0]) if rows else None

    def get_cluster_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            "SELECT id, name, description, created_at FROM clusters WHERE name = ?",
            (name,),
        )
        return dict(rows[0]) if rows else None

    def list_clusters(self) -> List[Dict[str, Any]]:
        rows = self._query("SELECT id, name, description, created_at FROM clusters ORDER BY id ASC")
        return [dict(r) for r in rows]

    def create_device(
        self,
        *,
        cluster_id: int,
        device_key: str,
        vendor: str,
        model: str,
        line_no: Optional[str] = None,
        ip: str,
        port: int,
        protocol: str,
        path: str,
        auth: DeviceAuth,
        enabled: bool = True,
    ) -> int:
        now = _utc_now_iso()
        return self._execute(
            """
            INSERT INTO devices(
                cluster_id, device_key, vendor, model, line_no, ip, port,
                protocol, path, auth_type, auth_token, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cluster_id,
                device_key,
                vendor,
                model,
                line_no,
                ip,
                int(port),
                protocol,
                path,
                auth.type,
                auth.token,
                1 if enabled else 0,
                now,
                now,
            ),
        )

    def get_device_by_key(self, device_key: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT id, cluster_id, device_key, vendor, model, line_no, ip, port, protocol, path,
                   auth_type, auth_token, enabled, created_at, updated_at
            FROM devices
            WHERE device_key = ?
            """,
            (device_key,),
        )
        return dict(rows[0]) if rows else None

    def upsert_device_by_key(
        self,
        *,
        cluster_id: int,
        device_key: str,
        vendor: str,
        model: str,
        line_no: Optional[str] = None,
        ip: str,
        port: int,
        protocol: str,
        path: str,
        auth: DeviceAuth,
        enabled: bool = True,
    ) -> Tuple[int, str]:
        existing = self.get_device_by_key(device_key)
        if existing:
            self.update_device(
                int(existing["id"]),
                cluster_id=cluster_id,
                vendor=vendor,
                model=model,
                line_no=line_no,
                ip=ip,
                port=int(port),
                protocol=protocol,
                path=path,
                auth=auth,
                enabled=enabled,
            )
            return int(existing["id"]), "updated"
        device_id = self.create_device(
            cluster_id=cluster_id,
            device_key=device_key,
            vendor=vendor,
            model=model,
            line_no=line_no,
            ip=ip,
            port=int(port),
            protocol=protocol,
            path=path,
            auth=auth,
            enabled=enabled,
        )
        return int(device_id), "created"

    def update_device(
        self,
        device_id: int,
        *,
        cluster_id: Optional[int] = None,
        device_key: Optional[str] = None,
        vendor: Optional[str] = None,
        model: Optional[str] = None,
        line_no: Optional[str] = None,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        protocol: Optional[str] = None,
        path: Optional[str] = None,
        auth: Optional[DeviceAuth] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        fields: List[str] = []
        params: List[Any] = []
        for name, value in [
            ("cluster_id", cluster_id),
            ("device_key", device_key),
            ("vendor", vendor),
            ("model", model),
            ("line_no", line_no),
            ("ip", ip),
            ("port", port),
            ("protocol", protocol),
            ("path", path),
        ]:
            if value is not None:
                fields.append(f"{name} = ?")
                params.append(value)
        if auth is not None:
            fields.append("auth_type = ?")
            params.append(auth.type)
            fields.append("auth_token = ?")
            params.append(auth.token)
        if enabled is not None:
            fields.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(_utc_now_iso())
        params.append(device_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE devices SET {', '.join(fields)} WHERE id = ?", tuple(params))

    def delete_device(self, device_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))

    def get_device(self, device_id: int) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT id, cluster_id, device_key, vendor, model, line_no, ip, port, protocol, path,
                   auth_type, auth_token, enabled, last_state, last_state_at, created_at, updated_at
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        )
        return dict(rows[0]) if rows else None

    def list_devices(self, *, cluster_id: Optional[int] = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
        wheres: List[str] = []
        params: List[Any] = []
        if cluster_id is not None:
            wheres.append("cluster_id = ?")
            params.append(cluster_id)
        if enabled_only:
            wheres.append("enabled = 1")
        where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""
        rows = self._query(
            f"""
            SELECT id, cluster_id, device_key, vendor, model, line_no, ip, port, protocol, path,
                   auth_type, auth_token, enabled, last_state, last_state_at, created_at, updated_at
            FROM devices
            {where_sql}
            ORDER BY id ASC
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]

    def upsert_baseline(
        self,
        *,
        cluster_id: int,
        vendor: str,
        model: str,
        expected_main_version: str,
        allowed_main_globs: Optional[List[str]] = None,
        note: Optional[str] = None,
        effective_from: Optional[str] = None,
    ) -> int:
        now = _utc_now_iso()
        allowed_json = None
        if allowed_main_globs is not None:
            cleaned = [str(x).strip() for x in allowed_main_globs if str(x).strip()]
            allowed_json = json.dumps(cleaned, ensure_ascii=False)
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO baselines(cluster_id, vendor, model, expected_main_version, allowed_main_globs_json, note, effective_from, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cluster_id, vendor, model) DO UPDATE SET
                    expected_main_version = excluded.expected_main_version,
                    allowed_main_globs_json = excluded.allowed_main_globs_json,
                    note = excluded.note,
                    effective_from = excluded.effective_from
                """,
                (cluster_id, vendor, model, expected_main_version, allowed_json, note, effective_from, now),
            )
            return int(cur.lastrowid) if cur.lastrowid else 0

    def list_baselines(self, *, cluster_id: Optional[int] = None) -> List[Dict[str, Any]]:
        wheres: List[str] = []
        params: List[Any] = []
        if cluster_id is not None:
            wheres.append("cluster_id = ?")
            params.append(cluster_id)
        where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""
        rows = self._query(
            f"""
            SELECT id, cluster_id, vendor, model, expected_main_version, allowed_main_globs_json, note, effective_from, created_at
            FROM baselines
            {where_sql}
            ORDER BY id ASC
            """,
            tuple(params),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["allowed_main_globs"] = self._parse_globs(d.get("allowed_main_globs_json"))
            out.append(d)
        return out

    def get_baseline(self, *, cluster_id: int, vendor: str, model: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT id, cluster_id, vendor, model, expected_main_version, allowed_main_globs_json, note, effective_from, created_at
            FROM baselines
            WHERE cluster_id = ? AND vendor = ? AND model = ?
            """,
            (cluster_id, vendor, model),
        )
        if not rows:
            return None
        d = dict(rows[0])
        d["allowed_main_globs"] = self._parse_globs(d.get("allowed_main_globs_json"))
        return d

    def delete_baseline(self, baseline_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM baselines WHERE id = ?", (int(baseline_id),))
            return int(cur.rowcount or 0) > 0

    @staticmethod
    def _parse_globs(raw: Any) -> List[str]:
        if not raw:
            return []
        if isinstance(raw, str):
            try:
                v = json.loads(raw)
                if isinstance(v, list):
                    return [str(x) for x in v if str(x).strip()]
            except json.JSONDecodeError:
                return [x.strip() for x in raw.split(",") if x.strip()]
        return []

    @staticmethod
    def baseline_allows(baseline: Dict[str, Any], observed_main: str) -> bool:
        expected = str(baseline.get("expected_main_version") or "")
        if observed_main == expected:
            return True
        globs = baseline.get("allowed_main_globs") or []
        for g in globs:
            if fnmatch.fnmatchcase(observed_main, str(g)):
                return True
        return False

    def update_device_state(self, device_id: int, state: str) -> None:
        now = _utc_now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE devices SET last_state = ?, last_state_at = ?, updated_at = ? WHERE id = ?",
                (state, now, now, int(device_id)),
            )

    def upsert_version_catalog(
        self,
        *,
        vendor: str,
        model: str,
        main_version: str,
        changelog_md: Optional[str] = None,
        released_at: Optional[str] = None,
        risk_level: Optional[str] = None,
        checksum: Optional[str] = None,
    ) -> int:
        now = _utc_now_iso()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO version_catalog(vendor, model, main_version, changelog_md, released_at, risk_level, checksum, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vendor, model, main_version) DO UPDATE SET
                    changelog_md = excluded.changelog_md,
                    released_at = excluded.released_at,
                    risk_level = excluded.risk_level,
                    checksum = excluded.checksum
                """,
                (vendor, model, main_version, changelog_md, released_at, risk_level, checksum, now),
            )
            return int(cur.lastrowid) if cur.lastrowid else 0

    def ensure_version_catalog_entry(self, *, vendor: str, model: str, main_version: str) -> None:
        now = _utc_now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO version_catalog(vendor, model, main_version, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(vendor, model, main_version) DO NOTHING
                """,
                (vendor, model, main_version, now),
            )

    def get_version_catalog_item(self, *, vendor: str, model: str, main_version: str) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT id, vendor, model, main_version, changelog_md, released_at, risk_level, checksum, created_at
            FROM version_catalog
            WHERE vendor = ? AND model = ? AND main_version = ?
            LIMIT 1
            """,
            (vendor, model, main_version),
        )
        return dict(rows[0]) if rows else None

    def list_version_catalog(self, *, vendor: Optional[str] = None, model: Optional[str] = None) -> List[Dict[str, Any]]:
        wheres: List[str] = []
        params: List[Any] = []
        if vendor is not None:
            wheres.append("vendor = ?")
            params.append(vendor)
        if model is not None:
            wheres.append("model = ?")
            params.append(model)
        where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""
        rows = self._query(
            f"""
            SELECT id, vendor, model, main_version, changelog_md, released_at, risk_level, checksum, created_at
            FROM version_catalog
            {where_sql}
            ORDER BY vendor ASC, model ASC, main_version ASC
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]

    def record_snapshot(
        self,
        *,
        device_id: int,
        success: bool,
        http_status: Optional[int],
        latency_ms: Optional[int],
        error: Optional[str],
        protocol_version: Optional[int],
        main_version: Optional[str],
        firmware_version: Optional[str],
        payload: Optional[Dict[str, Any]],
        observed_at: Optional[str] = None,
    ) -> int:
        return self._execute(
            """
            INSERT INTO device_snapshots(
                device_id, observed_at, success, http_status, latency_ms, error,
                protocol_version, main_version, firmware_version, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                observed_at or _utc_now_iso(),
                1 if success else 0,
                http_status,
                latency_ms,
                error,
                protocol_version,
                main_version,
                firmware_version,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )

    def get_latest_snapshot(self, device_id: int) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT id, device_id, observed_at, success, http_status, latency_ms, error,
                   protocol_version, main_version, firmware_version, payload_json
            FROM device_snapshots
            WHERE device_id = ?
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
            """,
            (device_id,),
        )
        if not rows:
            return None
        d = dict(rows[0])
        if d.get("payload_json"):
            try:
                d["payload"] = json.loads(d["payload_json"])
            except json.JSONDecodeError:
                d["payload"] = None
        else:
            d["payload"] = None
        return d

    def get_latest_success_snapshot(self, device_id: int) -> Optional[Dict[str, Any]]:
        rows = self._query(
            """
            SELECT id, device_id, observed_at, success, http_status, latency_ms, error,
                   protocol_version, main_version, firmware_version, payload_json
            FROM device_snapshots
            WHERE device_id = ? AND success = 1
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
            """,
            (device_id,),
        )
        if not rows:
            return None
        d = dict(rows[0])
        if d.get("payload_json"):
            try:
                d["payload"] = json.loads(d["payload_json"])
            except json.JSONDecodeError:
                d["payload"] = None
        else:
            d["payload"] = None
        return d

    def list_device_snapshots(
        self, *, device_id: int, limit: int = 50, offset: int = 0, success_only: bool = False
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where = "WHERE device_id = ?"
        params: List[Any] = [int(device_id)]
        if success_only:
            where += " AND success = 1"
        rows = self._query(
            f"""
            SELECT id, device_id, observed_at, success, http_status, latency_ms, error,
                   protocol_version, main_version, firmware_version, payload_json
            FROM device_snapshots
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("payload_json"):
                try:
                    d["payload"] = json.loads(d["payload_json"])
                except json.JSONDecodeError:
                    d["payload"] = None
            else:
                d["payload"] = None
            out.append(d)
        return out

    def list_device_version_history(self, *, device_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        rows = self._query(
            """
            SELECT
                s.main_version AS main_version,
                MIN(s.observed_at) AS first_seen,
                MAX(s.observed_at) AS last_seen,
                COUNT(*) AS samples,
                vc.changelog_md AS changelog_md,
                vc.released_at AS released_at,
                vc.risk_level AS risk_level,
                vc.checksum AS checksum
            FROM device_snapshots s
            JOIN devices d ON d.id = s.device_id
            LEFT JOIN version_catalog vc
                ON vc.vendor = d.vendor AND vc.model = d.model AND vc.main_version = s.main_version
            WHERE s.device_id = ? AND s.success = 1 AND s.main_version IS NOT NULL AND s.main_version != ''
            GROUP BY s.main_version
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (int(device_id), limit),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["samples"] = int(d.get("samples") or 0)
            out.append(d)
        return out

    def list_status(self) -> List[Dict[str, Any]]:
        devices = self.list_devices()
        out: List[Dict[str, Any]] = []
        for dev in devices:
            snap = self.get_latest_snapshot(int(dev["id"]))
            baseline = self.get_baseline(
                cluster_id=int(dev["cluster_id"]), vendor=str(dev["vendor"]), model=str(dev["model"])
            )
            state = "unknown"
            if snap is None:
                state = "never_polled"
            elif not bool(snap["success"]):
                state = "offline"
            elif baseline is None:
                state = "no_baseline"
            else:
                observed = str(snap["main_version"] or "")
                state = "ok" if self.baseline_allows(baseline, observed) else "mismatch"
            out.append(
                {
                    "device": dev,
                    "baseline": baseline,
                    "latest_snapshot": snap,
                    "state": state,
                }
            )
        return out

    def create_event(
        self,
        *,
        device_id: int,
        event_type: str,
        old_state: Optional[str],
        new_state: Optional[str],
        message: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        return self._execute(
            """
            INSERT INTO events(device_id, created_at, event_type, old_state, new_state, message, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(device_id),
                _utc_now_iso(),
                str(event_type),
                old_state,
                new_state,
                message,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )

    def list_events(self, *, limit: int = 50, device_id: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if device_id is None:
            rows = self._query(
                """
                SELECT id, device_id, created_at, event_type, old_state, new_state, message, payload_json
                FROM events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            rows = self._query(
                """
                SELECT id, device_id, created_at, event_type, old_state, new_state, message, payload_json
                FROM events
                WHERE device_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (int(device_id), limit),
            )
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("payload_json"):
                try:
                    d["payload"] = json.loads(d["payload_json"])
                except json.JSONDecodeError:
                    d["payload"] = None
            else:
                d["payload"] = None
            out.append(d)
        return out
