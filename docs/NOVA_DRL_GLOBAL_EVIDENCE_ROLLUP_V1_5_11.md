# Nova DRL Global Evidence Rollup v1.5.11

The DRL product view must represent both electronics-heavy and mechanical/robot repair histories. A manufacturer PN is the best reference when the corpus supports one, but many legitimate repairs are recorded only as recurring component/assembly names such as an axis motor, encoder, belt, lead screw, sensor, board, or connector.

v1.5.11 therefore treats the normal Parts view as a recurring **reference** view:

1. Prefer recurring manufacturer/reference PN or stable value/core when available.
2. Otherwise use an explicitly named recurring component/assembly from structured replacement evidence.
3. Allow an explicit replacement/change/install/swap statement already stored in technician Repair History to recover a missed replacement occurrence.
4. Never convert cleaning, adjustment, alignment, testing, or generic work notes into a Parts replacement.
5. Require recurrence in at least two distinct repair events for the normal 80/20 Parts view.
6. Preserve raw event/source evidence underneath all rollups.

The product report also adds **Recurring Repair Actions**, derived only from structured technician work-performed text and shown only when the same action + component/reference recurs in at least two repair events. This is not a return to raw Repair History; noisy one-off history remains hidden.

All logic is global. No PRE-200, Mitsubishi, RCL1A, or other product-specific recovery mapping exists in production code.
