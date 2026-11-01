"""L3: fuzzy diff similarity + AST hashes + range-diff style global assignment (docs/04 §L3)."""

from __future__ import annotations

import difflib
import hashlib
import os
import re

from prov.gitio import Repo

TOKEN = re.compile(r"\w+|[^\w\s]")
MAX_TOKENS = 3000  # ponytail: SequenceMatcher(autojunk=False) is quadratic; huge diffs are L1/L2 territory anyway

# extension -> (tree-sitter language, enclosing node types)
LANGS = {
    ".py": ("python", {"function_definition", "class_definition"}),
    ".js": ("javascript", {"function_declaration", "method_definition", "class_declaration"}),
    ".jsx": ("javascript", {"function_declaration", "method_definition", "class_declaration"}),
    ".ts": ("typescript", {"function_declaration", "method_definition", "class_declaration"}),
    ".tsx": ("tsx", {"function_declaration", "method_definition", "class_declaration"}),
    ".java": ("java", {"method_declaration", "class_declaration"}),
}

_parsers: dict[str, object] = {}


def _parser(lang: str):
    if lang not in _parsers:
        try:
            from tree_sitter_language_pack import get_parser

            _parsers[lang] = get_parser(lang)
        except Exception:  # tree-sitter not installed or grammar missing: no AST feature
            _parsers[lang] = None
    return _parsers[lang]


def supported(path: str) -> bool:
    ext = os.path.splitext(path)[1]
    return ext in LANGS and _parser(LANGS[ext][0]) is not None


def _norm_node(node, src: bytes) -> str:
    """Leaf tokens without comments, space-joined: comment- and whitespace-insensitive."""
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if "comment" in n.type:
            continue
        if n.child_count == 0:
            out.append(src[n.start_byte : n.end_byte].decode("utf-8", "replace"))
        else:
            stack.extend(reversed(n.children))
    return " ".join(out)


def compute_ast(repo: Repo, sha: str, changes: list[dict]) -> list[tuple[str, str, str]]:
    """-> [(path, func name, ahash)] for the smallest function/class enclosing each changed hunk (post-image)."""
    paths = sorted({h["path"] for h in changes if h["path"] and supported(h["path"])})
    blobs = repo.cat_file_batch([f"{sha}:{p}" for p in paths])
    out = set()
    for path in paths:
        src = blobs.get(f"{sha}:{path}")
        if src is None:
            continue
        lang, kinds = LANGS[os.path.splitext(path)[1]]
        tree = _parser(lang).parse(src)
        for h in changes:
            if h["path"] != path:
                continue
            row = max(h["new_start"] - 1, 0)  # 0-based
            node = tree.root_node.descendant_for_point_range((row, 0), (row + max(h["new_len"] - 1, 0), 0))
            while node is not None and node.type not in kinds:
                node = node.parent
            if node is None:
                continue
            name_node = node.child_by_field_name("name")
            name = src[name_node.start_byte : name_node.end_byte].decode() if name_node else ""
            out.add((path, name, hashlib.sha1(_norm_node(node, src).encode()).hexdigest()))
    return sorted(out)


def _tokens(changes: list[dict]) -> list[str]:
    toks = []
    for h in changes:
        for sign, lines in (("-", h["minus"]), ("+", h["plus"])):
            for ln in lines:
                toks += [sign + t for t in TOKEN.findall(ln)]
                if len(toks) >= MAX_TOKENS:
                    return toks[:MAX_TOKENS]
    return toks


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def features(idx, x: str, c: str) -> dict[str, float]:
    tok = difflib.SequenceMatcher(None, _tokens(idx.changes(x)), _tokens(idx.changes(c)), autojunk=False).ratio()
    fn = _jaccard(idx.funcs(x), idx.funcs(c))
    file = _jaccard({os.path.basename(p) for p in idx.files(x)}, {os.path.basename(p) for p in idx.files(c)})
    f = {"tok": tok, "fn": fn, "file": file}
    if any(supported(p) for p in idx.files(x)):
        ax, ac = idx.ast_hashes(x), idx.ast_hashes(c)
        f["ast"] = len(ax & ac) / len(ax) if ax else 0.0
    return f


def s3(f: dict[str, float]) -> float:
    if "ast" in f:
        return 0.50 * f["tok"] + 0.20 * f["fn"] + 0.10 * f["file"] + 0.20 * f["ast"]
    return 0.70 * f["tok"] + 0.20 * f["fn"] + 0.10 * f["file"]  # unsupported language: AST weight -> tok


def reason(f: dict[str, float]) -> str:
    ast = f", AST {f['ast']:.2f}" if "ast" in f else ""
    return f"similar diff: tokens {f['tok']:.2f}, functions {f['fn']:.2f}{ast}"


def prefilter(idx, x: str, candidates: set[str]) -> set[str]:
    """Candidates sharing a touched basename or an enclosing function name with X."""
    names = {os.path.basename(p) for p in idx.files(x)}
    funcs = idx.funcs(x)
    return {c for c in candidates if names & {os.path.basename(p) for p in idx.files(c)} or funcs & idx.funcs(c)}


