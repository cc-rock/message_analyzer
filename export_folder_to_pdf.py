#!/usr/bin/env python3
"""Render supported report artifacts to PDFs while preserving folder structure."""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import mimetypes
import sys
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Iterable

import holidays


CHAT_ENTRY_RE = re.compile(
    r"^\[(\d{2}/\d{2}/\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+):\s?(.*)$"
)
FRENCH_HOLIDAYS = holidays.FR()
SUPPORTED_EXTENSIONS = {".eml", ".txt", ".csv"}
DEFAULT_TIME_RANGES = "00:00-23:59"
TEXT_FALLBACK_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")

BASE_STYLES = """
  :root {
    color-scheme: light;
    --text: #202124;
    --muted: #5f6368;
    --border: #dadce0;
    --surface: #ffffff;
    --surface-soft: #f8f9fa;
    --mine: #dcf8c6;
    --other: #ffffff;
    --table-head: #e8f0fe;
    --day-band: #e7f3ff;
    --range-band: #f1f3f4;
  }

  * { box-sizing: border-box; }
  body {
    color: var(--text);
    font-family: Arial, Helvetica, sans-serif;
    font-size: 12px;
    line-height: 1.45;
    margin: 0;
    padding: 0;
    background: #fff;
  }

  .page {
    padding: 24px 30px 30px;
  }

  .muted {
    color: var(--muted);
  }

  .preformatted {
    white-space: pre-wrap;
    word-break: break-word;
  }
"""

EMAIL_STYLES = """
  @page {
    margin: 16mm 14mm 18mm;
    size: A4 portrait;
  }

  .email-subject {
    font-size: 24px;
    font-weight: 400;
    margin: 0 0 12px;
  }

  .email-meta {
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 20px;
  }

  .email-meta-row {
    display: flex;
    gap: 14px;
    margin: 4px 0;
    align-items: baseline;
  }

  .email-meta-label {
    color: var(--muted);
    width: 72px;
    flex: 0 0 72px;
  }

  .email-meta-value {
    flex: 1 1 auto;
  }

  .email-date {
    text-align: right;
    color: var(--muted);
    font-size: 11px;
    margin-top: 10px;
  }

  .email-body img {
    max-width: 100%;
    height: auto;
  }

  .email-body table {
    width: auto;
    max-width: 100%;
    border-collapse: collapse;
  }

  .attachments {
    margin-top: 22px;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }

  .attachments h2 {
    font-size: 14px;
    margin: 0 0 8px;
  }

  .attachments ul {
    margin: 0;
    padding-left: 18px;
  }
"""

WHATSAPP_STYLES = """
  @page {
    margin: 12mm;
    size: A4 portrait;
  }

  body {
    background: #efeae2;
  }

  .page {
    padding: 18px;
  }

  .chat-shell {
    display: block;
  }

  .section-header {
    background: var(--day-band);
    border: 1px solid #c5d9f3;
    border-radius: 999px;
    color: #194264;
    font-size: 11px;
    margin: 0 0 12px;
    padding: 6px 12px;
    text-align: center;
    width: fit-content;
  }

  .message-row {
    display: block;
    margin: 0 0 10px;
    clear: both;
  }

  .message-row.mine .bubble {
    background: var(--mine);
    float: right;
  }

  .message-row.other .bubble {
    background: var(--other);
    float: left;
  }

  .bubble {
    border-radius: 12px;
    box-shadow: 0 1px 1px rgba(0, 0, 0, 0.08);
    max-width: 78%;
    min-width: 28%;
    padding: 8px 10px 7px;
  }

  .bubble-header {
    color: var(--muted);
    display: flex;
    font-size: 10px;
    justify-content: space-between;
    margin-bottom: 4px;
    gap: 10px;
  }

  .bubble-text {
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .section-spacer {
    clear: both;
    height: 8px;
  }
"""

CSV_STYLES = """
  @page {
    margin: 10mm;
    size: A4 landscape;
  }

  .table-wrapper {
    width: 100%;
  }

  table {
    border-collapse: collapse;
    table-layout: fixed;
    width: 100%;
  }

  thead {
    display: table-header-group;
  }

  th, td {
    border: 1px solid var(--border);
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
    overflow-wrap: anywhere;
    word-break: break-word;
    hyphens: auto;
  }

  th {
    background: var(--table-head);
    font-weight: 600;
  }

  tbody tr:nth-child(even) td {
    background: #fafafa;
  }
"""

