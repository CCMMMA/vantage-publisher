#!/usr/bin/env python3
"""Threaded VantagePro2 publisher with CSV persistence and MQTT forwarding."""

import os
import json
import time
import csv
import threading
import logging
import sqlite3
import signal
import math
import argparse
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse, urlunparse

import requests
import paho.mqtt.client as mqtt
from pyvantagepro import VantagePro2

from airlink import airlinkData  # local module


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
def setup_logging():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE")  # e.g. /var/log/vantagepro2.log

    lg = logging.getLogger("vantage_publisher")
    if lg.handlers:
        return lg
    lg.setLevel(log_level)
    lg.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    lg.addHandler(sh)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
        fh.setFormatter(fmt)
        lg.addHandler(fh)

    return lg


logger = setup_logging()


def utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def datetime_serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type not serializable: {type(obj)}")


# ---------------------------------------------------------------------
# Offline queue (SQLite)
# ---------------------------------------------------------------------
class OfflineQueueSQLite:
    """Persistent FIFO queue for MQTT store-and-forward using SQLite."""

    def __init__(self, db_path: str, max_messages: int = 200000, max_age_sec: int = 7 * 86400):
        self.db_path = db_path
        self.max_messages = int(max_messages)
        self.max_age_sec = int(max_age_sec)
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        return con

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    qos INTEGER NOT NULL,
                    retain INTEGER NOT NULL
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_queue_ts ON queue(ts)")
            con.commit()

    def enqueue(self, topic: str, payload: str, qos: int = 0, retain: bool = False):
        now = int(time.time())
        with closing(self._connect()) as con:
            con.execute(
                "INSERT INTO queue(ts, topic, payload, qos, retain) VALUES(?,?,?,?,?)",
                (now, topic, payload, int(qos), 1 if retain else 0),
            )
            con.commit()
        self._prune()

    def _prune(self):
        now = int(time.time())
        min_ts = now - self.max_age_sec
        with closing(self._connect()) as con:
            con.execute("DELETE FROM queue WHERE ts < ?", (min_ts,))
            (cnt,) = con.execute("SELECT COUNT(*) FROM queue").fetchone()
            if cnt > self.max_messages:
                drop = cnt - self.max_messages
                con.execute(
                    """
                    DELETE FROM queue
                    WHERE id IN (
                        SELECT id FROM queue ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (drop,),
                )
            con.commit()

    def peek_batch(self, limit: int = 200):
        with closing(self._connect()) as con:
            cur = con.execute(
                """
                SELECT id, topic, payload, qos, retain
                FROM queue
                ORDER BY id ASC
                LIMIT ?
                """,
                (int(limit),),
            )
            return cur.fetchall()

    def delete_ids(self, ids):
        if not ids:
            return
        with closing(self._connect()) as con:
            con.executemany("DELETE FROM queue WHERE id = ?", [(int(i),) for i in ids])
            con.commit()

    def size(self) -> int:
        with closing(self._connect()) as con:
            (cnt,) = con.execute("SELECT COUNT(*) FROM queue").fetchone()
            return int(cnt)


class NoopOfflineQueue:
    """In-memory no-op queue used when MQTT persistence is disabled."""

    def enqueue(self, topic: str, payload: str, qos: int = 0, retain: bool = False):
        return

    def peek_batch(self, limit: int = 200):
        return []

    def delete_ids(self, ids):
        return

    def size(self) -> int:
        return 0


# ---------------------------------------------------------------------
# CSV storage
# ---------------------------------------------------------------------
def ensure_csv_schema(csv_path: Path, new_fields):
    """Ensure CSV header includes new_fields. Returns fieldnames for writing."""
    new_fields = list(new_fields)

    if not csv_path.exists():
        return new_fields

    try:
        with csv_path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            existing = reader.fieldnames or []
            if set(new_fields).issubset(set(existing)):
                return existing

            merged = list(existing)
            for k in new_fields:
                if k not in merged:
                    merged.append(k)

            rows = list(reader)

        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=merged)
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"CSV schema updated: {csv_path}")
        return merged
    except Exception as e:
        logger.error(f"CSV schema update error for {csv_path}: {e}")
        return new_fields


def save_data_to_csv(config_data, packet_data):
    try:
        ts = str(packet_data.get("Datetime", "") or "").strip()
        dt = None
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                dt = None
        if dt is None:
            dt = datetime.utcnow()

        station_uuid = str(config_data.get("station_uuid") or config_data.get("uuid") or "station")
        year = dt.strftime("%Y")
        month = dt.strftime("%m")
        day = dt.strftime("%d")
        hour = dt.strftime("%H")
        ymd = dt.strftime("%Y%m%d")

        root = Path(config_data["pathStorage"])
        hour_dir = root / station_uuid / year / month / day
        hour_dir.mkdir(parents=True, exist_ok=True)

        csv_path = hour_dir / f"{station_uuid}_{ymd}Z{hour}00.csv"

        fieldnames = ensure_csv_schema(csv_path, packet_data.keys())
        with csv_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(packet_data)
    except Exception as e:
        logger.error(f"Error writing to CSV: {e}")


def load_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_parameters_map(path: Path):
    if not path.exists():
        logger.warning(f"Parameters file not found ({path}), all station fields enabled")
        return None
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise SystemExit("parameters.json must contain a JSON object")
    return {str(k): bool(v) for k, v in data.items()}


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        raise ValueError("boolean value is required")
    txt = str(value).strip().lower()
    if txt in ("1", "true", "yes", "on"):
        return True
    if txt in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"invalid boolean value: {value}")


def parse_bool_arg(value):
    try:
        return parse_bool(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def get_cfg_bool(cfg: dict, key: str, default: bool) -> bool:
    if key not in cfg:
        return bool(default)
    try:
        return parse_bool(cfg.get(key))
    except ValueError:
        logger.warning(f"Invalid boolean value for '{key}', using default={default}")
        return bool(default)


def is_parameter_enabled(parameters_map, key: str) -> bool:
    if parameters_map is None:
        return True
    return bool(parameters_map.get(key, False))


def filter_payload(parameters_map, payload: dict) -> dict:
    if parameters_map is None:
        return dict(payload)
    return {k: v for k, v in payload.items() if is_parameter_enabled(parameters_map, k)}


def build_geojson_point(payload, station_uuid, name, latitude, longitude):
    properties = dict(payload)
    properties["uuid"] = station_uuid
    properties["name"] = name
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(longitude), float(latitude)],
        },
        "properties": properties,
    }


def normalize_mqtt_format(value) -> str:
    fmt = str(value or "").strip().lower()
    if fmt in ("geojson", "flat"):
        return fmt
    return "flat"


def sanitize_signalk_key(key: str) -> str:
    out = []
    for ch in str(key):
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "unknown"


SIGNALK_STANDARD_PATHS = {
    "TempOut": "environment.outside.temperature",
    "HumOut": "environment.outside.humidity",
    "Barometer": "environment.outside.pressure",
    "WindSpeed": "environment.wind.speedApparent",
    "WindDir": "environment.wind.angleApparent",
    "TempIn": "environment.inside.temperature",
    "HumIn": "environment.inside.humidity",
}


class SignalKWebsocketPublisher:
    """Best-effort Signal K stream publisher over websocket."""

    def __init__(self, server_url: str, token: str = "", timeout: float = 10.0):
        self.server_url = str(server_url).strip()
        self.token = str(token or "").strip()
        self.timeout = float(timeout)
        self._ws = None

    def _build_url(self) -> str:
        if not self.token:
            return self.server_url
        sep = "&" if "?" in self.server_url else "?"
        return f"{self.server_url}{sep}token={self.token}"

    def _connect(self):
        if self._ws is not None:
            return
        try:
            from websocket import create_connection  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Missing dependency websocket-client. Install with: python3 -m pip install websocket-client"
            ) from e
        self._ws = create_connection(self._build_url(), timeout=self.timeout)

    def publish(self, packet_json: str):
        try:
            self._connect()
            self._ws.send(packet_json)
        except Exception:
            self.close()
            raise

    def close(self):
        if self._ws is None:
            return
        try:
            self._ws.close()
        except Exception:
            pass
        self._ws = None


def signalk_http_base(server_url: str) -> str:
    parsed = urlparse(str(server_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    scheme = "https" if parsed.scheme == "wss" else "http" if parsed.scheme == "ws" else parsed.scheme
    path = parsed.path or ""
    if "/signalk/v1/stream" in path:
        prefix = path.split("/signalk/v1/stream", 1)[0]
        base_path = f"{prefix}/signalk/v1"
    elif path.endswith("/stream"):
        base_path = path[: -len("/stream")]
    else:
        base_path = path.rstrip("/")
    if not base_path:
        base_path = "/signalk/v1"
    return urlunparse((scheme, parsed.netloc, base_path, "", "", ""))


def save_signalk_token_to_config(config_path: Path, token: str):
    token = str(token or "").strip()
    if not token:
        return
    try:
        cfg = load_json_file(config_path)
        if not isinstance(cfg, dict):
            logger.warning(f"Cannot persist Signal K token: config is not a JSON object ({config_path})")
            return
        if str(cfg.get("signalkToken", "") or "").strip() == token:
            return
        cfg["signalkToken"] = token
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info(f"Signal K token saved to {config_path}")
    except Exception as e:
        logger.warning(f"Failed to persist Signal K token to config: {e}")


class SignalKAccessManager:
    """Manage Signal K security checks, token acquisition, and token persistence."""

    def __init__(
        self,
        server_url: str,
        station_uuid: str,
        config_path: Path,
        initial_token: str = "",
        timeout: float = 10.0,
        check_interval_sec: int = 60,
        request_retry_sec: int = 300,
    ):
        self.server_url = str(server_url or "").strip()
        self.http_base = signalk_http_base(self.server_url)
        self.station_uuid = str(station_uuid or "station")
        self.config_path = Path(config_path)
        self.timeout = float(timeout)
        self.check_interval_sec = max(10, int(check_interval_sec))
        self.request_retry_sec = max(30, int(request_retry_sec))
        self.token = str(initial_token or "").strip()
        self.security_enabled = None
        self.request_href = ""
        self.next_check_at = 0.0
        self.next_request_at = 0.0
        self.request_id = f"vantage-{self.station_uuid}"

    def _url(self, suffix: str) -> str:
        return f"{self.http_base.rstrip('/')}/{suffix.lstrip('/')}"

    def _token_from_response(self, data) -> str:
        if not isinstance(data, dict):
            return ""
        for key in ("token", "jwt", "accessToken"):
            val = data.get(key)
            if val:
                return str(val).strip()
        validate_obj = data.get("validate")
        if isinstance(validate_obj, dict):
            tok = validate_obj.get("token")
            if tok:
                return str(tok).strip()
        return ""

    def _check_security_enabled(self):
        if not self.http_base:
            return None
        url = self._url("access/requests")
        try:
            r = requests.get(url, timeout=self.timeout)
        except Exception as e:
            logger.warning(f"Signal K security check failed ({url}): {e}")
            return None
        if r.status_code in (200, 202, 401, 403):
            return True
        if r.status_code in (404, 405, 501):
            return False
        logger.warning(f"Signal K security check unexpected HTTP {r.status_code} ({url})")
        return None

    def _validate_token(self):
        if not self.token or not self.http_base:
            return False
        url = self._url("auth/validate")
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
        except Exception as e:
            logger.warning(f"Signal K token validate request failed ({url}): {e}")
            return None
        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                data = {}
            refreshed = self._token_from_response(data)
            if refreshed and refreshed != self.token:
                self.token = refreshed
                save_signalk_token_to_config(self.config_path, refreshed)
            return True
        if r.status_code in (401, 403):
            return False
        if r.status_code in (404, 405, 501):
            return None
        logger.warning(f"Signal K token validation unexpected HTTP {r.status_code} ({url})")
        return None

    def _resolve_request_url(self, href: str) -> str:
        href = str(href or "").strip()
        if not href:
            return ""
        if href.startswith("http://") or href.startswith("https://"):
            return href
        parsed = urlparse(self.http_base)
        return urlunparse((parsed.scheme, parsed.netloc, href, "", "", ""))

    def _submit_access_request(self):
        if not self.http_base:
            return False
        url = self._url("access/requests")
        payload = {
            "clientId": self.request_id,
            "description": f"Vantage Publisher device {self.station_uuid}",
        }
        try:
            r = requests.post(url, json=payload, timeout=self.timeout)
        except Exception as e:
            logger.warning(f"Signal K access request submission failed ({url}): {e}")
            return False
        if r.status_code in (200, 202):
            try:
                data = r.json()
            except Exception:
                data = {}
            self.request_href = str(data.get("href", "") or "").strip()
            state = str(data.get("state", "") or "").upper()
            token = self._token_from_response(data)
            if token:
                self.token = token
                save_signalk_token_to_config(self.config_path, token)
                logger.info("Signal K token acquired from access request response")
                return True
            if self.request_href:
                logger.info(f"Signal K access request state={state or 'PENDING'} href={self.request_href}")
                return False
            logger.warning("Signal K access request accepted without href/token; will retry later")
            return False
        if r.status_code in (404, 405, 501):
            logger.warning("Signal K access requests endpoint unavailable; cannot request token automatically")
            return False
        logger.warning(f"Signal K access request rejected HTTP {r.status_code}: {r.text[:200]}")
        return False

    def _poll_access_request(self):
        if not self.request_href:
            return False
        url = self._resolve_request_url(self.request_href)
        if not url:
            return False
        try:
            r = requests.get(url, timeout=self.timeout)
        except Exception as e:
            logger.warning(f"Signal K access request poll failed ({url}): {e}")
            return False
        if r.status_code != 200:
            logger.warning(f"Signal K access request poll HTTP {r.status_code} ({url})")
            return False
        try:
            data = r.json()
        except Exception:
            data = {}
        state = str(data.get("state", "") or "").upper()
        token = self._token_from_response(data)
        if token:
            self.token = token
            save_signalk_token_to_config(self.config_path, token)
            self.request_href = ""
            logger.info("Signal K access request approved; token acquired")
            return True
        if state in ("DENIED", "REJECTED"):
            logger.warning(f"Signal K access request {state}; will retry later")
            self.request_href = ""
        return False

    def on_ws_error(self, exc: Exception):
        msg = str(exc).lower()
        if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
            if self.token:
                logger.warning("Signal K token rejected by websocket; requesting a new token")
            self.token = ""
            self.request_href = ""
            self.next_request_at = 0.0

    def update(self, now_ts: float):
        if now_ts < self.next_check_at:
            return
        self.next_check_at = now_ts + self.check_interval_sec

        sec = self._check_security_enabled()
        if sec is not None:
            self.security_enabled = sec

        # If security is disabled/unsupported, publishing can proceed without a token.
        if self.security_enabled is False:
            return

        token_state = self._validate_token()
        if token_state is True:
            return
        if token_state is False:
            if self.token:
                logger.warning("Signal K token is invalid; requesting a new token")
            self.token = ""

        if self._poll_access_request():
            return

        if now_ts >= self.next_request_at:
            self._submit_access_request()
            self.next_request_at = now_ts + self.request_retry_sec
            self._poll_access_request()

    def token_for_ws(self) -> str:
        # With security disabled/unknown, keep configured token if present.
        if self.security_enabled is False:
            return ""
        return str(self.token or "").strip()

    def can_publish(self) -> bool:
        if self.security_enabled is False:
            return True
        return bool(self.token_for_ws())

class StorageHTTPRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, username="", password="", **kwargs):
        self._auth_user = str(username or "")
        self._auth_pass = str(password or "")
        super().__init__(*args, directory=directory, **kwargs)

    def _is_authorized(self) -> bool:
        if not self._auth_user:
            return True
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        expected = base64.b64encode(f"{self._auth_user}:{self._auth_pass}".encode("utf-8")).decode("ascii")
        return auth[6:] == expected

    def _send_auth_required(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="vantage-storage"')
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authentication required")

    def do_GET(self):
        if not self._is_authorized():
            self._send_auth_required()
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._is_authorized():
            self._send_auth_required()
            return
        super().do_HEAD()


def start_storage_http_server(http_cfg: dict):
    if not http_cfg.get("enabled"):
        return None, None
    root = Path(http_cfg["root"])
    root.mkdir(parents=True, exist_ok=True)
    handler = partial(
        StorageHTTPRequestHandler,
        directory=str(root),
        username=http_cfg.get("username", ""),
        password=http_cfg.get("password", ""),
    )
    server = ThreadingHTTPServer((http_cfg["host"], int(http_cfg["port"])), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def convert_signalk_value(key: str, value):
    if value is None:
        return None
    if key in ("TempOut", "TempIn"):
        return float(value) + 273.15
    if key == "Barometer":
        return float(value) * 100.0
    if key in ("HumOut", "HumIn"):
        return float(value) / 100.0
    if key == "WindDir":
        return math.radians(float(value))
    return value


def build_signalk_update(
    payload: dict,
    station_uuid: str,
    latitude: float,
    longitude: float,
    signalk_context: str,
    signalk_path_map: dict,
):
    values = [
        {
            "path": "navigation.position",
            "value": {"latitude": float(latitude), "longitude": float(longitude)},
        }
    ]

    for key, raw in payload.items():
        if key in ("position", "name", "uuid", "Datetime", "DatetimeWS"):
            continue
        mapped = signalk_path_map.get(key)
        path = str(mapped).strip() if mapped else SIGNALK_STANDARD_PATHS.get(
            key, f"environment.{sanitize_signalk_key(key)}"
        )
        try:
            value = convert_signalk_value(key, raw)
        except Exception:
            value = raw
        if value is None:
            continue
        values.append({"path": path, "value": value})

    return {
        "context": signalk_context or f"meteo.{station_uuid}",
        "updates": [
            {
                "timestamp": payload.get("Datetime", utc_now_iso()),
                "values": values,
            }
        ],
    }


def build_mqtt_packet(mqtt_format: str, payload: dict, cfg: dict) -> dict:
    if mqtt_format == "geojson":
        return build_geojson_point(
            payload,
            cfg["station_uuid"],
            cfg["name"],
            cfg["latitude"],
            cfg["longitude"],
        )
    return dict(payload)


def normalize_config(cfg: dict) -> dict:
    required = ("uuid", "name", "lat", "lon")
    for key in required:
        if key not in cfg:
            raise SystemExit(f"config.json must define '{key}'")

    root = Path(cfg["pathStorage"]) if cfg.get("pathStorage") else None
    source = f"tcp:127.0.0.1:{int(cfg.get('usbPort', 22222))}"
    timeout = float(cfg.get("timeout", 10))
    station_uuid = str(cfg["uuid"])
    signalk_path_map_raw = cfg.get("signalkPathMap", {})
    signalk_path_map = {}
    if isinstance(signalk_path_map_raw, dict):
        signalk_path_map = {str(k): str(v) for k, v in signalk_path_map_raw.items() if v}
    storage_root = str(root) if root else ""

    return {
        "raw": cfg,
        "source": source,
        "station_uuid": station_uuid,
        "name": str(cfg["name"]),
        "latitude": float(cfg["lat"]),
        "longitude": float(cfg["lon"]),
        "delay": float(cfg.get("delay", cfg.get("usbPollInterval", 2.0))),
        "usb_poll_interval": float(cfg.get("usbPollInterval", 1.0)),
        "timeout": timeout,
        "pathStorage": storage_root,
        "storage_enabled": get_cfg_bool(cfg, "storage", True),
        "mqtt_enabled": get_cfg_bool(cfg, "mqtt", False),
        "signalk_enabled": get_cfg_bool(cfg, "signalk", False),
        "mqtt": {
            "host": cfg.get("mqttBroker"),
            "port": int(cfg.get("mqttPort", 1883)),
            "username": cfg.get("mqttUser"),
            "password": cfg.get("mqttPass"),
            "qos": int(cfg.get("mqttQos", 1)),
            "topic": str(cfg["uuid"]),
            "keepalive": int(cfg.get("mqttKeepalive", 30)),
            "reconnect_sleep": float(cfg.get("mqttReconnectSleep", 1.0)),
        },
        "offline_max_messages": int(cfg.get("offlineMaxMessages", 200000)),
        "offline_max_age_sec": int(cfg.get("offlineMaxAgeSec", 7 * 86400)),
        "airlink_id": str(cfg.get("airlinkId","") or "").strip(),
        "airlink_interval_sec": int(cfg.get("airlinkIntervalSec", 300)),
        "mqtt_format": normalize_mqtt_format(cfg.get("mqttFormat")),
        "signalk_server_url": str(cfg.get("signalkServerUrl", "") or "").strip(),
        "signalk_token": str(cfg.get("signalkToken", "") or "").strip(),
        "signalk_context": str(cfg.get("signalkContext", f"meteo.{station_uuid}") or f"meteo.{station_uuid}"),
        "signalk_path_map": signalk_path_map,
        "http": {
            "enabled": get_cfg_bool(cfg, "httpEnabled", False),
            "host": str(cfg.get("httpHost", "0.0.0.0")),
            "port": int(cfg.get("httpPort", 8080)),
            "username": str(cfg.get("httpUser", "") or ""),
            "password": str(cfg.get("httpPass", "") or ""),
            "root": str(cfg.get("httpRoot", storage_root or ".")),
        },
        "spool_path": Path(
            cfg.get(
                "mqttSpoolFile",
                str((root or Path(".")) / "mqtt_offline_queue.sqlite"),
            )
        ),
    }


def is_mqtt_config_complete(mqtt_cfg: dict) -> bool:
    return bool(mqtt_cfg.get("host")) and bool(mqtt_cfg.get("port"))


# ---------------------------------------------------------------------
# USB reading via ser2net (thread, persistent stream)
# ---------------------------------------------------------------------
class USBReaderThread(threading.Thread):
    """Reads station data continuously and reconnects automatically on failures."""

    def __init__(
        self,
        source: str,
        timeout: float,
        parameters_map,
        stop_event: threading.Event,
        poll_interval_sec: float = 1.0,
    ):
        super().__init__(daemon=True)
        self.source = source
        self.timeout = float(timeout)
        self.parameters_map = parameters_map
        self.stop_event = stop_event
        self.poll_interval_sec = float(poll_interval_sec)
        self._lock = threading.Lock()
        self._latest = {}
        self._latest_ts = 0.0
        self._latest_seq = 0
        self._device = None
        self._retry_delay = 1.0

    def _close_device(self):
        if self._device is None:
            return
        try:
            self._device.close()
        except Exception:
            pass
        self._device = None

    def _ensure_connected(self):
        if self._device is not None:
            return True
        try:
            self._device = VantagePro2.from_url(self.source, timeout=self.timeout)
            logger.info(f"USB reader connected ({self.source})")
            return True
        except Exception as e:
            logger.warning(f"USB connect error ({self.source}): {e}")
            self._device = None
            return False

    def get_latest(self):
        with self._lock:
            return dict(self._latest), float(self._latest_ts), int(self._latest_seq)

    def run(self):
        logger.info(f"USB reader thread started (ser2net endpoint {self.source})")
        while not self.stop_event.is_set():
            if not self._ensure_connected():
                self.stop_event.wait(self._retry_delay)
                continue

            try:
                if hasattr(self._device.link, "settimeout"):
                    self._device.link.settimeout(self.timeout)
                payload = self._device.get_current_data_as_json() or {}
                filtered = filter_payload(self.parameters_map, payload)
                if filtered:
                    with self._lock:
                        self._latest = filtered
                        self._latest_ts = time.time()
                        self._latest_seq += 1
            except Exception as e:
                logger.warning(f"USB stream read error, reconnecting: {e}")
                self._close_device()
                self.stop_event.wait(self._retry_delay)
                continue

            self.stop_event.wait(self.poll_interval_sec)

        self._close_device()
        logger.info("USB reader thread stopped")


# ---------------------------------------------------------------------
# AirLink
# ---------------------------------------------------------------------
#def get_airlink_id(config_data) -> str:
#    broker = config_data.get("mqttBroker")
#    if not broker:
#        return ""
#    device_name = config_data["uuid"]
#    url = f"http://{broker}:8088/get_airlink/{device_name}"
#    try:
#        r = requests.get(url, timeout=3)
#        if r.status_code == 200:
#            j = r.json()
#            return j.get("airlinkID", "") or ""
#        if r.status_code == 404:
#            logger.info("AirLink: instrument not found (404)")
#            return ""
#        logger.warning(f"AirLink: HTTP {r.status_code} for {url}")
#        return ""
#    except Exception as e:
#        logger.error(f"AirLink request failed: {e}")
#        return ""


# ---------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------
mqtt_online = False
offline_queue = None  # set in main()


def flush_offline_queue(mqttc: mqtt.Client, batch_size: int = 200):
    global mqtt_online, offline_queue
    if not mqtt_online or offline_queue is None:
        return

    sent = 0
    while mqtt_online:
        rows = offline_queue.peek_batch(limit=batch_size)
        if not rows:
            break

        ids_to_delete = []
        for (row_id, topic, payload, qos, retain) in rows:
            try:
                info = mqttc.publish(topic, payload, qos=int(qos), retain=bool(retain))
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    ids_to_delete.append(row_id)
                    sent += 1
                else:
                    logger.warning(f"Offline flush publish rc={info.rc}; stopping flush")
                    mqtt_online = False
                    break
            except Exception as e:
                logger.warning(f"Offline flush publish failed: {e}")
                mqtt_online = False
                break

        offline_queue.delete_ids(ids_to_delete)

    if sent:
        logger.info(f"Offline queue flushed: sent={sent}, remaining={offline_queue.size()}")


def on_connect(client, userdata, flags, reason_code, properties=None):
    global mqtt_online
    mqtt_online = True
    logger.info(f"MQTT connected (reason={reason_code}).")
    try:
        flush_offline_queue(client, batch_size=300)
    except Exception as e:
        logger.error(f"Flush on connect failed: {e}")


def on_disconnect(client, userdata, *args):
    global mqtt_online
    mqtt_online = False
    logger.warning("MQTT disconnected. Will auto-reconnect.")


def on_publish(client, userdata, mid, *args):
    logger.debug(f"MQTT published mid={mid}")


def build_mqtt_client(mqtt_cfg: dict, timeout: float) -> mqtt.Client:
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if mqtt_cfg.get("username"):
        mqttc.username_pw_set(username=mqtt_cfg["username"], password=mqtt_cfg.get("password"))

    mqttc.on_connect = on_connect
    mqttc.on_disconnect = on_disconnect
    mqttc.on_publish = on_publish

    mqttc.reconnect_delay_set(min_delay=1, max_delay=30)
    mqttc.connect_async(mqtt_cfg["host"], int(mqtt_cfg["port"]), int(mqtt_cfg.get("keepalive", timeout)))
    mqttc.loop_start()
    return mqttc


def install_signal_handlers(stop_event):
    previous = {}

    def _handle_signal(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, _handle_signal)
    return previous


def restore_signal_handlers(previous):
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Threaded VantagePro2 publisher (CSV + MQTT + optional Signal K websocket)"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON file (default: config.json)",
    )
    parser.add_argument(
        "--parameters",
        default="parameters.json",
        help="Path to parameters JSON file (default: parameters.json)",
    )
    parser.add_argument(
        "--signalk",
        type=parse_bool_arg,
        default=None,
        metavar="true|false",
        help="Enable or disable direct Signal K websocket publishing",
    )
    parser.add_argument(
        "--mqtt",
        type=parse_bool_arg,
        default=None,
        metavar="true|false",
        help="Enable or disable MQTT publishing",
    )
    parser.add_argument(
        "--storage",
        type=parse_bool_arg,
        default=None,
        metavar="true|false",
        help="Enable or disable local CSV storage",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Dry mode: no storage, no MQTT/SignalK/http connections; log generated packets/rows",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    global offline_queue

    logger.info("Starting VantagePro2 publisher")
    args = parse_args()

    config_path = Path(args.config)
    config_data = load_json_file(config_path)
    parameters_data = load_parameters_map(Path(args.parameters))
    cfg = normalize_config(config_data)
    dry_mode = bool(args.dry)

    device_name = cfg["station_uuid"]
    logger.info(f"Device/Topic = {device_name}")

    storage_enabled = cfg["storage_enabled"] if args.storage is None else args.storage
    mqtt_enabled_cfg = cfg["mqtt_enabled"] if args.mqtt is None else args.mqtt
    signalk_enabled_cfg = cfg["signalk_enabled"] if args.signalk is None else args.signalk

    if dry_mode:
        storage_enabled = False
        mqtt_enabled_cfg = False
        signalk_enabled_cfg = False
        logger.info("Dry mode enabled: storage, MQTT, Signal K, and HTTP server connections are disabled")

    if storage_enabled and cfg["pathStorage"]:
        Path(cfg["pathStorage"]).mkdir(parents=True, exist_ok=True)
    elif storage_enabled:
        logger.warning("Storage enabled but pathStorage is empty; CSV storage will be skipped")

    # AirLink
    if dry_mode:
        airlink_id = ""
        logger.info("AirLink disabled in dry mode")
    else:
        # airlink_id = get_airlink_id(config_data)
        airlink_id = cfg["airlink_id"]
        if airlink_id:
            logger.info(f"AirLink enabled: {airlink_id}")
        else:
            logger.info("AirLink not available for this device")

    # MQTT
    mqtt_cfg = cfg["mqtt"]
    mqtt_runtime_enabled = mqtt_enabled_cfg and is_mqtt_config_complete(mqtt_cfg)
    signalk_runtime_enabled = signalk_enabled_cfg and bool(cfg["signalk_server_url"])
    if mqtt_enabled_cfg and not mqtt_runtime_enabled:
        logger.warning("MQTT enabled but broker/port is invalid; MQTT runtime disabled")
    if signalk_enabled_cfg and not signalk_runtime_enabled:
        logger.warning("Signal K enabled but signalkServerUrl is empty; Signal K runtime disabled")

    signalk_ws = None
    signalk_access = None
    signalk_ws_token = ""
    mqttc = None

    if mqtt_runtime_enabled and not dry_mode:
        # Offline buffer DB stored alongside CSV root.
        db_path = str(cfg["spool_path"])
        offline_queue = OfflineQueueSQLite(
            db_path=db_path,
            max_messages=cfg["offline_max_messages"],
            max_age_sec=cfg["offline_max_age_sec"],
        )
        logger.info(f"Offline MQTT queue DB: {db_path} (size={offline_queue.size()})")
    else:
        offline_queue = NoopOfflineQueue()
        logger.info("Offline MQTT queue disabled")

    if mqtt_runtime_enabled and not dry_mode:
        try:
            mqttc = build_mqtt_client(mqtt_cfg, cfg["timeout"])
            logger.info("MQTT client started (async connect)")
        except Exception as e:
            mqtt_runtime_enabled = False
            logger.error(f"MQTT client startup failed: {e}; MQTT runtime disabled")
    else:
        logger.info("MQTT runtime disabled")

    if signalk_runtime_enabled and not dry_mode:
        signalk_access = SignalKAccessManager(
            server_url=cfg["signalk_server_url"],
            station_uuid=cfg["station_uuid"],
            config_path=config_path,
            initial_token=cfg["signalk_token"],
            timeout=cfg["timeout"],
        )
        signalk_access.update(time.time())
        if signalk_access.can_publish():
            signalk_ws_token = signalk_access.token_for_ws()
            signalk_ws = SignalKWebsocketPublisher(
                cfg["signalk_server_url"],
                signalk_ws_token,
                timeout=cfg["timeout"],
            )
            logger.info(f"Signal K direct mode enabled ({cfg['signalk_server_url']})")
        else:
            logger.info("Signal K waiting for a valid token; publish will start after token approval")
    else:
        logger.info("Signal K runtime disabled")

    http_server = None
    if cfg["http"]["enabled"] and not dry_mode:
        if not storage_enabled and cfg["http"]["root"] == cfg["pathStorage"]:
            logger.warning("HTTP server root uses pathStorage while storage is disabled")
        try:
            http_server, _ = start_storage_http_server(cfg["http"])
            logger.info(
                f"HTTP storage server started at http://{cfg['http']['host']}:{cfg['http']['port']}/ (root={cfg['http']['root']})"
            )
        except Exception as e:
            logger.error(f"Failed to start HTTP storage server: {e}")
    elif cfg["http"]["enabled"]:
        logger.info("HTTP storage server disabled in dry mode")
    logger.info(f"MQTT payload format: {cfg['mqtt_format']}")

    logger.info(f"Using ser2net endpoint: {cfg['source']}")

    # Start USB reader thread
    stop_event = threading.Event()
    previous_handlers = install_signal_handlers(stop_event)
    usb_thread = USBReaderThread(
        source=cfg["source"],
        timeout=cfg["timeout"],
        parameters_map=parameters_data,
        stop_event=stop_event,
        poll_interval_sec=cfg["usb_poll_interval"],
    )
    usb_thread.start()

    # AirLink cache
    last_airlink_data = None
    last_airlink_time = 0.0
    AIRLINK_INTERVAL = cfg["airlink_interval_sec"]

    # MQTT publish QoS (0/1)
    QOS = int(mqtt_cfg.get("qos", 1))
    last_seq = -1

    try:
        while not stop_event.is_set():
            pkt, pkt_ts, pkt_seq = usb_thread.get_latest()
            if signalk_runtime_enabled and signalk_access is not None:
                signalk_access.update(time.time())

            if pkt and pkt_seq != last_seq:
                last_seq = pkt_seq
                # Preserve station datetime if present
                if "Datetime" in pkt:
                    pkt["DatetimeWS"] = pkt["Datetime"]

                # Overwrite Datetime to UTC now
                pkt["Datetime"] = utc_now_iso()

                # Add station metadata (your config keys)
                position = {'latitude': cfg["latitude"], 'longitude': cfg["longitude"]}
                pkt["position"] = position
                pkt["name"] = cfg["name"]


                # AirLink cached update
                now = time.time()
                if airlink_id and (last_airlink_data is None or (now - last_airlink_time) > AIRLINK_INTERVAL):
                    try:
                        last_airlink_data = airlinkData(airlink_id)
                        last_airlink_time = now
                    except Exception as e:
                        logger.error(f"AirLink update error: {e}")

                if last_airlink_data:
                    pkt.update(last_airlink_data)

                packet = build_mqtt_packet(cfg["mqtt_format"], pkt, cfg)
                signalk_packet = build_signalk_update(
                    pkt,
                    cfg["station_uuid"],
                    cfg["latitude"],
                    cfg["longitude"],
                    cfg["signalk_context"],
                    cfg["signalk_path_map"],
                )

                # Publish with store-and-forward
                topic = device_name
                payload = json.dumps(packet, separators=(",", ":"), default=datetime_serializer)
                signalk_payload = json.dumps(signalk_packet, separators=(",", ":"), default=datetime_serializer)

                if dry_mode:
                    logger.info(f"CSV_ROW;{json.dumps(pkt, separators=(',', ':'))}")
                    logger.info(f"MQTT_PACKET;{payload}")
                    logger.info(f"SIGNALK_UPDATE;{signalk_payload}")
                    continue

                if storage_enabled:
                    save_data_to_csv(cfg, pkt)
                
                try:
                    if mqtt_runtime_enabled and mqtt_online:
                        info = mqttc.publish(topic, payload, qos=QOS, retain=False)
                        if info.rc != mqtt.MQTT_ERR_SUCCESS:
                            logger.warning(f"Publish rc={info.rc}; enqueue offline")
                            offline_queue.enqueue(topic, payload, qos=QOS, retain=False)
                    elif mqtt_runtime_enabled:
                        offline_queue.enqueue(topic, payload, qos=QOS, retain=False)
                except Exception as e:
                    logger.warning(f"Publish exception: {e}; enqueue offline")
                    if mqtt_runtime_enabled:
                        offline_queue.enqueue(topic, payload, qos=QOS, retain=False)

                if signalk_runtime_enabled:
                    if signalk_access is not None:
                        if signalk_access.can_publish():
                            current_token = signalk_access.token_for_ws()
                            if signalk_ws is None or current_token != signalk_ws_token:
                                if signalk_ws is not None:
                                    signalk_ws.close()
                                signalk_ws_token = current_token
                                signalk_ws = SignalKWebsocketPublisher(
                                    cfg["signalk_server_url"],
                                    signalk_ws_token,
                                    timeout=cfg["timeout"],
                                )
                                logger.info("Signal K websocket publisher is ready")
                        elif signalk_ws is not None:
                            signalk_ws.close()
                            signalk_ws = None
                            signalk_ws_token = ""
                    try:
                        if signalk_ws is not None:
                            signalk_ws.publish(signalk_payload)
                    except Exception as e:
                        logger.warning(f"Signal K websocket publish failed: {e}")
                        if signalk_access is not None:
                            signalk_access.on_ws_error(e)
                        if signalk_ws is not None:
                            signalk_ws.close()
                            signalk_ws = None

                # Opportunistic MQTT flush
                if mqtt_runtime_enabled and mqtt_online and offline_queue.size() > 0:
                    flush_offline_queue(mqttc, batch_size=200)

                logger.info(f"Cycle OK: keys={len(pkt)} offline_q={offline_queue.size()}")

            else:
                # No packet yet or USB read errors
                age = time.time() - pkt_ts if pkt_ts else None
                if age is None or age > max(5.0, cfg["delay"] * 2):
                    logger.warning("No USB data available (station read pending or failing)")

            stop_event.wait(cfg["delay"])

    except KeyboardInterrupt:
        logger.info("Shutdown requested (CTRL+C)")
        stop_event.set()

    finally:
        try:
            stop_event.set()
            usb_thread.join(timeout=3.0)
        except Exception:
            pass

        try:
            if mqttc is not None:
                mqttc.loop_stop()
                mqttc.disconnect()
        except Exception:
            pass
        try:
            if signalk_ws is not None:
                signalk_ws.close()
        except Exception:
            pass
        try:
            if http_server is not None:
                http_server.shutdown()
                http_server.server_close()
        except Exception:
            pass

        restore_signal_handlers(previous_handlers)

        logger.info("Stopped")


if __name__ == "__main__":
    main()
