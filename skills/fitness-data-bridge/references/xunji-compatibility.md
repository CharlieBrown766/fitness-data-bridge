> Created time: 2026-09-24 00:55
> Modified time: 2026-09-24 00:55

# Xunji compatibility and evidence surfaces

## Verified application identity

- App wrapper: SynFit.app; inner app: trainnote.app.
- Bundle identifier: `com.xunji.react.native.app`.
- Audited version: 7.00.392, build 2.
- Audited main.jsbundle SHA-256: `e94b51144745031c6bed31d708b2f11cf1530b2e81985f650c79606381689262`.
- Identity export: 1,218 entries, 1,217 unique keys. Keep both elliptical labels under the shared key; do not overwrite one variant.

Discover the sandbox database dynamically through the existing adapter. Container UUIDs and window IDs are not stable selectors. Read `localtrains` identity, date, title, movement, note, start/end, version, sync status and deletion flag. Publication receipts retain initial identity evidence; facts retain subsequent source content and logical deletions.

## Execution changes

User workflows include copying a class to a later date, deleting its original and editing movements or set prescriptions. Current active Xunji rows express the execution arrangement. Completed timestamps and set completion flags express recorded execution. A deleted original does not prove a skipped class. A new ID does not prove extra training. Matching content is supporting evidence; require receipt identity or traceable user confirmation for a resolved successor.

Calendar contains reminders and may remain on the original dates. Never use its stale date to reverse a Xunji edit or classify missed training. Updating reminders is a separately requested operation.

## Interface verification limits

The action page was inspected with search, category filters, featured and frequently used actions. This is not a complete navigation map. No stable copy/delete button selectors are asserted here. Validate foreground interactions through Mac Agent before operating the UI. Database and bundle extraction verification does not establish the meaning of ambiguous recording flags such as `selfWeight`.
