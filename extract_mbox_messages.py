#!/usr/bin/env python3
"""Stream a large mbox file and extract matching messages as .eml files."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Iterable


SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class TimeRange:
    start_minute: int
    end_minute: int
    wraps_midnight: bool

    def contains(self, minute_of_day: int) -> bool:
        if self.wraps_midnight:
            return minute_of_day >= self.start_minute or minute_of_day <= self.end_minute
        return self.start_minute <= minute_of_day <= self.end_minute


@dataclass
class Stats:
    scanned: int = 0
    parsed: int = 0
    extracted: int = 0
    skipped_sender: int = 0
    skipped_date: int = 0
    skipped_time: int = 0
    skipped_parse: int = 0


@dataclass(frozen=True)
class AddressInfo:
    name: str
    address: str


class ProgressReporter:
    def __init__(self, total_bytes: int, enabled: bool) -> None:
        self.total_bytes = max(total_bytes, 1)
        self.enabled = enabled
        self.is_tty = enabled and sys.stdout.isatty()
        self.last_render = ""
        self.last_plain_percent = -1

    def update(self, bytes_read: int, scanned: int, extracted: int) -> None:
        if not self.enabled:
            return

        percent = min(100.0, (bytes_read / self.total_bytes) * 100.0)
        line = f"{percent:6.2f}% | scanned {scanned} | extracted {extracted}"

        if self.is_tty:
            padding = ""
            if len(self.last_render) > len(line):
                padding = " " * (len(self.last_render) - len(line))
            sys.stdout.write(f"\r{line}{padding}")
            sys.stdout.flush()
            self.last_render = line
            return

        rounded = int(percent)
        if rounded != self.last_plain_percent:
            print(line, flush=True)
            self.last_plain_percent = rounded

    def print_message(self, message: str) -> None:
        if not self.enabled:
            print(message)
            return

        if self.is_tty and self.last_render:
            sys.stdout.write("\r" + (" " * len(self.last_render)) + "\r")
            sys.stdout.flush()
        print(message)
        if self.is_tty:
            self.last_render = ""

    def finish(self) -> None:
        if self.enabled and self.is_tty and self.last_render:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.last_render = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract filtered messages from a large mbox file into .eml files."
    )
    parser.add_argument("--mbox", required=True, help="Path to the input .mbox file.")
    parser.add_argument(
        "--output-dir", required=True, help="Directory where matching .eml files are written."
    )
    parser.add_argument(
        "--from-address",
        required=True,
        help="Extract only messages whose parsed From address matches this value.",
    )
    parser.add_argument(
        "--time-ranges",
        required=True,
        help="Comma-separated ranges like 07:00-13:00,15:00-18:00,21:00-23:30.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed skip/write information while processing.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress reporting.",
    )
    parser.add_argument(
        "--encoding-errors",
        default="replace",
        choices=("strict", "ignore", "replace"),
        help="How to decode malformed header bytes surfaced by the email parser.",
    )
    return parser.parse_args()


def parse_hhmm(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError(f"Invalid time '{value}'. Expected HH:MM.") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time '{value}'. Hour must be 00-23 and minute 00-59.")

    return hour * 60 + minute


def parse_time_ranges(value: str) -> list[TimeRange]:
    ranges: list[TimeRange] = []
    for raw_range in value.split(","):
        chunk = raw_range.strip()
        if not chunk:
            continue
        try:
            start_text, end_text = chunk.split("-")
        except ValueError as exc:
            raise ValueError(f"Invalid range '{chunk}'. Expected HH:MM-HH:MM.") from exc

        start_minute = parse_hhmm(start_text.strip())
        end_minute = parse_hhmm(end_text.strip())
        ranges.append(
            TimeRange(
                start_minute=start_minute,
                end_minute=end_minute,
                wraps_midnight=end_minute < start_minute,
            )
        )

    if not ranges:
        raise ValueError("At least one time range is required.")

    return ranges


def message_in_ranges(message_dt: datetime, ranges: Iterable[TimeRange]) -> bool:
    minute_of_day = message_dt.hour * 60 + message_dt.minute
    return any(time_range.contains(minute_of_day) for time_range in ranges)


def extract_first_address(message, header_name: str) -> AddressInfo | None:
    addresses = getaddresses(message.get_all(header_name, []))
    if not addresses:
        return None
    display_name, address = addresses[0]
    normalized_address = address.strip()
    if not normalized_address:
        return None
    return AddressInfo(name=display_name.strip(), address=normalized_address)


def recipient_slug(recipient: AddressInfo | None) -> str:
    if recipient is None:
        return "unknown-recipient"

    base = recipient.name or recipient.address.split("@", 1)[0].strip() or "unknown-recipient"
    return slugify(base)


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "unknown-recipient"


def build_output_path(output_dir: Path, message_dt: datetime, recipient_slug: str) -> Path:
    stem = f"{message_dt.strftime('%Y-%m-%d_%H-%M-%S')}_{recipient_slug}"
    candidate = output_dir / f"{stem}.eml"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{stem}_{suffix}.eml"
        suffix += 1
    return candidate


def parse_message_date(message) -> datetime | None:
    raw_date = message.get("date")
    if not raw_date:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def parse_message(raw_bytes: bytes, encoding_errors: str):
    parser_policy = policy.default.clone(utf8=True, raise_on_defect=False)
    parser = BytesParser(policy=parser_policy)
    message = parser.parsebytes(raw_bytes)
    if encoding_errors != "replace":
        # Force header materialization with the requested error handling.
        for key, value in list(message.items()):
            if isinstance(value, str):
                value.encode("utf-8", errors=encoding_errors)
    return message


def build_summary_row(
    message_dt: datetime,
    sender: AddressInfo | None,
    recipient: AddressInfo | None,
    relative_file_path: Path,
) -> dict[str, str]:
    return {
        "type": "email",
        "timestamp": message_dt.isoformat(),
        "From address": sender.address if sender else "",
        "from name": sender.name if sender else "",
        "To address": recipient.address if recipient else "",
        "To name": recipient.name if recipient else "",
        "file": relative_file_path.as_posix(),
    }


def write_summary_csv(output_dir: Path, summary_rows: list[dict[str, str]]) -> None:
    summary_path = output_dir / "summary.csv"
    fieldnames = [
        "type",
        "timestamp",
        "From address",
        "from name",
        "To address",
        "To name",
        "file",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)


def process_message(
    raw_bytes: bytes,
    output_dir: Path,
    email_dir: Path,
    from_address: str,
    time_ranges: list[TimeRange],
    encoding_errors: str,
    stats: Stats,
    reporter: ProgressReporter,
    verbose: bool,
) -> dict[str, str] | None:
    stats.scanned += 1
    try:
        message = parse_message(raw_bytes, encoding_errors)
    except Exception as exc:  # pragma: no cover - intentionally broad for robustness
        stats.skipped_parse += 1
        if verbose:
            reporter.print_message(f"Skipping message #{stats.scanned}: parse error: {exc}")
        return None

    stats.parsed += 1

    sender = extract_first_address(message, "from")
    sender_address = sender.address.lower() if sender else None
    if sender_address != from_address:
        stats.skipped_sender += 1
        if verbose:
            reporter.print_message(
                f"Skipping message #{stats.scanned}: sender '{sender_address or '<missing>'}' does not match."
            )
        return None

    message_dt = parse_message_date(message)
    if message_dt is None:
        stats.skipped_date += 1
        if verbose:
            reporter.print_message(f"Skipping message #{stats.scanned}: missing or invalid Date header.")
        return None

    if not message_in_ranges(message_dt, time_ranges):
        stats.skipped_time += 1
        if verbose:
            reporter.print_message(
                f"Skipping message #{stats.scanned}: time {message_dt.strftime('%H:%M:%S')} outside allowed ranges."
            )
        return None

    recipient = extract_first_address(message, "to")
    output_path = build_output_path(email_dir, message_dt, recipient_slug(recipient))
    output_path.write_bytes(raw_bytes)
    relative_output_path = output_path.relative_to(output_dir)
    stats.extracted += 1
    if verbose:
        reporter.print_message(f"Wrote {relative_output_path.as_posix()}")
    return build_summary_row(message_dt, sender, recipient, relative_output_path)


def iterate_mbox_messages(mbox_path: Path) -> Iterable[tuple[bytes, int]]:
    bytes_read = 0
    current = bytearray()
    in_message = False

    with mbox_path.open("rb") as handle:
        for line in handle:
            bytes_read += len(line)
            if line.startswith(b"From "):
                if in_message and current:
                    yield bytes(current), bytes_read - len(line)
                    current.clear()
                in_message = True
                continue

            if in_message:
                current.extend(line)

    if in_message and current:
        yield bytes(current), bytes_read


def print_summary(stats: Stats) -> None:
    print("Summary:")
    print(f"  Messages scanned: {stats.scanned}")
    print(f"  Messages parsed: {stats.parsed}")
    print(f"  Extracted: {stats.extracted}")
    print(f"  Skipped (sender mismatch): {stats.skipped_sender}")
    print(f"  Skipped (date parse failure): {stats.skipped_date}")
    print(f"  Skipped (time mismatch): {stats.skipped_time}")
    print(f"  Skipped (parse failure): {stats.skipped_parse}")


def main() -> int:
    args = parse_args()

    mbox_path = Path(args.mbox)
    if not mbox_path.is_file():
        print(f"Input mbox file not found: {mbox_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    email_dir = output_dir / "email"
    email_dir.mkdir(parents=True, exist_ok=True)

    try:
        time_ranges = parse_time_ranges(args.time_ranges)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    stats = Stats()
    summary_rows: list[dict[str, str]] = []
    total_bytes = mbox_path.stat().st_size
    reporter = ProgressReporter(total_bytes=total_bytes, enabled=not args.no_progress)
    normalized_sender = args.from_address.strip().lower()

    try:
        for raw_message, bytes_read in iterate_mbox_messages(mbox_path):
            summary_row = process_message(
                raw_bytes=raw_message,
                output_dir=output_dir,
                email_dir=email_dir,
                from_address=normalized_sender,
                time_ranges=time_ranges,
                encoding_errors=args.encoding_errors,
                stats=stats,
                reporter=reporter,
                verbose=args.verbose,
            )
            if summary_row is not None:
                summary_rows.append(summary_row)
            reporter.update(bytes_read=bytes_read, scanned=stats.scanned, extracted=stats.extracted)
    finally:
        reporter.finish()

    write_summary_csv(output_dir, summary_rows)
    print_summary(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
