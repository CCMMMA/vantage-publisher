#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from pyvantagepro import VantagePro2


LOGGER = logging.getLogger("collect_history")


def parse_datetime(value: str) -> datetime:
    txt = value.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(txt)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime '{value}'. Use ISO format, e.g. 2026-03-08T10:30:00"
        ) from exc


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect archive history from Vantage Pro2 (SI-converted JSON rows)."
    )
    parser.add_argument(
        "--url",
        default="tcp:127.0.0.1:22222",
        help="Station URL (default: tcp:127.0.0.1:22222)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connection timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--start",
        type=parse_datetime,
        default=datetime(2009, 1, 1, 1, 1, 1),
        help="Archive start datetime in ISO format",
    )
    parser.add_argument(
        "--stop",
        type=parse_datetime,
        default=None,
        help="Archive stop datetime in ISO format (default: now)",
    )
    parser.add_argument(
        "--parameters",
        default="parameters.json",
        help="Path to parameters JSON map (default: parameters.json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="CSV output file path. If omitted, rows are logged to console.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (default: INFO)",
    )
    return parser.parse_args()


def load_parameters(path: str):
    p = Path(path)
    if not p.exists():
        LOGGER.warning("Parameters file not found (%s): all fields enabled", p)
        return None
    with p.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise SystemExit("Parameters file must contain a JSON object")
    return {str(k): bool(v) for k, v in data.items()}


def filter_rows(rows, parameters_map):
    if parameters_map is None:
        return rows
    out = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if key == "Datetime" or parameters_map.get(key, False):
                item[key] = value
        out.append(item)
    return out


def collect_rows(url: str, timeout: float, start_date: datetime, stop_date):
    device = VantagePro2.from_url(url, timeout=timeout)
    try:
        begin = time.time()
        # get_archives_as_json returns normalized SI-friendly rows from pyvantagepro
        rows = device.get_archives_as_json(start_date=start_date, stop_date=stop_date)
        elapsed = time.time() - begin
        LOGGER.info("Collected %d archive rows in %.3fs", len(rows), elapsed)
        return rows
    finally:
        device.close()


def write_csv(path: str, rows):
    if not rows:
        LOGGER.info("No rows to write.")
        return

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with output.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    LOGGER.info("Wrote %d rows to %s", len(rows), output)


def log_rows(rows):
    for row in rows:
        LOGGER.info("ROW;%s", json.dumps(row, separators=(",", ":"), default=str))


def main():
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    if args.stop is not None and args.stop < args.start:
        raise SystemExit("--stop must be greater than or equal to --start")

    parameters_map = load_parameters(args.parameters)
    rows = collect_rows(args.url, args.timeout, args.start, args.stop)
    rows = filter_rows(rows, parameters_map)

    if args.output:
        write_csv(args.output, rows)
    else:
        log_rows(rows)


if __name__ == "__main__":
    main()