EMAIL_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
EMAIL_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"alt", "src", "title"},
    "font": {"color"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "div": {"align"},
    "p": {"align"},
    "span": set(),
}
SELF_CLOSING_TAGS = {"br", "hr", "img"}


@dataclass(frozen=True)
class TimeRange:
    start_minute: int
    end_minute: int
    wraps_midnight: bool
    label: str

    def contains(self, minute_of_day: int) -> bool:
        if self.wraps_midnight:
            return minute_of_day >= self.start_minute or minute_of_day <= self.end_minute
        return self.start_minute <= minute_of_day <= self.end_minute


@dataclass(frozen=True)
class ChatEntry:
    message_dt: datetime
    sender_name: str
    message_text: str
    raw_text: str


@dataclass(frozen=True)
class WhatsAppSection:
    day_date: date
    day_label: str
    range_label: str | None
    entries: list[ChatEntry]


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content_type: str
    inline: bool


@dataclass(frozen=True)
class EmailRenderModel:
    subject: str
    from_text: str
    to_text: str
    cc_text: str
    reply_to_text: str
    date_text: str
    body_html: str
    attachments: list[EmailAttachment]


@dataclass
class ConversionStats:
    scanned: int = 0
    converted: int = 0
    skipped: int = 0
    failed: int = 0


class ProgressReporter:
    def __init__(self, total_files: int, enabled: bool) -> None:
        self.total_files = max(total_files, 1)
        self.enabled = enabled
        self.is_tty = enabled and sys.stdout.isatty()
        self.last_render = ""
        self.last_plain_percent = -1

    def update(self, processed_files: int, converted: int, failed: int) -> None:
        if not self.enabled:
            return

        percent = min(100.0, (processed_files / self.total_files) * 100.0)
        line = f"{percent:6.2f}% | processed {processed_files}/{self.total_files} | converted {converted} | failed {failed}"

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

    def finish(self) -> None:
        if self.enabled and self.is_tty and self.last_render:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.last_render = ""


