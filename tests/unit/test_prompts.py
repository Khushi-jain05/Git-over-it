import re
from datetime import UTC, datetime

from prov.llm.prompts import DRAFT_SYSTEM, build_draft_prompt
from prov.llm.ratelimit import estimate_tokens
from prov.models import Badge, ConflictReport, HunkReport, SideReport, Tier


def _report():
    def side(name, badge):
        return SideReport(side=name, text=f"{name} = 1\n", origins=[], matches=[], evidence=[], badge=badge,
                          tier=Tier.none)

    h = HunkReport(id="big.py#0", path="big.py", index=0, start_line=1500, end_line=1506, content_hash="h",
                   base=side("base", Badge.UNVERIFIED), ours=side("ours", Badge.TESTED_ELSEWHERE),
                   theirs=side("theirs", Badge.UNVERIFIED), recommendation="manual", recommendation_reason="")
    rep = ConflictReport(operation="cherry-pick", ours_ref="a" * 40, theirs_ref="b" * 40, metadata_state=Tier.none,
                         files=[], generated_at=datetime.now(UTC))
    return rep, h


def test_context_shrinks_to_budget_on_3000_line_file():
    rep, h = _report()
    working = "".join(f"line_{i} = {'x' * 400}\n" for i in range(3000))
    user = build_draft_prompt(rep, h, working, "dev", "fix", [], 3500)
    assert estimate_tokens(DRAFT_SYSTEM + user) <= 3500


def test_prompt_claims_only_report_badges():
    rep, h = _report()
    user = build_draft_prompt(rep, h, "x\n" * 3000, "dev", "fix", [], 3500)
    named = set(re.findall(r"PROD_VERIFIED|TESTED_ELSEWHERE|UNVERIFIED|FAILED|NO_METADATA", user))
    assert named <= {h.ours.badge.value, h.theirs.badge.value}
    assert "no test evidence" in user
