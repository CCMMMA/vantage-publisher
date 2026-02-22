#!/usr/bin/env python3
"""
vantage-publisher-threading.py

MQTT publisher for Davis VantagePro2 via ser2net TCP bridge:
  VantagePro2.from_url("tcp:127.0.0.1:PORT")

Includes:
  - logging (no prints)
  - daily CSV storage (YYYY/MM/YYYY-MM-DD.csv)
  - AirLink integration (cached)
  - MQTT store-and-forward offline buffer (SQLite FIFO)
  - USB read thread (threading-based)

Local dependency:
  - airlink.py providing airlinkData(airlink_id) -> dict

Expected config.json keys (as per your file):
  uuid, name, lon, lat, usbPort, usbPollInterval, delay, timeout, pathStorage,
  mqttBroker, mqttPort, mqttUser, mqttPass, mqttQos,
  offlineMaxMessages, offlineMaxAgeSec, airlinkIntervalSec
"""

import os
import json
import time
import csv
import threading
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler

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
    lg.setLevel(log_level)

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
        ts = packet_data.get("Datetime", utc_now_iso())
        year, month, day = ts[:4], ts[5:7].zfill(2), ts[8:10].zfill(2)

        root = Path(config_data["pathStorage"])
        month_dir = root / year / month
        month_dir.mkdir(parents=True, exist_ok=True)

        csv_path = month_dir / f"{year}-{month}-{day}.csv"

        fieldnames = ensure_csv_schema(csv_path, packet_data.keys())
        with csv_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(packet_data)
    except Exception as e:
        logger.error(f"Error writing to CSV: {e}")


# ---------------------------------------------------------------------
# USB reading via ser2net (thread)
# ---------------------------------------------------------------------
def read_usb(url: str, parameters_data: dict) -> dict:
    """One-shot read from VantagePro2 via TCP URL; safe against missing keys."""
    device = None
    try:
        device = VantagePro2.from_url(url, timeout=3)
        data = device.get_current_data() or {}
        out = {}
        for key, enabled in parameters_data.items():
            if enabled:
                val = data.get(key)
                if val is not None:
                    out[key] = val
        return out
    except Exception as e:
        logger.error(f"USB read error: {e}")
        return {}
    finally:
        try:
            if device is not None:
                device.close()
        except Exception:
            pass


class USBReaderThread(threading.Thread):
    """Continuously reads from the station (ser2net TCP) and stores the latest packet."""

    def __init__(self, usb_url: str, parameters_data: dict, poll_interval_sec: float = 1.0):
        super().__init__(daemon=True)
        self.usb_url = usb_url
        self.parameters_data = parameters_data
        self.poll_interval_sec = float(poll_interval_sec)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest = {}
        self._latest_ts = 0.0

    def stop(self):
        self._stop.set()

    def get_latest(self):
        with self._lock:
            return dict(self._latest), float(self._latest_ts)

    def run(self):
        logger.info(f"USB reader thread started (ser2net endpoint {self.usb_url})")
        while not self._stop.is_set():
            pkt = read_usb(self.usb_url, self.parameters_data)
            now = time.time()
            if pkt:
                with self._lock:
                    self._latest = pkt
                    self._latest_ts = now
            time.sleep(self.poll_interval_sec)
        logger.info("USB reader thread stopped")


# ---------------------------------------------------------------------
# AirLink
# ---------------------------------------------------------------------
def get_airlink_id(config_data) -> str:
    device_name = config_data["uuid"]
    url = f"http://{config_data['mqttBroker']}:8088/get_airlink/{device_name}"
    try:
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            j = r.json()
            return j.get("airlinkID", "") or ""
        if r.status_code == 404:
            logger.info("AirLink: instrument not found (404)")
            return ""
        logger.warning(f"AirLink: HTTP {r.status_code} for {url}")
        return ""
    except Exception as e:
        logger.error(f"AirLink request failed: {e}")
        return ""


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


def on_disconnect(client, userdata, reason_code, properties=None):
    global mqtt_online
    mqtt_online = False
    logger.warning(f"MQTT disconnected (reason={reason_code}). Will auto-reconnect.")


def on_publish(client, userdata, mid, reason_code, properties):
    logger.debug(f"MQTT published mid={mid}")