class EmailHtmlSanitizer(HTMLParser):
    """Allow a conservative subset of tags and attributes for message HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in EMAIL_ALLOWED_TAGS:
            self.tag_stack.append("")
            return

        safe_attrs: list[str] = []
        allowed = EMAIL_ALLOWED_ATTRS.get(tag, set())
        for key, value in attrs:
            key = key.lower()
            if key.startswith("on") or key not in allowed:
                continue
            if value is None:
                continue
            safe_attrs.append(f' {key}="{html.escape(value, quote=True)}"')

        self.parts.append(f"<{tag}{''.join(safe_attrs)}>")
        if tag not in SELF_CLOSING_TAGS:
            self.tag_stack.append(tag)
        else:
            self.tag_stack.append("")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.tag_stack:
            return
        expected = self.tag_stack.pop()
        if expected == tag and tag not in SELF_CLOSING_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def get_html(self) -> str:
        while self.tag_stack:
            tag = self.tag_stack.pop()
            if tag:
                self.parts.append(f"</{tag}>")
        return "".join(self.parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively render supported report files to PDF."
    )
    parser.add_argument("--input-dir", required=True, help="Directory to scan recursively.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where mirrored PDF outputs are written.",
    )
    parser.add_argument(
        "--my-whatsapp-name",
        default="",
        help="Sender name that should render on the right side in WhatsApp PDFs.",
    )
    parser.add_argument(
        "--time-ranges",
        default=DEFAULT_TIME_RANGES,
        help="Comma-separated ranges like 07:00-13:00,15:00-18:00.",
    )
    parser.add_argument(
        "--use-time-ranges-for-non-working-days",
        action="store_true",
        help="Apply WhatsApp time ranges on weekends and French public holidays too.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing PDFs instead of skipping them.",
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
                label=f"{start_text.strip()}-{end_text.strip()}",
            )
        )

    if not ranges:
        raise ValueError("At least one time range is required.")

    return ranges


def is_non_working_day(day_date: date) -> bool:
    return day_date in FRENCH_HOLIDAYS or day_date.weekday() >= 5


def parse_chat_datetime(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(f"{date_text}, {time_text}", "%d/%m/%y, %H:%M:%S")


def extract_message_text(raw_text: str) -> str:
    lines = raw_text.splitlines()
    if not lines:
        return ""

    first_line = lines[0]
    match = CHAT_ENTRY_RE.match(first_line)
    if match:
        pieces = [match.group(4)]
        if len(lines) > 1:
            pieces.extend(lines[1:])
        return "\n".join(pieces).rstrip()
    return raw_text.rstrip()


def parse_chat_entries(text: str) -> list[ChatEntry]:
    entries: list[ChatEntry] = []
    current_raw_lines: list[str] = []
    current_dt: datetime | None = None
    current_sender = ""

    def flush_current() -> None:
        if current_dt is None:
            return
        raw_text = "".join(current_raw_lines)
        entries.append(
            ChatEntry(
                message_dt=current_dt,
                sender_name=current_sender,
                message_text=extract_message_text(raw_text),
                raw_text=raw_text,
            )
        )

    for line in text.splitlines(keepends=True):
        match = CHAT_ENTRY_RE.match(line)
        if match:
            flush_current()
            try:
                current_dt = parse_chat_datetime(match.group(1), match.group(2))
            except ValueError:
                current_dt = None
                current_raw_lines = []
                current_sender = ""
                continue
            current_sender = match.group(3).strip()
            current_raw_lines = [line]
            continue

        if line.startswith("["):
            flush_current()
            current_dt = None
            current_raw_lines = []
            current_sender = ""
            continue

        if current_dt is not None:
            current_raw_lines.append(line)

    flush_current()
    return entries


def group_whatsapp_sections(
    entries: list[ChatEntry],
    time_ranges: list[TimeRange],
    use_time_ranges_for_non_working_days: bool,
) -> list[WhatsAppSection]:
    entries_by_day: dict[date, list[ChatEntry]] = {}
    for entry in entries:
        entries_by_day.setdefault(entry.message_dt.date(), []).append(entry)

    sections: list[WhatsAppSection] = []
    for day_date in sorted(entries_by_day):
        day_entries = entries_by_day[day_date]
        day_label = day_date.strftime("%A %d %B %Y")
        enforce_ranges = use_time_ranges_for_non_working_days or not is_non_working_day(day_date)

        if enforce_ranges:
            for time_range in time_ranges:
                range_entries = [
                    entry
                    for entry in day_entries
                    if time_range.contains(entry.message_dt.hour * 60 + entry.message_dt.minute)
                ]
                if range_entries:
                    sections.append(
                        WhatsAppSection(
                            day_date=day_date,
                            day_label=day_label,
                            range_label=time_range.label,
                            entries=range_entries,
                        )
                    )
            continue

        sections.append(
            WhatsAppSection(
                day_date=day_date,
                day_label=day_label,
                range_label=None,
                entries=day_entries,
            )
        )

    return sections


def iter_supported_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def build_output_path(input_dir: Path, output_dir: Path, source_path: Path) -> Path:
    relative_path = source_path.relative_to(input_dir)
    return output_dir / relative_path.with_suffix(".pdf")


def format_address_list(message: Message, header_name: str) -> str:
    addresses = getaddresses(message.get_all(header_name, []))
    formatted: list[str] = []
    for display_name, address in addresses:
        display_name = display_name.strip()
        address = address.strip()
        if display_name and address:
            formatted.append(f"{display_name} <{address}>")
        elif address:
            formatted.append(address)
        elif display_name:
            formatted.append(display_name)
    return ", ".join(formatted)


def format_email_date(message: Message) -> str:
    raw_date = message.get("date", "")
    if not raw_date:
        return ""

    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError, OverflowError):
        return raw_date

    if parsed is None:
        return raw_date
    return parsed.strftime("%a, %d %b %Y at %H:%M:%S %z")


def content_id_variants(content_id: str | None) -> set[str]:
    if not content_id:
        return set()
    normalized = content_id.strip()
    if not normalized:
        return set()
    stripped = normalized.strip("<>")
    return {
        normalized,
        stripped,
        f"<{stripped}>",
        f"cid:{stripped}",
        f"cid:{normalized}",
    }


def part_to_data_uri(part: Message) -> str | None:
    payload = part.get_payload(decode=True)
    if payload is None:
        return None

    content_type = part.get_content_type() or "application/octet-stream"
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def replace_cid_sources(html_body: str, cid_map: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix, quote, value, suffix = match.groups()
        replacement = cid_map.get(value) or cid_map.get(value.strip("<>"))
        if replacement is None and value.startswith("cid:"):
            replacement = cid_map.get(value[4:])
        if replacement is None:
            return match.group(0)
        return f"{prefix}{quote}{replacement}{quote}{suffix}"

    pattern = re.compile(r'(<img\b[^>]*\bsrc\s*=\s*)(["\'])([^"\']+)(\2[^>]*>)', re.IGNORECASE)
    return pattern.sub(replace, html_body)


def sanitize_email_html(html_body: str) -> str:
    sanitizer = EmailHtmlSanitizer()
    sanitizer.feed(html_body)
    sanitizer.close()
    return sanitizer.get_html()


def pick_email_body(message: EmailMessage) -> tuple[str, bool]:
    html_part = message.get_body(preferencelist=("html",))
    if html_part is not None:
        try:
            return html_part.get_content(), True
        except LookupError:
            return html_part.get_content(errors="replace"), True

    text_part = message.get_body(preferencelist=("plain",))
    if text_part is not None:
        try:
            return text_part.get_content(), False
        except LookupError:
            return text_part.get_content(errors="replace"), False

    try:
        raw_content = message.get_content()
    except LookupError:
        raw_content = message.get_content(errors="replace")

    if isinstance(raw_content, str):
        return raw_content, False
    return "", False


def build_email_model(source_path: Path) -> EmailRenderModel:
    with source_path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    body_content, is_html = pick_email_body(message)
    cid_map: dict[str, str] = {}
    attachments: list[EmailAttachment] = []

    for part in message.walk():
        if part.is_multipart():
            continue

        filename = part.get_filename()
        content_type = part.get_content_type()
        content_disposition = (part.get_content_disposition() or "").lower()
        content_id = part.get("Content-ID")
        data_uri = part_to_data_uri(part)
        if data_uri is not None and content_id:
            for key in content_id_variants(content_id):
                cid_map[key] = data_uri

        if filename:
            inline = content_disposition == "inline" or bool(content_id)
            attachments.append(
                EmailAttachment(
                    filename=filename,
                    content_type=content_type,
                    inline=inline,
                )
            )

    if is_html:
        body_html = sanitize_email_html(replace_cid_sources(body_content, cid_map))
    else:
        body_html = f'<div class="preformatted">{html.escape(body_content)}</div>'

    return EmailRenderModel(
        subject=(message.get("subject") or "(No subject)").strip() or "(No subject)",
        from_text=format_address_list(message, "from"),
        to_text=format_address_list(message, "to"),
        cc_text=format_address_list(message, "cc"),
        reply_to_text=format_address_list(message, "reply-to"),
        date_text=format_email_date(message),
        body_html=body_html,
        attachments=attachments,
    )


def render_email_html(model: EmailRenderModel) -> str:
    attachment_html = ""
    listed_attachments = [
        attachment
        for attachment in model.attachments
        if not attachment.inline
    ]
    if listed_attachments:
        rows = "".join(
            (
                "<li>"
                f"{html.escape(attachment.filename)}"
                f" <span class=\"muted\">({html.escape(attachment.content_type)})</span>"
                "</li>"
            )
            for attachment in listed_attachments
        )
        attachment_html = (
            '<section class="attachments"><h2>Attachments</h2><ul>'
            f"{rows}</ul></section>"
        )

    meta_rows = []
    for label, value in (
        ("From", model.from_text),
        ("To", model.to_text),
        ("Cc", model.cc_text),
        ("Reply-To", model.reply_to_text),
    ):
        if value:
            meta_rows.append(
                "<div class=\"email-meta-row\">"
                f"<div class=\"email-meta-label\">{html.escape(label)}</div>"
                f"<div class=\"email-meta-value\">{html.escape(value)}</div>"
                "</div>"
            )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>{BASE_STYLES}{EMAIL_STYLES}</style>
  </head>
  <body>
    <main class="page">
      <h1 class="email-subject">{html.escape(model.subject)}</h1>
      <section class="email-meta">
        {''.join(meta_rows)}
        <div class="email-date">{html.escape(model.date_text)}</div>
      </section>
      <section class="email-body">{model.body_html}</section>
      {attachment_html}
    </main>
  </body>
</html>"""


