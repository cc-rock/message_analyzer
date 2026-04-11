# Buffered MBOX-to-EML Extractor With Progress Reporting

## Summary
Build a standalone Python CLI script that streams a very large `.mbox` file sequentially, parses one message at a time, filters by sender and allowed time ranges, and writes only matching messages as `.eml` files into a target directory.

The script should also report progress by reading the input file size once at startup and computing completion as `bytes_read / total_bytes`. Progress should be rendered on a single console line using carriage-return updates so the terminal does not fill with percentage messages.

## Key Changes
### CLI and inputs
- Add a Python entry script, e.g. `extract_mbox_messages.py`.
- Expose required CLI parameters:
  - `--mbox` for input `.mbox` path
  - `--output-dir` for destination folder
  - `--from-address` for exact sender address match
  - `--time-ranges` for comma-separated ranges like `07:00-13:00,15:00-18:00,21:00-23:30`
- Expose optional runtime flags:
  - `--verbose` to print per-skip/per-write reasons
  - `--no-progress` to disable in-place progress output
  - `--encoding-errors` with default tolerant handling for malformed input bytes if needed
- Normalize the sender filter to case-insensitive exact address matching against the parsed `From` header address only.

### Streaming and parsing behavior
- Open the `.mbox` in binary mode and read it sequentially line by line.
- Get the total file size once at startup from the filesystem.
- Maintain a running `bytes_read` counter using the raw byte length of each line as it is consumed.
- Treat mbox message boundaries as lines beginning with `b"From "` at message boundaries.
- Accumulate raw bytes for one message only; when the next boundary is found, parse the completed message and reset the buffer.
- Parse each message from bytes using the standard library email parser so matching messages can be written back out unchanged as `.eml`.
- Do not use `mailbox.mbox` iteration, since the goal is strict streaming over very large files.

### Filtering and output behavior
- Parse the `Date` header into an aware datetime when possible.
- Interpret time ranges using each message’s own timestamp offset from the `Date` header.
- Support multiple ranges and overnight ranges such as `23:00-02:00`.
- Treat both range endpoints as inclusive.
- Skip messages with missing or unparseable `Date` headers and record that in the run summary.
- Build output filenames as `yyyy-mm-dd_hh-mm-ss_firstname-lastname.eml`.
- Derive `firstname-lastname` from the first parsed `To` recipient:
  - use the display name if present
  - otherwise use the local-part before `@`
  - replace spaces and special characters with `-`
  - collapse repeated separators and trim leading/trailing `-`
- Make filenames collision-safe by appending a numeric suffix only when the computed filename already exists.

### Progress and console output
- Add a lightweight progress reporter that updates after each completed message, using the current `bytes_read` value and total file size.
- Render progress on the same line with `\r`, showing at least percentage and optionally message counts, for example `42.8% | scanned 1834 | extracted 27`.
- Flush stdout on each progress refresh so the line updates immediately.
- Print a final newline once processing completes so the summary starts on a clean line.
- When `--verbose` is enabled, avoid corrupting the progress line by clearing or finishing the current progress line before printing a log message, then resume progress updates afterward.
- If stdout is not a TTY, fall back to less frequent plain-text progress updates or disable in-place rendering automatically.

### Reporting and robustness
- Print a final summary with at least:
  - total messages scanned
  - messages parsed successfully
  - skipped for sender mismatch
  - skipped for date parse failure
  - skipped for time mismatch
  - extracted count
- Continue past malformed messages where feasible and log the reason instead of aborting the full run.
- Create the output directory if it does not exist.

## Public Interfaces
- CLI contract:
  - `python extract_mbox_messages.py --mbox <path> --output-dir <dir> --from-address <email> --time-ranges <ranges>`
- Internal helpers to define clearly:
  - time-range parser returning normalized minute-based intervals
  - sender extractor from parsed headers
  - recipient slug generator from first `To` address
  - filename builder with collision handling
  - progress renderer that supports in-place updates and non-TTY fallback
  - message matcher that combines sender/date/time checks

## Test Plan
- Happy path: extract only messages from the target sender within one allowed time range.
- Multiple ranges: include messages matching any range and exclude others.
- Overnight range: `23:00-02:00` matches late-night and after-midnight messages correctly.
- Boundary times: exact start and exact end times are both included.
- Sender matching: exact parsed address match is case-insensitive; display-name differences do not matter.
- Recipient naming: use first `To` display name when present, otherwise local-part fallback.
- Date failures: malformed or missing `Date` headers are skipped and counted.
- Filename collisions: repeated same timestamp/recipient names produce unique suffixed filenames.
- Progress accuracy: percentage increases monotonically based on bytes read and ends at 100%.
- Console behavior: progress updates reuse one line in a TTY and summary prints cleanly afterward.
- Malformed content: parser errors or unusual headers do not stop the full extraction run.

## Assumptions
- Input is a standard Unix mbox-style file that uses `"From "` separator lines.
- The first `To` recipient is the intended recipient for filename generation.
- `.eml` output should preserve the original raw message bytes for matching messages.
- Progress is approximate to parsing completion, based on bytes consumed from the file, which is the right metric for a streaming extractor.
- If the repo remains empty, this can be delivered as a single self-contained script plus a short README/example usage note.
