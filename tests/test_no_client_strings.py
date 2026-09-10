"""This repo is public. No real company may appear in it, ever.

The failure this prevents actually happened elsewhere in this organisation: a
client's name went out in a *commit message*, which is published just as surely
as a file, and stayed public for six days. Force-pushing does not undo it,
because forks and caches keep the object.

So this test scans tracked file contents AND the commit log, and it fails on
figures as well as on names. A client's balance sheet identifies them as surely
as their name does: a set of exact cent-level amounts is a fingerprint, and
anyone holding the same export can match it.

THE FORBIDDEN LIST IS STORED AS DIGESTS

Writing the figures out here would put them back in the public repo, which is
the thing being prevented. So each forbidden token is stored as the SHA-256 of
its normalized form, and the scanner hashes every candidate token it finds and
looks for a match. That makes the guard publishable without publishing what it
guards.

To add one, pass the value and paste the line it prints into the right set:

    python3 tests/test_no_client_strings.py --digest "<the figure or the name>"

This file used to exempt itself from the file scan, which is how its own usage
example came to spell out a real figure in full. It no longer does. The digests
are stripped out and everything else here is scanned like any other file, so a
forbidden value written into this docstring fails this test.

It runs in CI and before every push. When it fails, the fix is never to weaken
the test.
"""

import contextlib
import hashlib
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# Words that only ever appear in this repo by mistake. This list is intentionally
# short and generic: the real, longer client list lives outside the public repo
# and is run over this tree separately, precisely so that publishing the guard
# does not publish the thing it guards.
FORBIDDEN = [
    r"\bmedianfi\.com/mcp\b",
    r"\brealm[\s_-]*id\s*[:=]?\s*\d{10,}\b",
    # Anything shaped like an Intuit company file id, whoever's it is. The
    # literal one this used to name has been replaced by its shape, because a
    # guard that spells out the identifier publishes the identifier.
    r"(?<!\d)\d{16}(?!\d)",
]

# SHA-256 of each forbidden money figure, normalized: no currency symbol, no
# thousands separators, no parentheses, no sign. Cent-exact amounts off a real
# balance sheet, a real trial balance and a real reconciliation.
FORBIDDEN_FIGURES = {
    "4146c73b987fbfc5fadbd946827df3c3813bf44b2fc950cd57d44709f326e961",
    "0669601dc8aea80b93aa200e3538d089ad55f9e8eedd24e81c3bb3e72615654f",
    "7cae3f2cabb60f59420ec6631b7c7b435eab379fe0403ef7c393980d622f2269",
    "538f29eb1b0e57288195bfa4b58a74ebcabd5089c44029d67441de16e5a1428b",
    "ca2dfc4c109b2992af2e3d40333dff4017a459ee6979600bcd0c084cabcd99eb",
    "77b5354ca625d129fbbcc11842b585c32128a8aa2639e6517588329049aa56f2",
    "808a9f3da2a00f95e4a0b6b2b72f48c509a8c405e0c4c3f6285b93928bd80bcd",
    "b391b5ab655e19ef3556e9784b182886fa7ff2897634830399ded6989aebaabb",
    "390e3413474f0e6e60062c76b738e1d64f6cd527c7af659d2745a1002188fb93",
    "080a7af61483d87492e699eadc76fbdcdc3c84b3e71826c826563175f48e5d21",
    "7b6107de0175e3536195d4381dd9660a0fe97d8647418b6abba11c5d108033e3",
    "cd8d4b200591182566cdcf76b723ff013730ad4b89e959b3c93bf9bd7b9074cc",
    "e1b299f7876a95a9b49adb58f53e0b8e11009dc2217876bb804c133e2d2d8d53",
    "d70515851e543ef2098686bfa505ec855a3c909c7f0d2ee2c0a8afef719866c7",
    "0fc22b0233f0aefb1c6517371eaa3a3d26f804f1e93de0d8841b2ecdd660ecc9",
    "7ae7ec80dbb373d73edd2d17b5e507d8a3574567dfab4cbf1f20ca8dafac2e7a",
    "1559a5ac688757f8f30928708b34a3732f6c17b02558c945f65e2c1fb326ff1c",
    "b36bb41d0e37528a4b787a736e43a9ebf57aa56df6ba34e69bd4dec747a2d75a",
    "e67c085a59cea584220bd15da03881ad7aef3313763fa3ee869bc87c2ca6e94b",
    "6428003a39683216d94339c323f18cfb9d75d999b8dcf429729bc315735bda2f",
    "e972e929f121cba640f610e8b8739e087b9b58ef4118d9f7043c87c00a410cdf",
    "2db423c1c7d8613afe56e2f2ae404b0d6d0aadb5923325788e44da03aafbe898",
    "581a485837a890f451dba87d829093606271180d0a105015addb9518c57f8a9c",
    "a1bffc3f7f4c00132a082079f5dd90b698940538721c16ae788faec9ed1d7f21",
    "72d42218420ccea8ddbd4e15c8274b684aa2293be82d005581deb523a831c3ac",
    "d0d0a254330e2fac69d45d798443229cdf442eb85241475230dc314c3b37af1c",
    "030837969a14c9128ef3ef3ed60a9da39ee07cb3af2b0d87875dabac252ac658",
    "50da093b86218e821d44355c340be6ce5ebf57cc57506df2e4a22f15e3bbd80b",
    "833a78c432933d0e29585549ccdb7a6d04d3603fb211c6ebc77511316e85b9f3",
    "828bd764141f9d925a5a038825cca2b034531e669624f9c3be4aefbe8e22fd6d",
    "0d1d8327903928f53417a0f2872aa2bd2843e83dfab1751a81bd087b8ef03071",
    "98ce1c2c46df500d0a167e17953bc8d5bd7501a0bd1e6c445d38a35afb1b3c3e",
    "cfe6f86236b432f5497b838cbda7d93de30d97705026e4adbc719084e63956b1",
    "599d9f1b778187d42a504f3be2f28ad8a81cf61bd5a56374ac1d70fc7a775fb3",
    "522b5b7196eb6d49871222c8ace8883e5d39b4f9360aa897fe86d54edd9ed8f6",
    "02b38bc70c6f42d952290719cc461ff6d4d30a33aa1b0dd3b3c63505788e6e42",
    "e0fa85eeb101b17ad7855c810efcee624cd39a720589eeb6c6d663ce2064954f",
    "07097e088c63b9118696778638352e18935e00cd8f94ac7515ceef030e251924",
    "612de66bd9774bb3447cb038530c63db31f8b14a7fa207401ad8e15870f835d9",
    "318f04cb29fa881a01cdf4753812d2034d6cbe39a7dea8c8bfe7c13d7b58e581",
    "6608af93d1dc5590ebbec41ddb8e207a958a5de542c88156797b11254dfa5d6f",
    "7c9c034a7cb192d2a62543ff3f8c8e2f4003bf498001999ccc53fc71e15e1dea",
    "7b2985d9daeb6a493bfc544375b67c5ec1af689828858dc31651746201bf10cb",
    "92a57c134592648365fe67702f90c1117b644c952b0adecdc8306ee272ae1b7f",
    "1e5b1f47adc2af40f3f2e4c7df00bbd38222cd199b54ad374be820fb7e24e0c8",
}

