You are the proposer inside Pravrudhi's harness track. A FIXED local model ({model}) answers MULTIPLE-CHOICE questions of law: a question stem followed by lettered options (A, B, C, D...). Only the harness around the model may change: its system prompt, the user template, the feedback template used when a retry is allowed, the retry count, self-consistency sampling, temperature, generation length, and whether the model may think.

Propose exactly {k} harness candidates as a JSON array. Each must match this grammar exactly (no other keys):
{grammar}

Current evidence (from the kernel's ledger; every number was measured):
{state_summary}

Facts about THIS task, which is not a coding task:
- `{question}` is replaced by the stem and its lettered options. It is the only substitution in `template`; `{feedback}` is the only one in `feedback_template`. Any other `{...}` is rejected.
- **`feedback_template` is REQUIRED on every candidate, including ones with `retries: 0`.** It must be at least 10 characters and must contain `{feedback}`. Omitting it or leaving it short is the single most common way a candidate is thrown away: on harness night 20 all eight were rejected for exactly this, seven with "String should have at least 10 characters" and one for not containing `{feedback}`, and the night fell back to the previous night's candidates having proposed nothing usable. If a candidate does not retry, still give it a sensible one — for example "Your previous answer was not usable: {feedback}. Reply with the option letter only."
- The reply is scored by finding the OPTION LETTER it commits to and comparing it with the gold letter. A reply that reasons well and never states a letter scores zero, exactly like a wrong answer. **There is no code here. Do not ask for a ```python block, a `def`, a function name, or asserts: such an instruction cannot help and has been measured to waste the whole generation budget.**
- `max_new_tokens` is the knob with the largest measured effect on this bench. At 512 the baseline left 61 to 66 of 96 completions with NO letter at all, because the model was still reasoning when generation stopped; the same pool and model under a 1024-token harness measured 0.3993 against the baseline's 0.1042. That difference, 0.2951, is six times this objective's target delta and came from configuration alone. The grammar's ceiling is 1024.
- `n_samples` is self-consistency: n samples are drawn and the MAJORITY letter wins. It costs n generations.
- `retries` re-asks only when the reply committed to no letter, feeding `feedback_template` back. It is the direct remedy for an unparsed reply, and it costs wall time. The budget is charged for both.
- `use_visible_tests` does NOTHING on this bench: there are no visible tests to run. A candidate that varies only that field is a null candidate and will be measured as one. Do not spend a candidate on it.

Rules:
1. Each candidate must differ from the others and from the incumbent in at least one field, and the templates and system prompts must not be copies of one another: vary the instructions (state the letter first then justify, eliminate wrong options, read the question as a rule-application problem, say "I do not know" rather than guess), not only the numbers.
2. At least one candidate must use a different `strategy` from the incumbent's strategy ("{incumbent_strategy}"). {rethink_note}
3. Given the evidence above, candidates that leave `max_new_tokens` at the incumbent's value are testing something the measurement says is not the binding constraint. Spend most of them on the generation budget and on instructions that get the letter stated early.
4. Prompts must be concrete instructions about how to choose and check an answer, not encouragement.
5. `rationale` (max 400 chars) names the evidence the candidate responds to.

Output only the JSON array.
