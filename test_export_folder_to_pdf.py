import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from export_folder_to_pdf import (
    build_email_model,
    build_output_path,
    group_whatsapp_sections,
    normalize_csv_rows,
    parse_chat_entries,
    parse_time_ranges,
)


class BuildOutputPathTests(unittest.TestCase):
    def test_preserves_relative_structure_and_switches_to_pdf(self) -> None:
        input_dir = Path("/reports")
        output_dir = Path("/pdfs")
        source_path = input_dir / "days" / "2024-05-07" / "email" / "note.eml"

        result = build_output_path(input_dir, output_dir, source_path)

        self.assertEqual(result, output_dir / "days" / "2024-05-07" / "email" / "note.pdf")


class ParseChatEntriesTests(unittest.TestCase):
    def test_keeps_multiline_message_attached_to_original_entry(self) -> None:
        chat_text = (
            "[07/05/24, 09:10:11] Carlo: first line\n"
            "second line\n"
            "[07/05/24, 09:11:12] Alice: reply\n"
        )

        entries = parse_chat_entries(chat_text)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].sender_name, "Carlo")
        self.assertEqual(entries[0].message_text, "first line\nsecond line")
        self.assertEqual(entries[1].message_text, "reply")


class WhatsAppGroupingTests(unittest.TestCase):
    def test_working_day_uses_only_ranges_with_messages(self) -> None:
        chat_text = (
            "[06/05/24, 08:15:00] Carlo: morning\n"
            "[06/05/24, 16:30:00] Alice: afternoon\n"
        )
        entries = parse_chat_entries(chat_text)
        time_ranges = parse_time_ranges("07:00-13:00,15:00-18:00")

        sections = group_whatsapp_sections(entries, time_ranges, False)

        self.assertEqual([section.range_label for section in sections], ["07:00-13:00", "15:00-18:00"])

    def test_non_working_day_uses_single_section_without_flag(self) -> None:
        chat_text = (
            "[05/05/24, 08:15:00] Carlo: morning\n"
            "[05/05/24, 16:30:00] Alice: afternoon\n"
        )
        entries = parse_chat_entries(chat_text)
        time_ranges = parse_time_ranges("07:00-13:00,15:00-18:00")

        sections = group_whatsapp_sections(entries, time_ranges, False)

        self.assertEqual(len(sections), 1)
        self.assertIsNone(sections[0].range_label)
        self.assertEqual(len(sections[0].entries), 2)

    def test_non_working_day_uses_ranges_with_flag(self) -> None:
        chat_text = (
            "[05/05/24, 08:15:00] Carlo: morning\n"
            "[05/05/24, 16:30:00] Alice: afternoon\n"
        )
        entries = parse_chat_entries(chat_text)
        time_ranges = parse_time_ranges("07:00-13:00,15:00-18:00")

        sections = group_whatsapp_sections(entries, time_ranges, True)

        self.assertEqual([section.range_label for section in sections], ["07:00-13:00", "15:00-18:00"])


class NormalizeCsvRowsTests(unittest.TestCase):
    def test_pads_rows_to_maximum_width(self) -> None:
        rows = [["a", "b"], ["c"], ["d", "e", "f"]]

        normalized = normalize_csv_rows(rows)

        self.assertEqual(
            normalized,
            [["a", "b", ""], ["c", "", ""], ["d", "e", "f"]],
        )


class BuildEmailModelTests(unittest.TestCase):
    def test_prefers_html_body_and_rewrites_cid_images(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Status update"
        message["From"] = "Carlo <carlo@example.com>"
        message["To"] = "Alice <alice@example.com>"
        message["Date"] = "Tue, 07 May 2024 09:10:11 +0000"
        message.set_content("Fallback plain text")
        message.add_alternative('<p>Hello</p><img src="cid:image1">', subtype="html")
        html_part = message.get_body(preferencelist=("html",))
        self.assertIsNotNone(html_part)
        html_part.add_related(
            b"fake-image",
            maintype="image",
            subtype="png",
            cid="<image1>",
            filename="inline.png",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "sample.eml"
            source_path.write_bytes(message.as_bytes())

            model = build_email_model(source_path)

        self.assertEqual(model.subject, "Status update")
        self.assertIn("data:image/png;base64,", model.body_html)
        self.assertIn("Carlo <carlo@example.com>", model.from_text)
        self.assertIn("Alice <alice@example.com>", model.to_text)

    def test_falls_back_to_plain_text_when_no_html_exists(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Plain note"
        message["From"] = "carlo@example.com"
        message["To"] = "alice@example.com"
        message.set_content("Line one\nLine two")

        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "plain.eml"
            source_path.write_bytes(message.as_bytes())

            model = build_email_model(source_path)

        self.assertIn("Line one", model.body_html)
        self.assertIn("Line two", model.body_html)
        self.assertIn("preformatted", model.body_html)


if __name__ == "__main__":
    unittest.main()