# The counts off the same file: a queue depth and its partition. Checked against
# whole integer tokens only, so a figure that merely contains one of them does
# not fire. Deliberately excluded as too ordinary to guard on: any count under
# three digits, and round hundreds that any fixture might reach for.
FORBIDDEN_COUNTS = {
    "4779bc407343d916c5a4f6a996174046419bdd87ce6c609054dc8788b3dfc233",
    "02e6295d8f522840f09b5194b3f023799ad6ed3306d9296005787e792224df20",
    "068814875fcdfb8faf539ef43cf5d109a22b7cfd28770e90b00be8c48bfc722f",
    "5f193b350c8aba4883dedf97367ef3080821470661d0a2e1faf420a300cb5ca8",
    "f8809aff4d69bece79dabe35be0c708b890d7eafb841f121330667b77d2e2590",
    "1bde41ce9b4fccbf7dde0dc315d1aea5fa03f78c56feb1ba744be9e37fab2dce",
    "8e28c5eb829e92abf7a5a921f42364cbb8b255d7c9861a68a3814a9de95d9d67",
    "377adeb4cd4096adc7ca64b533938cffc6294a9b3534f883b2336a26252cda9a",
}

# Names and name pairs, lowercased. Word and two-word tokens are hashed the same
# way, so a two-word company name is caught without the halves firing alone.
FORBIDDEN_NAMES = {
    "6e649ccc4f5484fa486efbfcd59ffc2f2def03a71fa8376a96a0b6826d49677d",
    "707aea90b10155cc96266d1976176ad8d5c567bc635d628b584a0df7ebb24968",
    "32aaaf42c7b7473c747993b04a89d8822dc72d0bfde526e830f6df0df7e787ce",
    "23a1c9b75f01454f7a8ee2e5c6894711ca0dcfd36ab98b9e91c36542ac446530",
    "7ce6fb2138746d68583f3863a7f5833f8610cd6cd5138d1d761783c4d29fe666",
    "48f6622f4344c8c359277d477d833be669f4f09cc151b7b7990801e0de21474e",
}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".csv", ".toml", ".cfg", ".sh", ""}

MONEY_TOKEN = re.compile(r"\d[\d,]*\.\d{2}")
INT_TOKEN = re.compile(r"(?<![\d.,])\d{3,}(?![\d.,])")
WORD_TOKEN = re.compile(r"[A-Za-z][A-Za-z'-]+")


