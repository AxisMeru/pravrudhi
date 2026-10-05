"""Accused-attribution check, v1 variant D0 (design: prabhasa-nyaya #418 accused-attribution design; Lead-2's decisions of
2026-10-05).

THE FAILURE IT TARGETS. For an element that requires a specific act BY the accused under review (v1: bns85's "the facts attribute
specific acts to this accused ..."), the judge can cite a fact that states cruelty perfectly well but about a CO-ACCUSED, or about
"the accused" / "the in-laws" collectively. The span-relevance check cannot see that: it only asks whether the span, alone,
states the
element. This check asks WHO did the quoted act.

HOW (D0, fully deterministic, no model call). Candidate actor expressions are found in the quoted fact by regex, each with
character
offsets: the accused under review's own aliases, other named parties, collective forms, kin, pronouns. The ACTOR is the group of
candidates immediately before the first act verb (or, in a simple passive, after "by"). Then:

  R1 resolvable      an act verb and an actor group were found, and nothing but light bridge words sits between them
                     -> else `accused_attribution_unresolved`
  R2 equals accused  the group is exactly the accused under review (one identity, no other party)
                     -> else `accused_attribution_not_matched` (the act is attributed to someone else)
  R3 not collective  no collective/plural/generic form ("the accused", "accused persons", ranges, "in-laws", "they", "X and Y",
                     "X along with Y") in the actor group -> else `accused_attribution_collective`
  R4 pronoun-only    he/she with no alias is NOT resolved by guessing an antecedent from another fact -> unresolved
  R5 any error       refuses (unresolved, with the error recorded); there is no pass-on-error path
  R0 no accused      no `AccusedRef` was given -> `accused_not_specified`

Collective words in OBJECT position ("... beat her in front of her in-laws") are not the actor and do not refuse. A refusal does
not
change the element's status: the established call and its quote stay visible and the agent turns the refusal into REFER_TO_LAWYER
(never not_established: we refuse to prove, we do not assert a denial). Everything here is OFF unless
`accused_attribution_enabled` is set, and then only elements the config lists under `requires_actor` are checked.

All examples in this repo's tests are constructed toy sentences; no real or sealed text is, or may be, used here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from pravrudhi.application.nyaya_judges import ElementJudgment, Judge, JudgeRequest

REASON_NOT_SPECIFIED = "accused_not_specified"
REASON_UNRESOLVED = "accused_attribution_unresolved"
REASON_NOT_MATCHED = "accused_attribution_not_matched"
REASON_COLLECTIVE = "accused_attribution_collective"
REASON_CONFIG_UNMATCHED = "accused_attribution_config_unmatched"
ATTRIBUTION_REASONS = (REASON_NOT_SPECIFIED, REASON_UNRESOLVED, REASON_NOT_MATCHED, REASON_COLLECTIVE)

MAX_ALIASES = 20
MAX_ALIAS_CHARS = 80
MAX_QUOTE_CHARS = 5000


@dataclass(frozen=True)
class AccusedRef:
    """The accused under review. `aliases` are the spellings the case documents use for this person (e.g. "Accused No.2", "A2",
    "Petitioner No.2", a name); `other_parties` are alias groups of other named parties (co-accused, the husband, ...), one
    tuple per
    person. Matching is case-insensitive and tolerant of spacing/punctuation ("Accused No. 2" == "accused no 2"); it is never
    fuzzy."""

    id: str
    aliases: tuple[str, ...]
    other_parties: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        def bad(x: Any) -> bool:
            return not isinstance(x, str) or not x.strip() or len(x) > MAX_ALIAS_CHARS

        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("AccusedRef.id must be a non-empty string")
        if not self.aliases or len(self.aliases) > MAX_ALIASES or any(bad(a) for a in self.aliases):
            raise ValueError(
                f"AccusedRef.aliases must be 1..{MAX_ALIASES} non-empty strings of at most {MAX_ALIAS_CHARS} characters"
            )
        for grp in self.other_parties:
            if not grp or len(grp) > MAX_ALIASES or any(bad(a) for a in grp):
                raise ValueError("AccusedRef.other_parties entries must be non-empty alias tuples of valid strings")


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    text: str
    role: str  # self | other | collective | generic | pronoun
    identity: str  # SELF | OTHER:<n> | UNK:<canon> | COLLECTIVE | GENERIC | PRON


@dataclass(frozen=True)
class AttributionResult:
    passed: bool
    reason: str | None
    variant: str = "D0"
    rule: str | None = None
    actor_span: str | None = None
    actor_start: int | None = None
    actor_end: int | None = None
    verb: str | None = None
    n_candidates: int = 0
    candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    error: str | None = None
    actor_p: float | None = None  # M1 only: the selector's probability for the chosen party

    def as_dict(self) -> dict[str, Any]:
        d = self._base_dict()
        if self.actor_p is not None:
            d["actor_p"] = self.actor_p
        return d

    def _base_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "passed": self.passed,
            "reason": self.reason,
            "rule": self.rule,
            "actor_span": self.actor_span,
            "actor_start": self.actor_start,
            "actor_end": self.actor_end,
            "verb": self.verb,
            "n_candidates": self.n_candidates,
            "candidates": [dict(c) for c in self.candidates],
            "error": self.error,
        }


# -- lexicons (public, hand-written, synthetic knowledge of English legal prose; versioned with this file) -----------------------
_NUM = r"(?:no|nos|number|numbers)\.?"
_ROLE = r"(?:accused|petitioners?|applicants?|respondents?|co-accused|co\s+accused)"
_RANGE = re.compile(rf"\b{_ROLE}[\W_]*{_NUM}?[\W_]*\d+(?:[\W_]*(?:to|and|&|-|,)[\W_]*\d+)+", re.I)
_NUMBERED = re.compile(rf"\b{_ROLE}[\W_]*(?:{_NUM}[\W_]*)?\d+(?![A-Za-z0-9])", re.I)
_SHORT_NUMBERED = re.compile(r"(?<![A-Za-z0-9])[AP][\s.-]?\d{1,3}(?![A-Za-z0-9])", re.I)  # "A2", "P-3"
_COLLECTIVE_PATTERNS = [
    r"\ball\s+(?:the\s+)?accused\b",
    r"\baccused\s+persons?\b",
    r"\bpetitioners\b",
    r"\bapplicants\b",
    r"\brespondents\b",
    r"\bin[\s-]*laws?\b",
    r"\bparents[\s-]*in[\s-]*law\b",
    r"\bfamily\s+members\b",
    r"\brelatives\b",
    r"\b(?:his|her|their)\s+(?:parents|family|sisters|brothers|relatives)\b",
    r"\band\s+others\b",
    r"\ball\s+of\s+them\b",
    r"\bothers\b",
]
_COLLECTIVE = [re.compile(p, re.I) for p in _COLLECTIVE_PATTERNS]
_GENERIC = [
    re.compile(r"\bthe\s+accused\b(?![\W_]*(?:" + _NUM + r"|\d))", re.I),
    re.compile(r"\bthe\s+petitioner\b(?![\W_]*(?:" + _NUM + r"|\d))", re.I),
    re.compile(r"\bco-accused\b", re.I),
]
_KIN_SINGULAR = re.compile(r"\b(?:his|her)\s+(?:mother|father|sister|brother|uncle|aunt|cousin|husband|wife)\b", re.I)
_PRON_THEY = re.compile(r"\b(?:they|them)\b", re.I)
_PRON_SINGLE = re.compile(r"\b(?:he|she)\b", re.I)
_VERB = re.compile(
    r"\b(?:harass\w*|tortur\w*|beat\w*|assault\w*|abus\w*|taunt\w*|demand\w*|ill[\s-]*treat\w*|mistreat\w*|treat(?:ed|s)?|threat\w*|"
    r"subject(?:ed|s)?|slap\w*|hit(?:s|ting)?|kick\w*|punch\w*|throw\w*|threw|thrown|drove|driven|forc(?:e|ed|es|ing)|insult\w*|humiliat\w*|"
    r"starv\w*|confin\w*|pressur\w*|coerc\w*|misbehav\w*|scold\w*|quarrel\w*|cause[ds]?|inflict\w*|attack\w*|strangl\w*|burn(?:ed|t|s)?|"
    r"lock(?:ed|s|ing)?|push(?:ed|es|ing)?|shov(?:e|ed|es|ing)|snatch\w*|pull(?:ed|s|ing)?|drag(?:ged|s|ging)?|spit(?:s|ting)?|spat|"
    r"refus(?:e|ed|es|ing)|deprive\w*|neglect\w*|evict\w*|expel\w*|turn(?:ed|s|ing)?\s+(?:\w+\s+){1,2}out|"
    r"stop(?:ped|s|ping)|restrain\w*|restrict\w*|throttl\w*|hurt\w*|injur\w*|curs(?:e|ed|es|ing)|shout\w*|yell\w*)\b",
    re.I,
)
_BRIDGE_WORDS = {
    "allegedly",
    "also",
    "then",
    "thereafter",
    "used",
    "to",
    "would",
    "had",
    "has",
    "have",
    "started",
    "began",
    "repeatedly",
    "always",
    "regularly",
    "again",
    "further",
    "even",
    "often",
    "continuously",
    "subsequently",
    "immediately",
    "himself",
    "herself",
    "was",
    "were",
    "being",
    "been",
    "not",
    "never",
    "did",
    "do",
    "does",
    "so",
    "soon",
    "later",
    "habitually",
    "constantly",
    "frequently",
    "openly",
    "physically",
    "mentally",
    "only",
    "just",
    "all",
    "the",
    "a",
    "an",
    "kept",
    "continued",
    "will",
    "shall",
    "may",
    "might",
    "could",
    "can",
    "and",
    "who",
    "which",
    "that",
    "when",
}
_CONNECTOR = re.compile(r"^\W*(?:,|and|&|/|along\s*with|alongwith|together\s+with|with|and\s+also)?\W*$", re.I)
_PASSIVE_BEFORE_VERB = re.compile(
    r"\b(?:was|were|been|being)\s+(?:(?:also|then|allegedly|repeatedly|physically|mentally|often|again)\s+)*$", re.I
)
_AGENT = re.compile(r"^\s*(?:\w+\s+){0,6}?by\s+", re.I)


def _canon(s: str) -> str:
    """Spelling-tolerant canonical form: lowercase alphanumeric tokens, 'no/nos/number' folded to 'no'."""
    toks = re.findall(r"[a-z0-9]+", s.lower())
    return " ".join("no" if t in ("no", "nos", "number", "numbers") else t for t in toks)


def _alias_regex(alias: str) -> re.Pattern[str]:
    toks = re.findall(r"[A-Za-z0-9]+", alias)
    parts = [r"(?:no|nos|number|numbers)\.?" if t.lower() in ("no", "nos", "number", "numbers") else re.escape(t) for t in toks]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\W_]*".join(parts) + r"(?![A-Za-z0-9])", re.I)


def _overlaps(a: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(a[0] < t[1] and t[0] < a[1] for t in taken)


def extract_candidates(quote: str, accused: AccusedRef) -> list[Candidate]:
    """Every actor-like expression in `quote`, with offsets, roles and identities, longest/most specific first on overlaps."""
    found: list[Candidate] = []
    taken: list[tuple[int, int]] = []

    def add(m: re.Match[str], role: str, identity: str) -> None:
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            return
        taken.append(span)
        found.append(Candidate(m.start(), m.end(), m.group(0), role, identity))

    for m in _RANGE.finditer(quote):
        add(m, "collective", "COLLECTIVE")
    for alias in sorted(accused.aliases, key=len, reverse=True):
        for m in _alias_regex(alias).finditer(quote):
            add(m, "self", "SELF")
    for n, grp in enumerate(accused.other_parties):
        for alias in sorted(grp, key=len, reverse=True):
            for m in _alias_regex(alias).finditer(quote):
                add(m, "other", f"OTHER:{n}")
    for pat in _COLLECTIVE:
        for m in pat.finditer(quote):
            add(m, "collective", "COLLECTIVE")
    for m in _KIN_SINGULAR.finditer(quote):
        add(m, "other", f"UNK:{_canon(m.group(0))}")
    for m in _NUMBERED.finditer(quote):
        add(m, "other", f"UNK:{_canon(m.group(0))}")
    for m in _SHORT_NUMBERED.finditer(quote):
        add(m, "other", f"UNK:{_canon(m.group(0))}")
    for pat in _GENERIC:
        for m in pat.finditer(quote):
            add(m, "generic", "GENERIC")
    for m in _PRON_THEY.finditer(quote):
        add(m, "collective", "COLLECTIVE")
    for m in _PRON_SINGLE.finditer(quote):
        add(m, "pronoun", "PRON")
    return sorted(found, key=lambda c: c.start)


def _bridge_ok(gap: str) -> bool:
    words = re.findall(r"[A-Za-z']+", gap)
    return len(words) <= 8 and all(w.lower() in _BRIDGE_WORDS for w in words) and not re.search(r"[.;:!?]", gap)


def _is_apposition(quote: str, c: Candidate, gap: str, named: Candidate) -> bool:
    """A singular kin phrase ("her husband") followed only by a comma and a NAMED party ("..., Accused No.2,") describes that
    person: it is not a second actor. Any "and"/"along with" or a second numbered party still makes the group collective."""
    return (
        named.role in ("self", "other")
        and bool(re.fullmatch(r"\s*,\s*", gap))
        and bool(_KIN_SINGULAR.fullmatch(quote[c.start : c.end]))
    )


def _group_before(quote: str, cands: list[Candidate], boundary: int) -> list[Candidate]:
    """The run of candidates ending right before `boundary` (a verb start), joined by connectors; [] if the nearest one is not
    adjacent."""
    before = [c for c in cands if c.end <= boundary]
    if not before:
        return []
    last = before[-1]
    if not _bridge_ok(quote[last.end : boundary]):
        return []
    group = [last]
    left = last.start  # where the group (plus any absorbed apposition) begins
    for c in reversed(before[:-1]):
        gap = quote[c.end : left]
        if _is_apposition(quote, c, gap, group[0]):
            left = c.start  # "her husband, Accused No.2, beat": the kin phrase describes the named person, not a second actor
            continue
        if _CONNECTOR.match(gap):
            group.insert(0, c)
            left = c.start
        else:
            break
    return group


def _group_after(quote: str, cands: list[Candidate], start: int) -> list[Candidate]:
    after = [c for c in cands if c.start >= start]
    if not after or quote[start : after[0].start].strip() != "":
        return []
    group = [after[0]]
    for c in after[1:]:
        if _CONNECTOR.match(quote[group[-1].end : c.start]):
            group.append(c)
        else:
            break
    return group


class _Pred:
    """Stands in for a regex match of the act verb when the predicate is read from the sentence instead of a lexicon."""

    def __init__(self, start: int, end: int, text: str) -> None:
        self._s, self._e, self._t = start, end, text

    def start(self) -> int:
        return self._s

    def end(self) -> int:
        return self._e

    def group(self, _: int = 0) -> str:
        return self._t


def _open_predicate(quote: str, cands: list[Candidate]) -> _Pred | None:
    """Baseline "open verb": the first word after the leading candidate group that is not a bridge word. Used only by the
    measurement baseline (`open_verbs=True`); never by default."""
    if not cands:
        return None
    group = [cands[0]]
    for c in cands[1:]:
        if _CONNECTOR.match(quote[group[-1].end : c.start]):
            group.append(c)
        else:
            break
    pos = group[-1].end
    for m in re.finditer(r"[A-Za-z']+", quote[pos:]):
        if m.group(0).lower() not in _BRIDGE_WORDS:
            return _Pred(pos + m.start(), pos + m.end(), m.group(0))
    return None


def _decide(quote: str, accused: AccusedRef, cands: list[Candidate], open_verbs: bool = False) -> AttributionResult:
    base: dict[str, Any] = {
        "n_candidates": len(cands),
        "candidates": tuple(
            {"text": c.text, "start": c.start, "end": c.end, "role": c.role, "identity": c.identity} for c in cands
        ),
    }
    vm: Any = _VERB.search(quote)
    if open_verbs:  # baseline: an earlier open predicate wins over a later lexicon verb ("pinched and slapped")
        op = _open_predicate(quote, cands)
        if op is not None and (vm is None or op.start() < vm.start()):
            vm = op
    if vm is None:
        return AttributionResult(False, REASON_UNRESOLVED, rule="R1", **base)
    verb = vm.group(0)
    group: list[Candidate] = []
    passive = bool(_PASSIVE_BEFORE_VERB.search(quote[: vm.start()]))
    if passive:
        m = _AGENT.match(quote[vm.end() :])
        if m:
            group = _group_after(quote, cands, vm.end() + m.end())
    if not group:
        group = _group_before(quote, cands, vm.start()) if not passive else []
    if not group:
        return AttributionResult(False, REASON_UNRESOLVED, rule="R1", verb=verb, **base)
    span_start, span_end = group[0].start, group[-1].end
    common = {"verb": verb, "actor_span": quote[span_start:span_end], "actor_start": span_start, "actor_end": span_end, **base}
    ids = {c.identity for c in group}
    if any(c.role in ("collective", "generic") for c in group) or len(ids) > 1:
        return AttributionResult(False, REASON_COLLECTIVE, rule="R3", **common)
    only = group[0]
    if only.role == "pronoun":
        return AttributionResult(False, REASON_UNRESOLVED, rule="R4", **common)
    if only.role == "self":
        return AttributionResult(True, None, rule=None, **common)
    return AttributionResult(False, REASON_NOT_MATCHED, rule="R2", **common)


# -- hard vetoes (apply whatever the actor rules say) ---------------------------------------------------------------------
#: V2: a range written with short aliases ("A-9 to A-17", "A1-A5", "P.2 and P.4") before the first act verb is a collective actor.
_SHORT_RANGE = re.compile(
    r"(?<![A-Za-z0-9])[AP][\s.\-]*\d{1,3}\s*(?:to|[-\u2013\u2014]|and|&|,)\s*[AP]?[\s.\-]*\d{1,3}(?![A-Za-z0-9])", re.I
)
#: V1: after the accused, "together with / along with / alongwith" followed by a name (capitalised word), a role or a short alias.
_JOINED = re.compile(r"\b(?:together|along)[\s-]*with\s+(?:[A-Z][a-z]+|(?i:accused\b|petitioners?\b)|(?i:[AP])[\s.\-]*\d)")
#: V1: "and" / "&" straight after the accused and before the act verb, followed by a capitalised name.
_AND_NAME = re.compile(r"^\s*,?\s*(?:and|&)\s+(?!Accused\b|Petitioner|Respondent|Complainant)[A-Z][a-z]+")
_SENTENCE_END = re.compile(r"(?<!\bNo)(?<!\bNos)\.\s+(?=[A-Z])")


def _veto(quote: str, accused: AccusedRef) -> str | None:
    """`"V1"` / `"V2"` when a hard veto fires, else None. Deterministic; it overrides any pass or other refusal reason."""
    vm = _VERB.search(quote)
    before = quote[: vm.start()] if vm else quote
    if _SHORT_RANGE.search(before):
        return "V2"
    selfs = [c for c in extract_candidates(quote, accused) if c.role == "self"]
    if not selfs:
        return None
    end = selfs[0].end
    clause = _SENTENCE_END.split(quote[end:], maxsplit=1)[0]
    if _JOINED.search(clause):
        return "V1"
    if vm is not None and vm.start() > end and _AND_NAME.match(quote[end : vm.start()]):
        return "V1"
    return None


def check_attribution(quote: str | None, accused: AccusedRef | None, *, open_verbs: bool = False) -> AttributionResult:
    """R0..R5 on one quoted fact, then the hard vetoes. Never raises: any error is a refusal (R5)."""
    if accused is None:
        return AttributionResult(False, REASON_NOT_SPECIFIED, rule="R0")
    try:
        if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS:
            return AttributionResult(False, REASON_UNRESOLVED, rule="R1", error="empty or oversized quote")
        res = _decide(quote, accused, extract_candidates(quote, accused), open_verbs)
        # V2 (a short-alias range) is collective whatever else was found; V1 turns a pass or an unresolved refusal into a
        # collective refusal, while a specific R2/R3 refusal keeps its own rule.
        veto = _veto(quote, accused)
        if veto == "V2" or (veto == "V1" and (res.passed or res.reason == REASON_UNRESOLVED)):
            return AttributionResult(False, REASON_COLLECTIVE, rule=veto)
        return res
    except Exception as e:  # noqa: BLE001 -- fail closed: an error is a refusal, never a pass
        return AttributionResult(False, REASON_UNRESOLVED, rule="R5", error=f"{type(e).__name__}: {e}"[:300])


# -- the judge wrapper -----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AttributedJudgeRequest(JudgeRequest):
    """A `JudgeRequest` that also carries the accused under review. The agent builds this class ONLY when the feature is on and
    the
    element requires an actor, so with the feature off no request, audit record or result differs from before the feature
    existed."""

    accused: AccusedRef | None = None
    requires_actor: bool = False


class AccusedAttributionJudge:
    """Wraps a `Judge` (placed right after `SpanRelevanceJudge`). Asked only when the wrapped judge says established on a
    non-denial
    element that requires an actor and has a resolvable span; never changes the status. The result is attached as
    `ElementJudgment.attribution`; a refusal is `attribution["passed"] is False` and the agent turns it into REFER_TO_LAWYER."""

    def __init__(self, inner: Judge, *, name: str | None = None, selector: Any = None) -> None:
        self.inner = inner
        self.selector = selector  # None = D0; an `nyaya_attribution_m1.ActorSelector` = M1
        self.name: str = name or str(getattr(inner, "name", "accused_attribution"))

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        judgment = self.inner.judge(request)
        if not getattr(request, "requires_actor", False) or request.is_denial or judgment.status != "established":
            return judgment
        quote = (judgment.quote or "").strip()
        if not judgment.fact_id or not quote:
            return judgment  # no resolvable span: the quote check downstream rejects this anyway
        if self.selector is not None:
            from pravrudhi.application.nyaya_attribution_m1 import check_attribution_m1

            result = check_attribution_m1(quote, getattr(request, "accused", None), self.selector)
        else:
            result = check_attribution(quote, getattr(request, "accused", None))
        return replace(judgment, attribution=result.as_dict())