def strip_html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", "", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def build_email_plaintext_fallback_html(source_path: Path) -> str:
    with source_path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    body_content, is_html = pick_email_body(message)
    body_text = strip_html_to_text(body_content) if is_html else body_content.strip()
    attachments: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if not filename:
            continue
        content_type = part.get_content_type()
        content_disposition = (part.get_content_disposition() or "").lower()
        content_id = part.get("Content-ID")
        inline = content_disposition == "inline" or bool(content_id)
        if not inline:
            attachments.append(f"- {filename} ({content_type})")

    subject = (message.get("subject") or "(No subject)").strip() or "(No subject)"
    from_text = format_address_list(message, "from")
    to_text = format_address_list(message, "to")
    cc_text = format_address_list(message, "cc")
    reply_to_text = format_address_list(message, "reply-to")
    date_text = format_email_date(message)

    meta_lines = [
        f"From: {from_text}" if from_text else "",
        f"To: {to_text}" if to_text else "",
        f"Cc: {cc_text}" if cc_text else "",
        f"Reply-To: {reply_to_text}" if reply_to_text else "",
        f"Date: {date_text}" if date_text else "",
    ]
    attachment_block = ""
    if attachments:
        attachment_block = "\n\nAttachments:\n" + "\n".join(attachments)

    content = (
        "\n".join(line for line in meta_lines if line)
        + "\n\n"
        + (body_text or "(No readable body content)")
        + attachment_block
    ).strip()
    return render_plaintext_fallback_html(subject, content)


