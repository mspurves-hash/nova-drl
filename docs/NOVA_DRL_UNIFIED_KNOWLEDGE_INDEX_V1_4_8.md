# Nova DRL Unified Knowledge Index v1.4.8

## Purpose

v1.4.8 is the **Everything-style retrieval layer** for DRL Nova.

An engineer should not need to know whether the value in front of them is a model, serial number, RMA, DRL log, manufacturer PN, or supplier order reference. They type what they know and the local knowledge index returns the relevant matches.

Examples:

```text
NOVA-DRL> BRD-1526990
NOVA-DRL> 1526990
NOVA-DRL> S07211
NOVA-DRL> 53434
NOVA-DRL> DGK52102
NOVA-DRL> MSR 56889
NOVA-DRL> IXFX24N100
NOVA-DRL> GB8 parts
```

The initial lookup is **not an AI task**. It is local indexed retrieval. AI reasoning should sit above this layer for interpretive questions such as:

> I have a GB8 drifting in the Y axis. What could be the cause?

For that type of question Nova should first retrieve relevant indexed evidence, then reason over it.

## Current coverage

v1.4.8 combines two different coverage scopes deliberately:

- **File/path discovery:** full persistent DRL file index from v1.4.2.
- **Repair knowledge:** currently the frozen 10% v1.4.6 benchmark plus v1.4.7 RMA/procurement enrichment.

The schema is intended to accept the full DRL repair corpus later without changing the engineer search interface.

## Product-part knowledge

Each ingested equipment family gets an indexed product record and product-part rows derived from its repair history. Product-part rows retain:

- equipment family,
- likely/observed manufacturer PN when present,
- part/assembly description,
- number of repair events using the item,
- explicit recorded quantity,
- quantity-unstated mentions,
- observed variants,
- linked repair-event IDs.

This means stable questions such as `GB8 parts` can be answered from the local index immediately rather than running an LLM every time.

## Procurement is separate from manufacturer PN

DRL order references are first-class searchable data but are not manufacturer PNs.

- `DGK...` = Digi-Key order reference.
- `MSR...` = Mouser order reference.
- `NWK...` / `DSK...` = procurement/order references; supplier remains unknown unless stated by source evidence.

v1.4.8 also applies a strict retrieval rule: RMA and procurement references require literal source evidence. It does not use the normal 80/20 recurrence rule to guess a tracking identifier.

For example, if a v1.4.7 row says `DGK52102` but its evidence says `Parts ordered DigiKey 55516`, v1.4.8 will not index that unsupported association as DGK52102. It can instead recover the visibly supported `55516` as a Digi-Key reference.

Similarly, a Line Card entry `MSR 56889` is indexed as a Mouser procurement reference and is excluded from product-part manufacturer-PN knowledge when the apparent PN `56889` came only from that procurement notation.

## Search implementation

The unified database defaults to:

```text
/opt/nova-drl/index/drl_knowledge_index.sqlite
```

It uses SQLite FTS5 with the trigram tokenizer. This allows fast case-insensitive substring search, including partial identifiers.

No NAS walk occurs during a query. No LLM runs during a normal lookup.

## Source refresh model

The knowledge DB is rebuildable and disposable. Sources remain authoritative.

After the normal daily DRL file-index refresh, run:

```text
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --refresh
```

This atomically rebuilds the local unified index from the latest file metadata plus currently ingested repair knowledge. An interrupted rebuild leaves the previous good knowledge DB intact.

## Engineer prompt

The included wrapper is:

```text
/opt/nova-drl/bin/nova-drl
```

Create the command once:

```text
sudo ln -sf /opt/nova-drl/bin/nova-drl /usr/local/bin/nova-drl
```

Then engineers can simply run:

```text
nova-drl
```

or perform a one-shot search:

```text
nova-drl 1526990
```

## 80/20 boundary

The standing DRL Nova 80/20 rule remains fixed. High-volume repair/parts knowledge may use recurrence and best-guess consolidation, with source evidence preserved for human exception review.

Tracking identifiers are the exception: RMA and supplier/order references are literal fields and are not inferred across repair events.
