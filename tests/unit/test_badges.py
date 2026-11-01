from datetime import UTC, datetime, timedelta

from prov.analyze.badges import decide, recommend
from prov.models import Badge, EvidenceItem, Level, Match, SideReport, Subject, Tier
from prov.store.records import new_record

T = datetime(2026, 10, 1, tzinfo=UTC)
SELF = Match(sha="a" * 40, branch="production", env="prod", level=Level.SELF, confidence=1.0, reason="origin commit")


def ev(kind, env, result, days=0, tier=Tier.recorded, via=SELF):
    rec = new_record(Subject(commit="a" * 40, tree="b" * 40), kind, env, result, at=T + timedelta(days=days))
    return EvidenceItem(record=rec, via=via, tier=tier, note="")


def side(evidence, text="x\n"):
    return SideReport(side="theirs", text=text, origins=[], matches=[], evidence=evidence, badge="UNVERIFIED", tier="none")


def test_prod_verified_recorded():
    assert decide(side([ev("deploy", "prod", "PASSED"), ev("test", "uat", "PASSED")]), Tier.recorded) == (
        Badge.PROD_VERIFIED, Tier.recorded)


def test_latest_failed_wins():
    s = side([ev("test", "uat", "PASSED", 0), ev("test", "uat", "FAILED", 1), ev("deploy", "prod", "PASSED")])
    assert decide(s, Tier.recorded)[0] == Badge.FAILED


def test_weak_match_never_prod_verified():
    weak = SELF.model_copy(update={"confidence": 0.7, "level": Level.L2})
    s = side([ev("deploy", "prod", "PASSED", via=weak), ev("test", "uat", "PASSED", via=weak)])
    assert decide(s, Tier.recorded)[0] == Badge.TESTED_ELSEWHERE


def test_inferred_only_never_recorded():
    inferred = EvidenceItem(record=None, via=SELF, tier=Tier.inferred, note="reachable from production")
    assert decide(side([inferred]), Tier.none) == (Badge.UNVERIFIED, Tier.inferred)
    assert decide(side([]), Tier.none) == (Badge.NO_METADATA, Tier.none)


def test_recommendation():
    good = side([])
    good.badge, good.tier = Badge.PROD_VERIFIED, Tier.recorded
    meh = side([], text="")
    meh.badge, meh.tier = Badge.UNVERIFIED, Tier.none
    assert recommend(meh, good)[0] == "take_theirs"
