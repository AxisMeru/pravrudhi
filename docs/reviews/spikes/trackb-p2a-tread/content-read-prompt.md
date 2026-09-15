You are a content reader for a legal-reasoning dataset (Indian Penal Code / Bharatiya Nyaya Sanhita offence limbs). Read ONLY the file named at the end. Do not run git, python or any command; do not read other files. Write your report to the output path named at the end.

Each record is a teacher-authored scenario for one contract (offence limb): `required_elements` (0-indexed, el<k> is the k-th), `facts` keyed F_narrative and F_el<k>, `elements` with status/evidence, `shape` (control | omit:<k> | deny), `abstain`, `answer`. The structural checker has already validated the IR layer. Your job is the semantic layer it cannot see. For EVERY record answer these, each as PASS/FAIL with a one-line reason:

1. fact_fit: each F_el<k> fact, read on its own, actually establishes element k and ONLY element k (a fact that also entails another element, e.g. an "entrustment" fact that already states the dishonest misappropriation, FAILS).
2. narrative_neutral: F_narrative sets the scene without establishing any element.
3. omission_real (omit:<k> only): the omitted element k is NOT established anywhere in the narrative or the other facts, and the answer/abstention says the offence cannot be determined for that reason. Control/deny: N/A.
4. denial_real (deny only): the denial fact genuinely raises the stated defence and the answer reflects that the limb does not apply. Others: N/A.
5. coherent: the scenario is a coherent, plausible fact pattern (named parties, conduct, object, setting), not a paraphrase of the provision text.
6. english_law_register: written as a legal fact pattern in plain English, no Sanskrit terms, no role tokens leaking into prose.

Also flag near-duplicate scenarios (same parties/conduct/object across two records) as `duplicate_of`.

Output: JSON with `records`: [{attempt_id, contract_id, shape, fact_fit, narrative_neutral, omission_real, denial_real, coherent, english_law_register, duplicate_of, notes}], then `summary`: counts of FAIL per question overall and per contract_id, and a list of the 10 worst records with the reason. Be strict: a doubtful fact_fit is a FAIL with the doubt stated. Do not soften.

INPUT FILE: {INPUT}
OUTPUT FILE: {OUTPUT}