def render_whatsapp_html(
    entries: list[ChatEntry],
    time_ranges: list[TimeRange],
    my_whatsapp_name: str,
    use_time_ranges_for_non_working_days: bool,
) -> str:
    sections = group_whatsapp_sections(
        entries=entries,
        time_ranges=time_ranges,
        use_time_ranges_for_non_working_days=use_time_ranges_for_non_working_days,
    )

    if not sections:
        return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>{BASE_STYLES}{WHATSAPP_STYLES}</style>
  </head>
  <body>
    <main class="page">
      <div class="section-header">No parsable WhatsApp messages found in this file.</div>
    </main>
  </body>
</html>"""

    parts: list[str] = []
    normalized_my_name = my_whatsapp_name.strip()
    for section in sections:
        header_text = section.day_label
        if section.range_label:
            header_text = f"{header_text} | {section.range_label}"
        parts.append(f'<div class="section-header">{html.escape(header_text)}</div>')

        for entry in section.entries:
            role = "mine" if normalized_my_name and entry.sender_name == normalized_my_name else "other"
            timestamp_text = entry.message_dt.strftime("%H:%M:%S")
            parts.append(
                "<div class=\"message-row {role}\">"
                "<div class=\"bubble\">"
                "<div class=\"bubble-header\">"
                f"<span>{html.escape(entry.sender_name)}</span>"
                f"<span>{html.escape(timestamp_text)}</span>"
                "</div>"
                f"<div class=\"bubble-text\">{html.escape(entry.message_text)}</div>"
                "</div>"
                "</div>"
                "<div class=\"section-spacer\"></div>".format(role=role)
            )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>{BASE_STYLES}{WHATSAPP_STYLES}</style>
  </head>
  <body>
    <main class="page">
      <section class="chat-shell">
        {''.join(parts)}
      </section>
    </main>
  </body>
</html>"""


