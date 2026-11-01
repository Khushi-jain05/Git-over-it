# Benchmark results (actual output)

Ten adversarial Git-conflict benchmark repositories (`testing-1` … `testing-10`, each a small app with real history,
engineered merge hazards and synthetic provenance notes). Each scenario runs in a throwaway clone; the answer key is
removed from the clone so the tool never sees it. Command: `python bench/run_testing1.py --repo <testing-N>`.

| Repository | Result |
| --- | --- |
| testing-1 | scenarios 10/10 pass, checks 32/32 pass |
| testing-2 | scenarios 10/10 pass, checks 37/37 pass |
| testing-3 | scenarios 10/10 pass, checks 32/32 pass |
| testing-4 | scenarios 10/10 pass, checks 37/37 pass |
| testing-5 | scenarios 10/10 pass, checks 31/31 pass |
| testing-6 | scenarios 10/10 pass, checks 36/36 pass |
| testing-7 | scenarios 9/10 pass, checks 33/34 pass |
| testing-8 | scenarios 9/10 pass, checks 28/29 pass |
| testing-9 | scenarios 8/10 pass, checks 30/32 pass |
| testing-10 | scenarios 8/10 pass, checks 31/33 pass |

Remaining failures, with the reason (from the last run):

```text
testing-7  FAIL  S084  Provenance references that do not resolve to the real origin
testing-7          XX  human review: no one-sided recommendation   (got ['manual', 'take_ours'])
testing-8  FAIL  S010  Function deleted and later recreated elsewhere
testing-8          XX  equivalent: 285bcb0f ~ 32ced7e8   (got no match)
testing-9  FAIL  S035  Only the parent commit was tested
testing-9          XX  partial: 4509bd7a ~ ebf46438   (got no match)
testing-9  FAIL  S094  Case-only rename and Unicode file names
testing-9          XX  conflicted files   (got [])
testing-10  FAIL  S066  Revert of a tuned rule collides with a later edit to the same tier
testing-10          XX  reverted: c84838db ~ ce68f1da (revert detected)   (reverts=[])
testing-10  FAIL  S051  Second history with a hand-resolved revert
testing-10          XX  reverted: c84838db ~ ce68f1da (revert detected)   (reverts=[])
```

- testing-10 S066/S051: the "revert" is an empty commit whose message names a subject that does not exist; nothing in the repository links it to the reverted commit, so `reverts=[]` is the factual answer.
- testing-8 S010: the recreated function is a rewrite (same behaviour, different code); text-based matching cannot see it, only the optional LLM judge could.
- testing-9 S094: case-only rename + Unicode names cannot coexist on macOS's case-insensitive filesystem, so git reports no conflict there (the answer key itself was validated with `git merge-tree`).
- testing-9 S035: the child rewrites its parent's lines; git prov correctly gives it no evidence from the parent but does not list the pair as "partial".
- testing-7 S084: one hunk of an embedded sub-scenario gets an evidence-based `take_ours` (ours passed UAT, theirs' equivalent failed UAT) where the answer key wants human review for the whole file.
