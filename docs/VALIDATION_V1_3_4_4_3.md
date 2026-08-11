# Validation v1.3.4.4.3

For log 130813004, v1.3.4.4.2 grouped rows 8-12 because continuation
handwriting crossed into the narrow Repaired/Replaced area.

The true start markers are:

- action 1: physical row 1
- action 2: physical row 11

Expected split:

- Action 1: physical rows 1-10
- Action 2: physical rows 11-12

The first block must contain the complete note ending with `concrete proof of
this.` The second block must begin with the X / ADDED Flanges entry.

The older 130130006 traveler is also used as an offline regression target:
its large printed table header is not reconstructed as fake repair rows, and
five true action-start marks remain.