def digest(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def normalize_number(token) -> str:
    return token.replace(",", "").replace("$", "").strip("()").lstrip("-")


def offending(text) -> list:
    """Every forbidden token in `text`, as (kind, token).

    Three vocabularies, because a figure, a count and a name are found in
    different shapes and a single regex over all of them would either miss or
    over-fire.
    """
    hits = []
    for match in MONEY_TOKEN.finditer(text):
        token = normalize_number(match.group(0))
        if digest(token) in FORBIDDEN_FIGURES:
            hits.append(("figure", match.group(0)))
    for match in INT_TOKEN.finditer(text):
        token = normalize_number(match.group(0))
        if digest(token) in FORBIDDEN_FIGURES or digest(token) in FORBIDDEN_COUNTS:
            hits.append(("count", match.group(0)))
    words = [(m.group(0).lower(), m.group(0)) for m in WORD_TOKEN.finditer(text)]
    for i, (lower, raw) in enumerate(words):
        if digest(lower) in FORBIDDEN_NAMES:
            hits.append(("name", raw))
        if i + 1 < len(words) and digest(f"{lower} {words[i + 1][0]}") in FORBIDDEN_NAMES:
            hits.append(("name", f"{raw} {words[i + 1][1]}"))
    for pattern in FORBIDDEN:
        for match in re.finditer(pattern, text, re.I):
            hits.append(("pattern", match.group(0)))
    return hits


def tracked_files():
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split("\n")
        files = [REPO / f for f in out if f.strip()]
        if files:
            return files
    except Exception:
        pass
    files = []
    for p in REPO.rglob("*"):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.parts):
            files.append(p)
    return files


def test_no_client_strings_in_files():
    hits = []
    for path in tracked_files():
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if path.name == "test_no_client_strings.py":
            # Scanned like everything else, with only the digests removed. A
            # digest is a 64-character hex string and can carry sixteen
            # consecutive digits by chance, which is the one pattern here that
            # would otherwise fire on it.
            text = re.sub(r"\b[0-9a-f]{64}\b", "", text)
        for kind, token in offending(text):
            line = text[: text.index(token)].count("\n") + 1 if token in text else 0
            hits.append(f"{path.relative_to(REPO)}:{line}: {kind} {token!r}")
    assert not hits, "client data in a public repo:\n  " + "\n  ".join(hits)


def test_no_client_strings_in_commit_messages():
    """A commit message is published exactly as surely as a file is."""
    try:
        log = subprocess.run(
            ["git", "log", "--all", "--format=%H%n%s%n%b"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
    except Exception:
        return  # no history yet
    hits = sorted({f"{kind} {token!r}" for kind, token in offending(log)})
    assert not hits, (
        "client data in a commit message (published even if the file is not):\n  "
        + "\n  ".join(hits)
    )


@contextlib.contextmanager
def _also_forbidding(vocabulary, value):
    """Teach the guard one extra secret, for the length of a test.

    Digests are compared, never plaintext, so this adds a digest exactly the way
    a real entry is added and removes it afterwards.
    """
    d = digest(value.lower())
    vocabulary.add(d)
    try:
        yield
    finally:
        vocabulary.discard(d)


def test_the_guard_actually_fires():
    """A guard nobody has seen fail is a guard nobody knows works.

    The obvious way to test this is to write a real forbidden value here and
    watch the guard catch it. That defeats the purpose: the value would then be
    published in the guard, which is exactly what happened to an earlier version
    of this file. Reconstructing it from arithmetic is no better, because the
    digits are still on the page for anyone who greps.

    So the test plants its own secret instead. It adds the digest of a string
    that means nothing to anyone, proves the guard catches that string, and
    proves it lets an ordinary sentence through. That exercises the whole
    mechanism, hash and match and report, while this file stays free of anything
    worth hiding.
    """
    canary = "quokkalantern"
    with _also_forbidding(FORBIDDEN_NAMES, canary):
        hits = offending(f"a mention of {canary} in passing")
        assert any(k == "name" for k, _ in hits), "the guard did not catch a planted string"
    # Outside the block the planted digest is gone, so the guard forgets it.
    assert not offending(f"a mention of {canary} in passing")
    # And an ordinary sentence never trips it.
    assert not offending("a balance of 3,118,447.25 sits there")
    assert not offending("there are 863 items")


def test_no_profiles_are_tracked():
    tracked = {p.name for p in tracked_files()}
    leaked = [n for n in tracked if n.endswith(".local.json") or n.endswith(".APPROVED")]
    assert not leaked, f"a local profile or approval is tracked by git: {leaked}"


def test_engine_makes_no_network_calls():
    """'Sends nothing anywhere' has to be true, not just claimed in the README."""
    banned = re.compile(
        r"^\s*(?:import|from)\s+(requests|urllib|http\.client|httpx|socket|aiohttp|ftplib|smtplib|telnetlib)\b",
        re.M,
    )
    hits = []
    for path in (REPO / "lib").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in banned.finditer(text):
            hits.append(f"{path.relative_to(REPO)}: {m.group(0).strip()}")
    assert not hits, "the engine imports a network library:\n  " + "\n  ".join(hits)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--digest":
        value = sys.argv[2]
        normalized = normalize_number(value) if any(c.isdigit() for c in value) \
            else " ".join(value.lower().split())
        print(f'    "{digest(normalized)}",   # {len(normalized)} characters')
        return 0
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
