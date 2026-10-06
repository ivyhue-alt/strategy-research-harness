# Data provenance

Research ran on XAUUSD M1 bars covering 2026-04 through 2026-06, sourced from
HistData.com. The files are not redistributed here under that provider's terms.

Scripts default to `research/data/` and accept an `M1_DIR` environment variable
to point at a local copy. The provider ships a per-file status report noting session
gaps; the recurring ~3,595s gaps are the daily futures break and were treated as
expected rather than missing data.
