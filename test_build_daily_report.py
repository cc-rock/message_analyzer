import unittest
from datetime import date, datetime
from pathlib import Path

from build_daily_report import SentMessage, compute_working_end, compute_working_start


def make_message(timestamp_text: str) -> SentMessage:
    return SentMessage(
        source="whatsapp",
        timestamp=datetime.fromisoformat(timestamp_text),
        recipient_name="Recipient",
        recipient_address="recipient@example.com",
        file_path=Path("debug/message.txt"),
        summary_row={},
    )


class ComputeWorkingStartTests(unittest.TestCase):
    def test_after_midnight_message_assigned_to_previous_day_starts_at_ten(self) -> None:
        day_date = date(2023, 5, 4)
        first_message = make_message("2023-05-05T02:32:58")

        result = compute_working_start(day_date, first_message, non_working_day=False)

        self.assertEqual(result, "10:00:00")

    def test_same_day_early_message_keeps_actual_time(self) -> None:
        day_date = date(2023, 5, 4)
        first_message = make_message("2023-05-04T08:15:00")

        result = compute_working_start(day_date, first_message, non_working_day=False)

        self.assertEqual(result, "08:15:00")

    def test_same_day_first_message_after_ten_clamps_to_ten(self) -> None:
        day_date = date(2023, 5, 4)
        first_message = make_message("2023-05-04T13:00:00")

        result = compute_working_start(day_date, first_message, non_working_day=False)

        self.assertEqual(result, "10:00:00")

    def test_working_day_without_messages_starts_at_ten(self) -> None:
        result = compute_working_start(date(2023, 5, 4), None, non_working_day=False)

        self.assertEqual(result, "10:00:00")

    def test_non_working_day_keeps_actual_time(self) -> None:
        first_message = make_message("2023-05-06T08:15:00")

        result = compute_working_start(date(2023, 5, 6), first_message, non_working_day=True)

        self.assertEqual(result, "08:15:00")


class ComputeWorkingEndTests(unittest.TestCase):
    def test_after_midnight_last_message_keeps_actual_time(self) -> None:
        day_date = date(2023, 5, 4)
        last_message = make_message("2023-05-05T02:32:58")

        result = compute_working_end(day_date, last_message, non_working_day=False)

        self.assertEqual(result, "02:32:58")


if __name__ == "__main__":
    unittest.main()
