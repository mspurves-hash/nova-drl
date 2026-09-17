# NOVA DRL Type-First Identity Catalog Audit v2.0.0-a3

## Why a3 exists

The a2 audit correctly preserved Part # punctuation, but it treated every
model-like token anywhere in an `equipment_family` string as a possible
equipment identity. That was too broad.

The frozen v1.6.0 corpus also contains pipe-joined co-occurrence labels such as:

```text
BRD - BM23995 EXEC CAR ASYST | PS - KD200-414 DIGITAL POWER
```

Under a2, both Part # values were mapped to the entire compound string. That
made it appear that `KD200-414` had joined the `BM23995` board family. It had
not. The row records two different pieces of equipment in one source value.

Version a3 supersedes the a2 candidate mapping and implements the DRL label
contract supplied during review:

```text
EQUIPMENT TYPE - DRL PART NUMBER descriptive context
```

## Hard identity boundary

a3 performs these steps in order:

1. Split every source label on `|` before identity parsing.
2. Parse the equipment type at the beginning of each resulting segment.
3. Take only the first token after the type boundary as that segment's DRL
   Part #.
4. Treat the remaining text as context only.
5. Fence identity by `(canonical equipment type, exact Part #)`.

Examples:

| Source segment | Type | Part # | Context only |
| --- | --- | --- | --- |
| `BRD - BM23995 EXEC CAR ASYST` | `BRD` | `BM23995` | `EXEC CAR ASYST` |
| `PS - KD200-414 DIGITAL POWER` | `PS` | `KD200-414` | `DIGITAL POWER` |
| `CNTL - 9800106571 GB7 GENMARK` | `CNTL` | `9800106571` | `GB7 GENMARK` |
| `RBT - GB7 7S3L GENMARK` | `RBT` | `GB7` | `7S3L GENMARK` |
| `BRD - 1520960 4-AXIS MTR CONTROL EATON` | `BRD` | `1520960` | `4-AXIS MTR CONTROL EATON` |

This means `GB7` is not admitted as a controller identity when it occurs after
controller Part # `9800106571`, and `4-AXIS` is not admitted as a board
identity when it occurs after board Part # `1520960`.

## Equipment-type handling

Only the type equivalences confirmed for this audit are canonicalized:

- `RBT` and `ROBOT`
- `CNTL` and `CONTROLLER`
- `PS` and `POWER SUPPLY`
- `RBT ARM` and `ROBOT ARM`
- `SVO-DRV`, `SVO DRV`, `SVO DRIVER`, and `SERVO DRIVER`
- `ALIGNER` and `PREALIGNER`

Other apparent type typos or variants remain separate, flagged review items.
They are not silently corrected or merged.

When the normal spaced dash is missing, a3 recovers a segment only if it begins
with a known type prefix. The recovered candidate is still flagged for review.
If a safe type/Part # boundary cannot be established, the segment goes to the
`UNPARSED / MALFORMED` queue and the audit does not guess.

## Collision and ambiguity rules

- Exact punctuation remains part of Part # identity.
- A punctuation-stripped key is used only to expose possible within-type
  collisions.
- The same exact Part # under two equipment types creates two separate
  identities and a cross-type ambiguity.
- Pipe co-occurrence is preserved as provenance; it never implies identity
  equality.
- Multiple descriptive labels for the same type and exact Part # are shown as
  family-label variants for human review.
- No candidate, alias, typo correction, or mapping is auto-approved.

## Regression coverage

The test suite locks down the failures found during benchmark review:

- `BM23995` remains a `BRD` identity and `KD200-414` remains a `PS` identity;
- pipe-joined labels split before parsing;
- description and OEM tokens cannot become equipment identities;
- `GB7` remains fenced to the robot segment;
- user-confirmed type aliases share the intended type fence;
- punctuation collisions remain separate;
- exact Part # values seen under different types remain ambiguous;
- malformed labels are reported without guessing;
- the database, corpus, launchers, accepted facts, and Qdrant remain unchanged.

An offline parser check against all 2,660 distinct family labels retained in
the a2 audit output produced 2,607 split segments: 2,595 parsed and 12 sent to
the malformed queue. It also passed the BM23995/KD200-414, GB7, and 4-AXIS
leakage checks. This vocabulary check validates parser coverage only; the
authoritative event counts must come from running a3 against the frozen corpus.

## Files

```text
nova_drl_identity_catalog_audit_v2_0_0_a3.py
test_nova_drl_identity_catalog_audit_v2_0_0_a3.py
NOVA_DRL_IDENTITY_CATALOG_AUDIT_V2_0_0_A3.md
```

The a3 script imports the installed a2 module only for shared read-only file,
launcher, and SQLite inventory helpers. It replaces the a2 lossless-label and
identity parsing path completely. Keep
`nova_drl_identity_catalog_audit_v2_0_0_a2.py` beside it in `/opt/nova-drl`.
The release ZIP also includes an unchanged copy of that a2 helper module so the
bundle can be extracted and tested in isolation.

## Install and run

Put the three a3 files into the GitHub repository, then install them through
the existing server workflow:

```bash
cd /opt/nova-drl
git pull
python3 test_nova_drl_identity_catalog_audit_v2_0_0_a3.py
python3 nova_drl_identity_catalog_audit_v2_0_0_a3.py
```

After the tests pass, save the machine-readable audit:

```bash
python3 nova_drl_identity_catalog_audit_v2_0_0_a3.py --json \
  > /opt/nova-drl/output/nova_drl_identity_catalog_audit_v2_0_0_a3.json
```

Then confirm the file exists:

```bash
ls -lh /opt/nova-drl/output/nova_drl_identity_catalog_audit_v2_0_0_a3.json
```

## What to review next

Review these sections in order:

1. cross-type exact Part # groups;
2. within-type punctuation-normalization collisions;
3. unparsed or malformed family segments;
4. the ranked 80/20 candidate queue;
5. the critical DRL benchmarks.

Do not redirect a launcher, deploy a resolver, modify the frozen corpus, or
enable Qdrant based only on this audit. The next milestone is a separately
frozen, human-approved identity catalog.