def build_mqtt_client(config_data) -> mqtt.Client:
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqttc.username_pw_set(username=config_data["mqttUser"], password=config_data["mqttPass"])

    mqttc.on_connect = on_connect
    mqttc.on_disconnect = on_disconnect
    mqttc.on_publish = on_publish

    mqttc.reconnect_delay_set(min_delay=1, max_delay=30)
    mqttc.connect(config_data["mqttBroker"], config_data["mqttPort"], config_data["timeout"])
    mqttc.loop_start()
    return mqttc


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    global offline_queue

    logger.info("Starting VantagePro2 publisher")

    with open("parameters.json", "r") as f:
        parameters_data = json.load(f)

    with open("config.json", "r") as f:
        config_data = json.load(f)

    device_name = config_data["uuid"]
    logger.info(f"Device/Topic = {device_name}")

    Path(config_data["pathStorage"]).mkdir(parents=True, exist_ok=True)

    # Offline buffer DB stored alongside CSV root
    db_path = str(Path(config_data["pathStorage"]) / "mqtt_offline_queue.sqlite")
    offline_queue = OfflineQueueSQLite(
        db_path=db_path,
        max_messages=config_data.get("offlineMaxMessages", 200000),
        max_age_sec=config_data.get("offlineMaxAgeSec", 7 * 86400),
    )
    logger.info(f"Offline MQTT queue DB: {db_path} (size={offline_queue.size()})")

    # AirLink
    airlink_id = get_airlink_id(config_data)
    if airlink_id:
        logger.info(f"AirLink enabled: {airlink_id}")
    else:
        logger.info("AirLink not available for this device")

    # MQTT
    mqttc = build_mqtt_client(config_data)
    logger.info("MQTT client started")

    # ser2net endpoint
    usb_url = f"tcp:127.0.0.1:{config_data['usbPort']}"
    logger.info(f"Using ser2net endpoint: {usb_url}")

    # Start USB reader thread
    usb_thread = USBReaderThread(
        usb_url=usb_url,
        parameters_data=parameters_data,
        poll_interval_sec=float(config_data.get("usbPollInterval", 1.0)),
    )
    usb_thread.start()

    # AirLink cache
    last_airlink_data = None
    last_airlink_time = 0.0
    AIRLINK_INTERVAL = int(config_data.get("airlinkIntervalSec", 300))

    # MQTT publish QoS (0/1)
    QOS = int(config_data.get("mqttQos", 1))

    try:
        while True:
            pkt, pkt_ts = usb_thread.get_latest()

            if pkt:
                # Preserve station datetime if present
                if "Datetime" in pkt:
                    pkt["DatetimeWS"] = pkt["Datetime"]

                # Overwrite Datetime to UTC now
                pkt["Datetime"] = utc_now_iso()

                # Add station metadata (your config keys)
                # pkt["latitude"] = config_data["lat"]
                # pkt["longitude"] = config_data["lon"]
                position = {'latitude': config_data["lat"], 'longitude': config_data["lon"]}
                pkt["position"] = position
                pkt["name"] = config_data["name"]


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

                # Persist CSV
                save_data_to_csv(config_data, pkt)

                # Publish with store-and-forward
                topic = device_name
                payload = json.dumps(pkt, default=datetime_serializer)
                
                try:
                    if mqtt_online:
                        info = mqttc.publish(topic, payload, qos=QOS, retain=False)
                        if info.rc != mqtt.MQTT_ERR_SUCCESS:
                            logger.warning(f"Publish rc={info.rc}; enqueue offline")
                            offline_queue.enqueue(topic, payload, qos=QOS, retain=False)
                    else:
                        offline_queue.enqueue(topic, payload, qos=QOS, retain=False)
                except Exception as e:
                    logger.warning(f"Publish exception: {e}; enqueue offline")
                    offline_queue.enqueue(topic, payload, qos=QOS, retain=False)

                # Opportunistic flush
                if mqtt_online and offline_queue.size() > 0:
                    flush_offline_queue(mqttc, batch_size=200)

                logger.info(f"Cycle OK: keys={len(pkt)} offline_q={offline_queue.size()}")

            else:
                # No packet yet or USB read errors
                age = time.time() - pkt_ts if pkt_ts else None
                if age is None or age > max(5.0, float(config_data.get("delay", 5)) * 2):
                    logger.warning("No USB data available (station read pending or failing)")

            time.sleep(float(config_data["delay"]))

    except KeyboardInterrupt:
        logger.info("Shutdown requested (CTRL+C)")

    finally:
        try:
            usb_thread.stop()
        except Exception:
            pass

        try:
            mqttc.disconnect()
        except Exception:
            pass
        try:
            mqttc.loop_stop()
        except Exception:
            pass

        logger.info("Stopped")


if __name__ == "__main__":
    main()
