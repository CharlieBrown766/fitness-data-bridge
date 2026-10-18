> Created time: 2026-09-24 00:55
> Modified time: 2026-10-18 13:37

# Xunji compatibility and evidence surfaces

## Verified application identity

- App wrapper: SynFit.app; inner app: trainnote.app.
- Bundle identifier: `com.xunji.react.native.app`.
- Audited application files: 7.00.405, build 3 (static inspection).
- Audited main.jsbundle SHA-256: `57367756d45471f5a2842fddf08d6537881734a54ed75ab45745b7de63c45228`.
- Identity export: 1,223 entries, 1,222 unique keys. Keep both elliptical labels under the shared key; do not overwrite one variant.

Discover the sandbox database dynamically through the existing adapter. Container UUIDs and window IDs are not stable selectors. Read `localtrains` identity, date, title, movement, note, start/end, version, sync status and deletion flag. Publication receipts retain initial identity evidence; facts retain subsequent source content and logical deletions.

## Execution changes

User workflows include copying a class to a later date, deleting its original and editing movements or set prescriptions. Current active Xunji rows express the execution arrangement. Completed timestamps and set completion flags express recorded execution. A deleted original does not prove a skipped class. A new ID does not prove extra training. Matching content is supporting evidence; require receipt identity or traceable user confirmation for a resolved successor.

Calendar contains reminders and may remain on the original dates. Never use its stale date to reverse a Xunji edit or classify missed training. Updating reminders is a separately requested operation.

## Interface verification limits

The action page was inspected with search, category filters, featured and frequently used actions. This is not a complete navigation map. No stable copy/delete button selectors are asserted here. Validate foreground interactions through Mac Agent before operating the UI. Database and bundle extraction verification does not establish the meaning of ambiguous recording flags such as `selfWeight`.


## Open API Key and complete-data contract

The audited bundle contains `OpenApiKeyPage` with issue, reissue, revoke, one-time full-key display and usage metadata. Its text states one active Open API key per member; the older training/food/body/template Skill page remains present. Static evidence does not prove the new key's format, cross-service permissions or equivalence to every legacy token.

The connector stages such keys separately and activates only individually verified read endpoints. No live-account Key-management operation or new-Key server behavior is attested here. Preserve endpoint-specific verification and keep old credentials available during staging.

The bundled training-v2 description includes full-data RPE, configured/actual rest, heart-rate summaries, superset `items` and `dropSets`. The transport already supports gzip, bearer headers and 15/30/45-second training intervals. Native SQLite normalization and API conversion remain separate contracts. The last UI selector verification described above predates this static app audit; app-file inspection does not establish current interface behavior.
