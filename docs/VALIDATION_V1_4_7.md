# Validation — v1.4.7

Synthetic tests cover:

- known DRL distributor/order-reference classification;
- RMA normalization/deduplication;
- DGK/MSR supplier inference only where explicitly configured;
- event-level tracking merge;
- reclassification of a v1.4.6 order-reference-as-PN into procurement evidence;
- preservation/use of an explicitly associated manufacturer PN;
- SQLite RMA/order lookup indexing.

No live DRL data or hosted benchmark results are embedded in the tests.