def assign(scores: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """One-to-one assignment maximising total score (range-diff style). scores: {(source, cand): s3}"""
    if not scores:
        return {}
    srcs = sorted({s for s, _ in scores})
    cands = sorted({c for _, c in scores})
    try:
        from scipy.optimize import linear_sum_assignment

        cost = [[1 - scores.get((s, c), 0.0) for c in cands] for s in srcs]
        rows, cols = linear_sum_assignment(cost)
        pairs = [(srcs[r], cands[k]) for r, k in zip(rows, cols, strict=True)]
    except ImportError:  # ponytail: greedy fallback when scipy is absent; optimal for the typical 1-source case
        pairs, used_s, used_c = [], set(), set()
        for (s, c), _ in sorted(scores.items(), key=lambda kv: -kv[1]):
            if s not in used_s and c not in used_c:
                pairs.append((s, c))
                used_s.add(s)
                used_c.add(c)
    return {p: scores[p] for p in pairs if p in scores}


CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs", ".c", ".h", ".cc", ".cpp", ".cs",
            ".kt", ".swift", ".php", ".scala"}
BEHAVIOUR_TOKEN = re.compile(r"\d[\w.]*|\w+|==|!=|<=|>=|\*\*|//|<<|>>|[<>+\-*/%&|^~]|\S")
OPERATORS = {"==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/", "%", "**", "//", "<<", ">>", "&", "|", "^", "~",
             "and", "or", "not", "is", "&&", "||"}
CONSTANTS = {"True", "False", "None", "true", "false", "null", "nil"}


MANIFEST = re.compile(r"^(requirements.*\.txt|.*\.toml|setup\.cfg|package(-lock)?\.json|.*\.lock|go\.(mod|sum)|Gemfile"
                      r"|pom\.xml|build\.gradle(\.kts)?|Cargo\.toml|Pipfile)$")  # dependency pins change behaviour too
GITLINK = "Subproject commit "  # a submodule pin, as `git diff` prints it


def _behaviour_tokens(changes: list[dict], names: set[str]):
    from collections import Counter

    out: Counter = Counter()
    for h in changes:
        base = os.path.basename(h["path"])
        if base not in names:
            continue
        code = os.path.splitext(h["path"])[1] in CODE_EXT or MANIFEST.match(base)
        for ln in h["minus"] + h["plus"]:
            if ln.startswith(GITLINK):
                out[ln.strip()] += 1  # the pinned commit itself is the behaviour
            elif code:
                out.update(BEHAVIOUR_TOKEN.findall(ln))
    return out


def behaviour_differs(idx, x: str, c: str) -> list[str]:
    """Literals/operators that one code diff has and the other lacks (`qty <= 9` vs `qty <= 8`).
    Renamed identifiers, plain assignment, punctuation, strings and formatting do not count.
    Only files both diffs touch are compared: a partial backport leaves the other files out on purpose."""
    cx, cc = idx.changes(x), idx.changes(c)
    names = {os.path.basename(h["path"]) for h in cx} & {os.path.basename(h["path"]) for h in cc}
    tx, tc = _behaviour_tokens(cx, names), _behaviour_tokens(cc, names)
    extra = (tx - tc) + (tc - tx)
    return sorted(t for t in extra if t[0].isdigit() or t in OPERATORS or t in CONSTANTS or t.startswith(GITLINK))


CALL = re.compile(r"([A-Za-z_][\w]*)\s*\(")
DEF_KW = r"(def|function|func|fn|class)"  # POSIX ERE (git grep): plain groups only
_defs: dict[tuple[str, tuple[str, ...]], set[str]] = {}


def _defined(repo, tree: str, names: tuple[str, ...]) -> set[str]:
    key = (tree, names)
    if key not in _defs:
        pat = rf"(^|[^[:alnum:]_]){DEF_KW}[[:space:]]+({'|'.join(names)})([^[:alnum:]_]|$)"  # POSIX ERE: no \b, \s
        out = repo.run(["grep", "-h", "-o", "-E", pat, tree, "--"], check=False)
        _defs[key] = {m.group(2) for m in re.finditer(rf"{DEF_KW}\s+(\w+)", out)}
    return _defs[key]


def missing_prereqs(idx, x: str, c: str) -> list[str]:
    """Functions X's added code calls that C's tree defines but X's tree does not: C was tested with a helper
    that X never got (s20), so C's tests say nothing about X."""
    xr, cr = idx.commit(x), idx.commit(c)
    if xr is None or cr is None or xr["tree"] == cr["tree"]:
        return []
    called = tuple(sorted({n for h in idx.changes(x) if os.path.splitext(h["path"])[1] in CODE_EXT
                           for ln in h["plus"] for n in CALL.findall(ln)}))
    if not called:
        return []
    return sorted(_defined(idx.repo, cr["tree"], called) - _defined(idx.repo, xr["tree"], called))
