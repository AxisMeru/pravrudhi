# Reason codes and rule text (`/api/v1/analyse-facts`)

Every contract in an analyse-facts response carries a machine-readable `reason`, and every element carries a `quote_check`. Both are fixed
enumerations published in [`openapi-v1.json`](openapi-v1.json) (`ContractResultOut.reason`: 16 values; `ElementResultOut.quote_check`: 8
values), so a client can be checked against the schema. The plain-language text below says what each code means; it describes what the judge
or the code did, never the content of your facts. No change to any verdict comes with these fields.

## Contract `reason`

| Code | What it means |
|---|---|
| `all_elements_established` | The judge found every condition this provision requires in words quoted from your facts, and found no fact that defeats the claim. |
| `denial_established` | The judge found a fact in your case that defeats this claim, and quoted it. |
| `missing_element` | The judge did not find at least one required condition shown in your facts. |
| `no_training_statute_text` | We have no statute text for this provision, so we did not judge it. |
| `judge_error` | A judge call failed on at least one condition, so we give no answer. |
| `assembly_lean_mismatch` | Two internal checks disagreed, so we give no answer. |
| `denial_unquotable` | The judge thinks a defeating fact exists but could not give a valid word-for-word quote for it. Please have a lawyer look. |
| `second_judge_defeater_disagreement` | The two judges disagree on whether a defeating fact exists. Please have a lawyer look. (deployments that use two judges only) |
| `uncertain` | The judge is not sure whether a condition holds. Please have a lawyer look. |
| `uncertain_second_judge` | The second judge is not sure whether a condition holds. Please have a lawyer look. (deployments that use two judges only) |
| `second_judge_unavailable` | The second judge was unavailable, so we give a referral, not an answer. (deployments that use two judges only) |
| `gate1_unavailable` | The entailment check (a separate check of the quoted words against the claim) was unavailable, so we give a referral, not an answer. |
| `gate1_not_entailed` | The entailment check (a separate check of the quoted words against the claim) did not find enough support for it. Please have a lawyer look. |
| `gate1_contradiction` | The entailment check (a separate check of the quoted words against the claim) found they contradict it. Please have a lawyer look. |
| `contract_not_validated` | This provision is not on the validated list, so we give a referral, not a proof or denial. |
| `input_too_long` | The facts and question together are too long for the checker to read in one go, so we give a referral, not an answer. Please shorten them or have a lawyer look. |

Rows marked "deployments that use two judges only" apply when the deployment runs a second judge.

## Element `quote_check`

A judge that calls an element established must quote the words that show it. The service then looks for the quote, word for word (same
capital letters and spacing), in the fact the judge named. `quote_check` is the result of that check; it is null when no check ran.

| Code | What it means |
|---|---|
| `ok` | The judge's quoted words appear exactly once in the fact they name. |
| `not_established` | The judge did not find this condition shown in your facts. |
| `no_quote` | The judge said this condition holds but gave no words to show it. |
| `unknown_fact` | The judge pointed to a fact that is not among yours. |
| `empty_quote` | The judge's quote was empty. |
| `non_evidential_quote` | The judge's quote was too short or only punctuation to count as evidence. |
| `quote_not_found` | The judge's quote does not appear word for word (same capital letters and spacing) in the fact it named. |
| `ambiguous_quote` | The judge's quote appears more than once in that fact, so we cannot tell which passage it meant. The condition is not counted as shown. |

A quote that occurs more than once in the fact is `ambiguous_quote`: the element is **not** counted as shown, and `occurrences` carries the count.

## Rule text

Each contract result can also carry three fields that say what text it was checked against. They are **off by default** (`expose_rule_text`) and are absent from the response unless the deployment enables them:

- `rule_text`: the provision text the contract is checked against, from the Lean checker's `--describe-source`. It is the registry's recorded source text
  (the sources cite India Code) and it is **unofficial**: it is not the official text of the law.
- `judge_rule_text`: exactly the first `statute_chars` characters (600 in the shipped configuration) of the statute text the judge was
  configured with, that is, the text **as sent in the judge's prompt**. It is returned when `statute_text_mismatch` is true (the judge's
  text differs from `rule_text`) or when the judge's text was cut (it is longer than `statute_chars`), so you can see what the judge worked from. It is null otherwise, and null when no judge statute text is
  configured for the contract.
- `rule_text_source`: where `rule_text` came from; today always `lean_describe_source`.
