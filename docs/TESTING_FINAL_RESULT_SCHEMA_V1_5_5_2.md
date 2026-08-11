# Testing / Final Result Form-Aware Schema v1.5.5.2

## Testing candidate additions

```text
form_profile
semantic_role
association_basis
selected_result
raw_model_result
semantic_correction
```

## Core invariant

```text
x_mark/checkmark + normal checklist/test step => completed
x_mark/checkmark alone => never fail
```

FAIL requires explicit result-field association and unambiguous selection.

## Traveler final disposition

Known fields are label-gated by form profile. Numeric/admin values cannot be
borrowed as marks for neighboring disposition options.

## Supporting final results

Must have:

```text
semantic_role = final_result_field
known result-field label
unambiguous selected result for pass/fail
```

Document titles and generic Pass/Fail choices are rejected.
