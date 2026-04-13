#!/usr/bin/env python3
"""Extract WhatsApp chats from iOS zip files with optional full-chat export."""

from __future__ import annotations

import argparse
import csv
import holidays
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WHITESPACE_RE = re.compile(r"\s+")
CHAT_ENTRY_RE = re.compile(
    r"^\[(\d{2}/\d{2}/\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+):\s?(.*)$"
)
ZIP_NAME_RE = re.compile(r"^WhatsApp Chat - (.+)\.zip$")
FRENCH_HOLIDAYS = holidays.FR()


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
    archives_scanned: int = 0
    chats_written: int = 0
    scanned: int = 0
    parsed: int = 0
    extracted: int = 0
    skipped_date: int = 0
    skipped_time: int = 0
    skipped_parse: int = 0
    skipped_archives: int = 0


@dataclass(frozen=True)
class ChatEntry:
    message_dt: datetime
    sender_name: str
    raw_text: str


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
        description=(
            "Export filtered WhatsApp iOS chat files by default, or full chats with "
            "--export-full-chats, while keeping filtered sent-message rows in "
            "wa_summary.csv."
        )
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing WhatsApp iOS export zip files.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where WhatsApp chat files and wa_summary.csv are written.",
    )
    parser.add_argument(
        "--export-full-chats",
        action="store_true",
        help=(
            "Write the entire _chat.txt content for each archive. When omitted, exported "
            "chat files keep the previous filtered behavior."
        ),
    )
    parser.add_argument(
        "--from-name",
        required=True,
        help="Treat messages from this sender name as sent messages.",
    )
    parser.add_argument(
        "--time-ranges",
        required=True,
        help=(
            "Comma-separated ranges like 07:00-13:00,15:00-18:00,21:00-23:30. "
            "Ignored on non-working days unless --use-time-ranges-for-non-working-days is set."
        ),
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="Inclusive end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--use-time-ranges-for-non-working-days",
        action="store_true",
        help=(
            "Apply time-range filtering on French public holidays and weekends instead of "
            "exporting all sent messages for those dates."
        ),
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


def parse_cli_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid {label} '{value}'. Expected YYYY-MM-DD.") from exc


def message_in_ranges(message_dt: datetime, ranges: Iterable[TimeRange]) -> bool:
    minute_of_day = message_dt.hour * 60 + message_dt.minute
    return any(time_range.contains(minute_of_day) for time_range in ranges)


def is_non_working_day(day_date: date) -> bool:
    return day_date in FRENCH_HOLIDAYS or day_date.weekday() >= 5


def parse_chat_datetime(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(f"{date_text}, {time_text}", "%d/%m/%y, %H:%M:%S")


def parse_chat_entries(text: str, stats: Stats) -> list[ChatEntry]:
    entries: list[ChatEntry] = []
    current_entry: ChatEntry | None = None

    for line in text.splitlines(keepends=True):
        match = CHAT_ENTRY_RE.match(line)
        if match:
            stats.scanned += 1
            try:
                message_dt = parse_chat_datetime(match.group(1), match.group(2))
            except ValueError:
                stats.skipped_parse += 1
                current_entry = None
                continue

            current_entry = ChatEntry(
                message_dt=message_dt,
                sender_name=match.group(3).strip(),
                raw_text=line,
            )
            entries.append(current_entry)
            stats.parsed += 1
            continue

        if line.startswith("["):
            stats.scanned += 1
            stats.skipped_parse += 1
            current_entry = None
            continue

        if current_entry is not None:
            updated_entry = ChatEntry(
                message_dt=current_entry.message_dt,
                sender_name=current_entry.sender_name,
                raw_text=current_entry.raw_text + line,
            )
            entries[-1] = updated_entry
            current_entry = updated_entry

    return entries


def extract_chat_text(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as archive:
        candidate_names = [
            info.filename
            for info in archive.infolist()
            if not info.is_dir() and Path(info.filename).name == "_chat.txt"
        ]
        if not candidate_names:
            raise FileNotFoundError("Archive does not contain _chat.txt.")

        chat_name = candidate_names[0]
        with archive.open(chat_name) as handle:
            return handle.read().decode("utf-8-sig")


def extract_name_from_zip_filename(zip_path: Path) -> str | None:
    match = ZIP_NAME_RE.match(zip_path.name)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def find_first_counterpart(entries: Iterable[ChatEntry], from_name: str) -> str | None:
    for entry in entries:
        if entry.sender_name.strip() != from_name:
            return entry.sender_name.strip()
    return None


def sanitize_filename_component(value: str) -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", value.strip())
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.rstrip(". ")
    return cleaned or "name"


def build_output_path(whatsapp_dir: Path, recipient_name: str) -> Path:
    safe_name = sanitize_filename_component(recipient_name)
    stem = f"Whatsapp_chat_{safe_name}"
    candidate = whatsapp_dir / f"{stem}.txt"
    suffix = 1
    while candidate.exists():
        candidate = whatsapp_dir / f"{stem}_{suffix}.txt"
        suffix += 1
    return candidate


def build_summary_row(
    message_dt: datetime,
    sender_name: str,
    recipient_name: str,
    relative_file_path: Path,
) -> dict[str, str]:
    return {
        "type": "whatsapp",
        "timestamp": message_dt.isoformat(),
        "From address": sender_name,
        "from name": sender_name,
        "To address": recipient_name,
        "To name": recipient_name,
        "file": relative_file_path.as_posix(),
    }


def write_summary_csv(output_dir: Path, summary_rows: list[dict[str, str]]) -> None:
    summary_path = output_dir / "wa_summary.csv"
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


def print_summary(stats: Stats) -> None:
    print("Summary:")
    print(f"  Archives scanned: {stats.archives_scanned}")
    print(f"  Chats written: {stats.chats_written}")
    print(f"  Messages scanned: {stats.scanned}")
    print(f"  Messages parsed: {stats.parsed}")
    print(f"  Extracted: {stats.extracted}")
    print(f"  Skipped (date mismatch): {stats.skipped_date}")
    print(f"  Skipped (time mismatch): {stats.skipped_time}")
    print(f"  Skipped (parse failure): {stats.skipped_parse}")
    print(f"  Skipped archives: {stats.skipped_archives}")


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    whatsapp_dir = output_dir / "whatsapp"
    whatsapp_dir.mkdir(parents=True, exist_ok=True)

    try:
        time_ranges = parse_time_ranges(args.time_ranges)
        start_date = parse_cli_date(args.start_date, "start date")
        end_date = parse_cli_date(args.end_date, "end date")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if start_date > end_date:
        print("Start date must be less than or equal to end date.", file=sys.stderr)
        return 1

    zip_paths = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix == ".zip")
    total_bytes = sum(path.stat().st_size for path in zip_paths)
    reporter = ProgressReporter(total_bytes=total_bytes, enabled=not args.no_progress)
    stats = Stats()
    summary_rows: list[dict[str, str]] = []
    fallback_name_counter = 1
    bytes_read = 0
    from_name = args.from_name.strip()

    try:
        for zip_path in zip_paths:
            stats.archives_scanned += 1
            try:
                chat_text = extract_chat_text(zip_path)
            except (FileNotFoundError, OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
                stats.skipped_archives += 1
                if args.verbose:
                    reporter.print_message(f"Skipping archive {zip_path.name}: {exc}")
                bytes_read += zip_path.stat().st_size
                reporter.update(bytes_read=bytes_read, scanned=stats.scanned, extracted=stats.extracted)
                continue

            entries = parse_chat_entries(chat_text, stats)
            recipient_name = extract_name_from_zip_filename(zip_path)
            if recipient_name is None:
                recipient_name = find_first_counterpart(entries, from_name)
            if recipient_name is None:
                recipient_name = f"name{fallback_name_counter}"
                fallback_name_counter += 1

            output_path = build_output_path(whatsapp_dir, recipient_name)
            relative_output_path = output_path.relative_to(output_dir)
            exported_chunks: list[str] = []

            for entry in entries:
                message_date = entry.message_dt.date()
                if message_date < start_date or message_date > end_date:
                    stats.skipped_date += 1
                    if args.verbose:
                        reporter.print_message(
                            f"Skipping message from {zip_path.name} at "
                            f"{entry.message_dt.strftime('%Y-%m-%d %H:%M:%S')}: date outside range."
                        )
                    continue

                enforce_time_ranges = (
                    args.use_time_ranges_for_non_working_days
                    or not is_non_working_day(message_date)
                )
                if enforce_time_ranges and not message_in_ranges(entry.message_dt, time_ranges):
                    stats.skipped_time += 1
                    if args.verbose:
                        reporter.print_message(
                            f"Skipping message from {zip_path.name} at "
                            f"{entry.message_dt.strftime('%Y-%m-%d %H:%M:%S')}: time outside allowed ranges."
                        )
                    continue

                if not args.export_full_chats:
                    exported_chunks.append(entry.raw_text)
                    stats.extracted += 1

                if entry.sender_name.strip() == from_name:
                    summary_rows.append(
                        build_summary_row(
                            message_dt=entry.message_dt,
                            sender_name=entry.sender_name.strip(),
                            recipient_name=recipient_name,
                            relative_file_path=relative_output_path,
                        )
                    )

            if args.export_full_chats:
                exported_chunks = [entry.raw_text for entry in entries]
                stats.extracted += len(entries)

            output_path.write_text("".join(exported_chunks), encoding="utf-8", newline="")
            stats.chats_written += 1
            if args.verbose:
                reporter.print_message(f"Wrote {relative_output_path.as_posix()}")

            bytes_read += zip_path.stat().st_size
            reporter.update(bytes_read=bytes_read, scanned=stats.scanned, extracted=stats.extracted)
    finally:
        reporter.finish()

    write_summary_csv(output_dir, summary_rows)
    print_summary(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
