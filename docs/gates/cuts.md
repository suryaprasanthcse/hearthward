# Cuts

Append-only: date, cut, reason. The hour is not recorded for these entries; blueprint section 11 defines the triggers.

| Date | Cut | Reason |
|---|---|---|
| 2026-10-21 | Managed Service for Apache Flink (Lambda and SQS FIFO instead), the Fire TV app, Lambda MicroVM vision workers, Amazon Connect voice (L3), real Ring and Bee devices | Already cut when the blueprint was written (section 11, "Already cut") |
| 2026-10-22 | Hosting the console on S3 + CloudFront (the "Phase 6" stack, commit `ebc6552`, reverted in `aea7956`) | AWS rejected the deploy with HTTP 403 ("account must be verified before you can add new CloudFront resources"). The console runs under the console blueprint's Cut Line B: complete, served locally, probes recorded. |

Not a section 11 cut, recorded so the state is plain: the causal store runs on `InMemoryGnomonBackend` behind the
`CausalStore` port (`src/hearthward/causal/gnomon_adapter.py`; the G4 log lists it), so the FalkorDB-backed GNOMON graph
is not bound. This matches the shape of cut C3, but the G4 gate was green, so C3's trigger did not fire.
