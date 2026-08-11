# v1.3.4.4.3 Changelog

- Replaced row-ink repair-start splitting with true connected-component X/mark detection.
- Rejects sparse continuation handwriting that drifts into action columns.
- Maps each accepted start-marker centroid to its physical repair row.
- Uses the next true start-marker row as the repair-block boundary.
- Prevents the large leading printed table-header span from being reconstructed as fake rows.
- Preserves row mark scores for diagnostics only.
- Adds accepted/rejected start-component diagnostics to block_detection_debug.json.
- Keeps sparse-grid reconstruction from v1.3.4.4.2.
- No DRL source modifications.
- No Qdrant writes.
