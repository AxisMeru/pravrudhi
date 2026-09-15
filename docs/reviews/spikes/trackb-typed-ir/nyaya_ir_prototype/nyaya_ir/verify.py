"""Closed finite-world diagnostic, separate from legal-rules-v1."""
from dataclasses import dataclass
from .models import Verdict
@dataclass(frozen=True)
class Syllogism:
    paksa: str
    sadhya: str
    hetu: str
    sapaksa: str
    def __post_init__(self):
        if any(not isinstance(x,str) or not x.strip() for x in (self.paksa,self.sadhya,self.hetu,self.sapaksa)):
            raise ValueError('syllogism fields must be nonempty strings')
@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    counterexamples: tuple[str,...] = ()
    supporting: tuple[str,...] = ()
def evaluate(world, syllogism):
    s=syllogism
    if s.hetu not in world.get(s.paksa,()): return VerificationResult(Verdict.ASIDDHA)
    bad=tuple(sorted(k for k,v in world.items() if s.hetu in v and s.sadhya not in v))
    both=tuple(sorted(k for k,v in world.items() if s.hetu in v and s.sadhya in v))
    if bad: return VerificationResult(Verdict.SAVYABHICARA if both else Verdict.VIRUDDHA,bad)
    if s.sapaksa==s.paksa or s.sapaksa not in both: return VerificationResult(Verdict.APRASIDDHA)
    return VerificationResult(Verdict.VALID,supporting=tuple(k for k in both if k!=s.paksa))
