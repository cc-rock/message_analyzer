#!/usr/bin/env python3
"""Generate a synthetic WhatsApp iOS chat export for testing."""

from __future__ import annotations

import argparse
import random
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path


SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WHITESPACE_RE = re.compile(r"\s+")
CHAT_FILENAME = "_chat.txt"

WORD_BANK = [
    "alpha",
    "amber",
    "anchor",
    "apple",
    "april",
    "arch",
    "artist",
    "atlas",
    "autumn",
    "bamboo",
    "beacon",
    "berry",
    "bicycle",
    "blossom",
    "blue",
    "bridge",
    "brook",
    "candle",
    "canyon",
    "canvas",
    "cedar",
    "circle",
    "cloud",
    "cobalt",
    "comet",
    "coral",
    "cricket",
    "crystal",
    "daisy",
    "dawn",
    "delta",
    "desert",
    "drift",
    "echo",
    "ember",
    "engine",
    "falcon",
    "feather",
    "field",
    "flame",
    "forest",
    "frost",
    "garden",
    "glacier",
    "glow",
    "gold",
    "granite",
    "harbor",
    "hazel",
    "helium",
    "horizon",
    "island",
    "jade",
    "jasmine",
    "journey",
    "juniper",
    "lagoon",
    "lantern",
    "leaf",
    "linen",
    "maple",
    "marble",
    "meadow",
    "mercury",
    "meteor",
    "mint",
    "mist",
    "morning",
    "mountain",
    "nebula",
    "nickel",
    "oasis",
    "ocean",
    "olive",
    "opal",
    "orchid",
    "paper",
    "pebble",
    "pine",
    "planet",
    "plaza",
    "prairie",
    "quartz",
    "rain",
    "raven",
    "river",
    "saffron",
    "sage",
    "shadow",
    "silver",
    "sky",
    "snow",
    "solar",
    "spring",
    "stone",
    "summer",
    "sunset",
    "thunder",
    "valley",
    "violet",
    "willow",
    "winter",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic WhatsApp iOS exported chat zip for testing."
    )
    parser.add_argument(
        "--my-name",
        required=True,
        help="Name of the WhatsApp account owner.",
    )
    parser.add_argument(
        "--contact-name",
        required=True,
        help="Name of the other chat participant.",
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
        "--message-count",
        required=True,
        type=int,
        help="Number of messages to generate.",
    )
    parser.add_argument(
        "--max-length",
        required=True,
        type=int,
        help="Maximum number of characters for each generated message text.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory where the zip file will be written. Defaults to the current directory.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Optional random seed for reproducible output.",
    )
    return parser.parse_args()


def parse_cli_date(value: str, label: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid {label} '{value}'. Expected YYYY-MM-DD.") from exc


def sanitize_zip_component(value: str) -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", value.strip())
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.rstrip(". ")
    return cleaned or "contact"


def build_zip_path(output_dir: Path, contact_name: str) -> Path:
    safe_contact_name = sanitize_zip_component(contact_name)
    zip_name = f"WhatsApp Chat - {safe_contact_name}.zip"
    return output_dir / zip_name


def generate_timestamps(
    start_dt: datetime,
    end_dt: datetime,
    message_count: int,
    rng: random.Random,
) -> list[datetime]:
    if message_count == 0:
        return []

    end_inclusive = end_dt + timedelta(days=1) - timedelta(seconds=1)
    total_seconds = int((end_inclusive - start_dt).total_seconds())
    if total_seconds < 0:
        raise ValueError("Start date must be less than or equal to end date.")

    timestamps = [
        start_dt + timedelta(seconds=rng.randint(0, total_seconds))
        for _ in range(message_count)
    ]
    timestamps.sort()
    return timestamps


def build_random_message(max_length: int, rng: random.Random) -> str:
    words: list[str] = []
    current_length = 0
    target_length = rng.randint(1, max_length)

    while True:
        word = rng.choice(WORD_BANK)
        addition = word if not words else f" {word}"
        if current_length + len(addition) > target_length:
            break
        words.append(word)
        current_length += len(addition)

    if not words:
        fallback_word = min(WORD_BANK, key=len)
        return fallback_word[:max_length]

    message = " ".join(words)

    # Occasionally split long enough messages across multiple lines to mimic
    # exported chat entries whose text continues on following lines.
    if len(message) >= 12 and rng.random() < 0.28:
        split_count = rng.randint(1, min(3, len(words) - 1))
        split_positions = sorted(rng.sample(range(1, len(words)), split_count))
        parts: list[str] = []
        start_index = 0
        for split_index in split_positions:
            parts.append(" ".join(words[start_index:split_index]))
            start_index = split_index
        parts.append(" ".join(words[start_index:]))
        multiline_message = "\n".join(part for part in parts if part)
        if len(multiline_message) <= max_length:
            return multiline_message

    return message


def build_chat_text(
    my_name: str,
    contact_name: str,
    timestamps: list[datetime],
    max_length: int,
    rng: random.Random,
) -> str:
    if not timestamps:
        return ""

    senders = [my_name.strip(), contact_name.strip()]
    first_sender_index = rng.randint(0, 1)
    lines: list[str] = []

    for index, timestamp in enumerate(timestamps):
        sender = senders[(first_sender_index + index) % 2]
        message = build_random_message(max_length=max_length, rng=rng)
        header = f"[{timestamp.strftime('%d/%m/%y, %H:%M:%S')}] {sender}: "
        lines.append(f"{header}{message}")

    return "\n".join(lines) + "\n"


def write_chat_zip(zip_path: Path, chat_text: str) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(CHAT_FILENAME, chat_text)


def validate_args(args: argparse.Namespace) -> tuple[datetime, datetime, Path]:
    start_dt = parse_cli_date(args.start_date, "start date")
    end_dt = parse_cli_date(args.end_date, "end date")
    if start_dt > end_dt:
        raise ValueError("Start date must be less than or equal to end date.")
    if args.message_count < 0:
        raise ValueError("Message count must be greater than or equal to 0.")
    if args.max_length < 1:
        raise ValueError("Max length must be greater than or equal to 1.")
    if not args.my_name.strip():
        raise ValueError("My name must not be empty.")
    if not args.contact_name.strip():
        raise ValueError("Contact name must not be empty.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return start_dt, end_dt, output_dir


def main() -> int:
    args = parse_args()

    try:
        start_dt, end_dt, output_dir = validate_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    timestamps = generate_timestamps(
        start_dt=start_dt,
        end_dt=end_dt,
        message_count=args.message_count,
        rng=rng,
    )
    chat_text = build_chat_text(
        my_name=args.my_name,
        contact_name=args.contact_name,
        timestamps=timestamps,
        max_length=args.max_length,
        rng=rng,
    )
    zip_path = build_zip_path(output_dir=output_dir, contact_name=args.contact_name)
    write_chat_zip(zip_path=zip_path, chat_text=chat_text)
    print(f"Wrote {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
