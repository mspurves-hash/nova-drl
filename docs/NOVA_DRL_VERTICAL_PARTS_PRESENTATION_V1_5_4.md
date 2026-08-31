# NOVA DRL v1.5.4 — Vertical Parts Presentation

For technician-facing product searches, the parts section is intentionally simple:

```text
PARTS REPLACED
--------------
PART NUMBER                                  TIMES REPLACED
-------------------------------------------- --------------
PN-A100                                                   12
PN-B200                                                    8
PN-C300                                                    3
```

`Times Replaced` means the number of distinct indexed repair events containing that part. The list is sorted highest to lowest. Detailed variants, quantities, source events, and evidence remain in the underlying knowledge index and are still searchable; they are simply omitted from the main parts list for readability. The printable PDF uses the same presentation.
