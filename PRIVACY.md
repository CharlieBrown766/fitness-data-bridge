> Created time: 2026-08-16 00:51
> Modified time: 2026-08-25 11:04

# Privacy

Fitness Data Bridge code is separate from personal Fitness data.

Personal credentials belong in `运行/private/credentials.json`. API caches, local database snapshots, logs and receipts belong under `运行/`; training and health records belong under `数据/`. Apple Health exports and Xunji records must not be copied into this plugin.

The Xunji clients send requests only to their declared Xunji API domains. Apple Calendar and SynFit operations run locally on the selected Mac. No telemetry or unrelated data collection is implemented.
