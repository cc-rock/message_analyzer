import unittest
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fill_timesheet_xlsx import DaySummary, load_day_summaries, update_workbook


class FakeCell:
    def __init__(self, value=None) -> None:
        self.value = value


class FakeSheet:
    def __init__(self, cells: dict[str, object], max_row: int) -> None:
        self.max_row = max_row
        self._cells = {key: FakeCell(value) for key, value in cells.items()}

    def __getitem__(self, key: str) -> FakeCell:
        return self._cells.setdefault(key, FakeCell())

    def __setitem__(self, key: str, value: object) -> None:
        self.__getitem__(key).value = value


class FakeWorkbook:
    def __init__(self, sheet: FakeSheet) -> None:
        self.worksheets = [sheet]
        self.saved_path = None

    def save(self, path: Path) -> None:
        self.saved_path = path


class LoadDaySummariesTests(unittest.TestCase):
    def test_loads_day_type_from_csv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "by_day.csv"
            csv_path.write_text(
                (
                    "date,day type,messages in lunch time,start working time,end working time\n"
                    "2026-04-12,weekend,NO,08:00:00,11:00:00\n"
                ),
                encoding="utf-8",
            )

            summaries = load_day_summaries(csv_path)

        self.assertEqual(summaries[date(2026, 4, 12)].day_type, "weekend")


class UpdateWorkbookTests(unittest.TestCase):
    def test_non_working_day_without_lunch_overlap_leaves_lunch_cell_blank(self) -> None:
        row_date = date(2026, 4, 12)
        workbook = FakeWorkbook(FakeSheet({"A1": "12/4/2026"}, max_row=1))
        summaries = {
            row_date: DaySummary(
                day=row_date,
                day_type="weekend",
                lunch_flag="NO",
                start_time=time(8, 0, 0),
                end_time=time(11, 0, 0),
            )
        }

        with patch("fill_timesheet_xlsx.load_workbook", return_value=workbook):
            update_workbook(
                Path("timesheet.xlsx"),
                summaries,
                date_column="A",
                start_column="B",
                end_column="C",
                lunch_column="D",
            )

        self.assertEqual(workbook.worksheets[0]["B1"].value, 8.0)
        self.assertEqual(workbook.worksheets[0]["C1"].value, 11.0)
        self.assertIsNone(workbook.worksheets[0]["D1"].value)

    def test_equal_start_and_end_skips_row_entirely(self) -> None:
        row_date = date(2026, 4, 12)
        workbook = FakeWorkbook(FakeSheet({"A1": "12/4/2026"}, max_row=1))
        summaries = {
            row_date: DaySummary(
                day=row_date,
                day_type="working",
                lunch_flag="NO",
                start_time=time(8, 0, 0),
                end_time=time(8, 0, 0),
            )
        }

        with patch("fill_timesheet_xlsx.load_workbook", return_value=workbook):
            update_workbook(
                Path("timesheet.xlsx"),
                summaries,
                date_column="A",
                start_column="B",
                end_column="C",
                lunch_column="D",
            )

        self.assertIsNone(workbook.worksheets[0]["B1"].value)
        self.assertIsNone(workbook.worksheets[0]["C1"].value)
        self.assertIsNone(workbook.worksheets[0]["D1"].value)

    def test_non_working_overnight_shift_without_lunch_overlap_leaves_lunch_cell_blank(self) -> None:
        row_date = date(2026, 4, 12)
        workbook = FakeWorkbook(FakeSheet({"A1": "12/4/2026"}, max_row=1))
        summaries = {
            row_date: DaySummary(
                day=row_date,
                day_type="weekend",
                lunch_flag="NO",
                start_time=time(22, 0, 0),
                end_time=time(1, 0, 0),
            )
        }

        with patch("fill_timesheet_xlsx.load_workbook", return_value=workbook):
            update_workbook(
                Path("timesheet.xlsx"),
                summaries,
                date_column="A",
                start_column="B",
                end_column="C",
                lunch_column="D",
            )

        self.assertEqual(workbook.worksheets[0]["B1"].value, 22.0)
        self.assertEqual(workbook.worksheets[0]["C1"].value, 25.0)
        self.assertIsNone(workbook.worksheets[0]["D1"].value)


if __name__ == "__main__":
    unittest.main()
