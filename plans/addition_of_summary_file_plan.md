# MBOX Extractor Output Restructure and CSV Summary

## Summary
Update the extractor so matching `.eml` files are no longer written directly into the output root. Instead, always create an `email/` subfolder under the requested output directory and place exported messages there.

Add a `summary.csv` file at the root of the output directory after processing completes. It should contain one row per exported message and include message metadata plus the relative exported file path.

## Key Changes
### Output layout
- Keep `--output-dir` as the root export folder.
- Create `<output-dir>/email/` before writing any `.eml` files.
- Update filename generation so collision handling still happens inside the `email/` subfolder.
- Store the file path in CSV as a POSIX-style relative path from the output root, e.g. `email/2024-12-15_01-35-53_carlo-conserva.eml`.

### CSV summary generation
- Add a CSV writer flow that collects one summary record for each successfully exported message.
- Write `summary.csv` in the output root after processing completes.
- Use these exact columns in this order:
  - `type`
  - `timestamp`
  - `From address`
  - `from name`
  - `To address`
  - `To name`
  - `file`
- Populate values as follows:
  - `type`: always `email`
  - `timestamp`: parsed message datetime serialized as ISO 8601 with offset
  - `From address`: first parsed `From` address
  - `from name`: first parsed `From` display name, or empty string if missing
  - `To address`: first parsed `To` address
  - `To name`: first parsed `To` display name, or empty string if missing
  - `file`: relative path to the exported `.eml` under the output root
- Write a header row even if no messages are exported.

### Message metadata handling
- Refactor address extraction so the script returns both display name and address for the first `From` and first `To` entries.
- Keep sender filtering based on exact case-insensitive match of the parsed `From` address only.
- Keep filename slug generation based on the first `To` recipient:
  - use `To` display name when present
  - otherwise use the `To` address local-part
- Keep timestamp sourcing unchanged from the parsed `Date` header used for time filtering.

### Reporting and compatibility
- Keep the existing progress reporter and end-of-run counts.
- Keep `.eml` output as raw original message bytes.
- Do not add CSV rows for skipped or failed messages; `summary.csv` should describe exported files only.

## Public Interfaces
- CLI remains:
  - `python extract_mbox_messages.py --mbox <path> --output-dir <dir> --from-address <email> --time-ranges <ranges>`
- Internal helpers should now clearly cover:
  - extraction of first `From` and first `To` name/address pairs
  - output-path builder rooted under `email/`
  - summary-row creation and CSV writing
  - relative-path serialization using `/` separators in CSV

## Test Plan
- Exported `.eml` files land under `<output-dir>/email/` and nowhere else.
- `summary.csv` is created in the output root with the exact expected header order.
- A matching message produces one CSV row with:
  - `type=email`
  - ISO 8601 timestamp with offset
  - parsed sender and recipient addresses
  - blank name fields when display names are missing
  - `file` equal to the relative `email/...eml` path
- Filename collisions still generate unique files and the CSV records the final chosen path.
- Zero-match runs still create `summary.csv` with just the header row.
- Progress output and end summary still behave cleanly with the new CSV step.

## Assumptions
- The first parsed `From` and first parsed `To` addresses are the authoritative values for both filtering and CSV output.
- CSV should use UTF-8 encoding and standard quoted CSV behavior from Python’s `csv` module.
- The `file` column should always use forward slashes, regardless of OS path separators.