def normalize_csv_rows(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def render_csv_html(rows: list[list[str]]) -> str:
    normalized = normalize_csv_rows(rows)
    if not normalized:
        normalized = [["No data available"]]

    header = normalized[0]
    body = normalized[1:]
    header_html = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in body
    )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>{BASE_STYLES}{CSV_STYLES}</style>
  </head>
  <body>
    <main class="page">
      <div class="table-wrapper">
        <table>
          <thead><tr>{header_html}</tr></thead>
          <tbody>{body_rows}</tbody>
        </table>
      </div>
    </main>
  </body>
</html>"""


def read_text_with_fallbacks(source_path: Path) -> str:
    raw_bytes = source_path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    for encoding in TEXT_FALLBACK_ENCODINGS:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc

    assert last_error is not None
    raise last_error


def read_csv_rows(source_path: Path) -> list[list[str]]:
    text = read_text_with_fallbacks(source_path)
    return [row for row in csv.reader(io.StringIO(text, newline=""))]


def render_plaintext_fallback_html(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <main class="page">
      <h1>{html.escape(title)}</h1>
      <div class="preformatted">{html.escape(content)}</div>
    </main>
  </body>
</html>"""


def write_pdf_from_html(html_text: str, target_path: Path, base_url: str) -> None:
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is not installed in .venv. Install it with ./.venv/bin/pip install weasyprint."
        ) from exc

    target_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_text, base_url=base_url).write_pdf(str(target_path))


def convert_file(
    source_path: Path,
    target_path: Path,
    *,
    time_ranges: list[TimeRange],
    my_whatsapp_name: str,
    use_time_ranges_for_non_working_days: bool,
) -> None:
    suffix = source_path.suffix.lower()
    if suffix == ".eml":
        try:
            model = build_email_model(source_path)
            html_text = render_email_html(model)
            write_pdf_from_html(
                html_text,
                target_path,
                base_url=source_path.parent.resolve().as_uri(),
            )
            return
        except RecursionError:
            fallback_html = build_email_plaintext_fallback_html(source_path)
            write_pdf_from_html(
                fallback_html,
                target_path,
                base_url=source_path.parent.resolve().as_uri(),
            )
            return
    elif suffix == ".txt":
        text = read_text_with_fallbacks(source_path)
        entries = parse_chat_entries(text)
        if entries:
            html_text = render_whatsapp_html(
                entries=entries,
                time_ranges=time_ranges,
                my_whatsapp_name=my_whatsapp_name,
                use_time_ranges_for_non_working_days=use_time_ranges_for_non_working_days,
            )
        else:
            html_text = render_plaintext_fallback_html(source_path.name, text)
    elif suffix == ".csv":
        html_text = render_csv_html(read_csv_rows(source_path))
    else:
        raise ValueError(f"Unsupported file type: {source_path}")

    write_pdf_from_html(html_text, target_path, base_url=source_path.parent.resolve().as_uri())


def print_summary(stats: ConversionStats) -> None:
    print("Summary:")
    print(f"  Files scanned: {stats.scanned}")
    print(f"  Converted: {stats.converted}")
    print(f"  Skipped: {stats.skipped}")
    print(f"  Failed: {stats.failed}")


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        time_ranges = parse_time_ranges(args.time_ranges)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    supported_files = iter_supported_files(input_dir)
    stats = ConversionStats(scanned=len(supported_files))
    reporter = ProgressReporter(
        total_files=len(supported_files),
        enabled=not args.no_progress,
    )

    processed = 0
    try:
        for source_path in supported_files:
            target_path = build_output_path(input_dir, output_dir, source_path)
            if target_path.exists() and not args.overwrite:
                stats.skipped += 1
                processed += 1
                reporter.update(processed, stats.converted, stats.failed)
                continue

            try:
                convert_file(
                    source_path,
                    target_path,
                    time_ranges=time_ranges,
                    my_whatsapp_name=args.my_whatsapp_name,
                    use_time_ranges_for_non_working_days=args.use_time_ranges_for_non_working_days,
                )
            except Exception as exc:
                stats.failed += 1
                print(f"Failed to convert {source_path}: {exc}", file=sys.stderr)
            else:
                stats.converted += 1

            processed += 1
            reporter.update(processed, stats.converted, stats.failed)
    finally:
        reporter.finish()

    print_summary(stats)
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
