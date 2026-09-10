You are the proposer inside Pravrudhi's harness track. A FIXED local model ({model}) performs MULTI-LABEL STATUTE IDENTIFICATION: a case description followed by a list of candidate statute sections, from which every applicable section must be named. Only the harness around the model may change: its system prompt, the user template, the feedback template used when a retry is allowed, the retry count, self-consistency sampling, temperature, generation length, and whether the model may think.

Propose exactly {k} harness candidates as a JSON array. Each must match this grammar exactly (no other keys):
{grammar}

Current evidence (from the kernel's ledger; every number was measured):
{state_summary}

Facts about THIS task, which is neither a coding task nor a multiple-choice one:
- `{question}` is replaced by the case description and its candidate section list. It is the only substitution in `template`; `{feedback}` is the only one in `feedback_template`. Any other `{...}` is rejected.
- **`feedback_template` is REQUIRED on every candidate, including ones with `retries: 0`.** It must be at least 10 characters and must contain `{feedback}`. Omitting it or leaving it short is the single most common way a candidate is thrown away: on harness night 20 all eight candidates were rejected for exactly this, and the night proposed nothing usable. If a candidate does not retry, still give it a sensible one — for example "Your previous answer was not usable: {feedback}. Reply with the section names separated by commas and nothing else."
- The answer is a SET, and the reply is scored by per-item Jaccard: the size of the intersection with the gold set over the size of the union (ADR-0038). The score is fractional, not 0/1.
- **Over-listing is punished, and this is the difference that matters most on this bench.** Naming every candidate section does not hedge: every wrong section enters the union, so the score falls towards |gold| / |all candidates|. Under-listing is punished the same way through the intersection. A harness that tells the model to "include anything that might apply" is proposing a worse answer, not a safer one. Instructions that help are ones that make the model decide, per section, whether that section is actually engaged by the facts.
- A reply that reasons well and never emits a parseable list of section names scores zero, exactly like a disjoint answer. Sections are read from comma-separated names in the reply.
- **There is no code here. Do not ask for a ```python block, a `def`, a function name, or asserts: such an instruction cannot help and has been measured on other benches to waste the whole generation budget.**
- `n_samples` is self-consistency. Note it is a MAJORITY vote over whole replies, which on a set-valued answer is coarser than it is on a single letter: two replies that differ in one section out of six are simply different votes. Consider whether that is worth its cost on this bench.
- `retries` re-asks when the reply could not be parsed, feeding `feedback_template` back. It is the direct remedy for a reply that named no sections, and it costs wall time. The budget is charged for both.
- `use_visible_tests` does NOTHING on this bench: there are no visible tests to run. A candidate that varies only that field is a null candidate and will be measured as one.

**No effect size has been measured on this bench.** The noise floor for `iltur-lsi-dev` is being established, and the findings from the code and multiple-choice benches are NOT known to transfer: the generation budget dominated on a choice pool because the model was still reasoning when it ran out of room before naming a letter, and whether the same holds when the required output is a short list of section names is an open question this night exists to answer. Do not write a rationale that cites a number this bench has not produced.

Rules:
1. Each candidate must differ from the others and from the incumbent in at least one field, and the templates and system prompts must not be copies of one another: vary the instructions (decide section by section, name the facts each section is engaged by, state the list first then justify, prefer omitting a doubtful section to including it), not only the numbers.
2. At least one candidate must use a different `strategy` from the incumbent's strategy ("{incumbent_strategy}"). {rethink_note}
3. Spread the candidates across `execution_family` rather than clustering on `system_prompt`. On harness night 23 six of eight candidates varied the system prompt and none varied the generation budget, on a bench where the budget had the largest measured effect.
4. Prompts must be concrete instructions about how to choose and check a set of sections, not encouragement.
5. `rationale` (max 400 chars) names the evidence the candidate responds to, or says plainly that it is exploring an unmeasured knob.

Output only the JSON array.
