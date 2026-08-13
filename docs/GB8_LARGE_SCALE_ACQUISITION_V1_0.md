# Nova DRL GB8 Large-Scale Acquisition Launcher v1.0

This launcher leaves `nova_traveler_reader_v1_3_5_1.py` unchanged and applies that frozen acquisition engine to every top-level folder beginning with `RBT - GB8`.

It creates one unified v1.3.5.1 corpus manifest, preserves original NAS source paths and hashes, reuses matching evidence already acquired by the pilot, performs no repair/parts/diagnostic classification, accepts no facts, and writes nothing to Qdrant.

The launcher is resumable: every Traveler evidence record is written immediately. Re-running the same command reuses matching completed records unless `--force` is supplied.
