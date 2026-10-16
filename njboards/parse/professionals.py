"""Find licensed professionals (engineers, planners, attorneys, architects,
surveyors, traffic consultants...) in minutes / resolutions / letters, and
tell applicant-side witnesses from board-side professionals."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..util import sentences, squash

CRED = (r"P\.?\s?E\.?|P\.?\s?P\.?|Esq(?:uire)?\.?|A\.?I\.?A\.?|R\.?A\.?|P\.?L\.?S\.?|L\.?S\.?|C\.?M\.?E\.?|L\.?L\.?A\.?|R\.?L\.?A\.?|"
        r"AICP|PTOE|PTP|CFM|LEED\s*AP|CPWM|ASLA|NJLA|CLA|CPESC|LSRP|PWS|CPG|P\.?G\.?|CCM|NCARB|MBA|Ph\.?D\.?|CPA|RPA|MAI|SCGREA|CTA|PhD")
CRED_LIST = re.compile(rf"\b({CRED})(?:\s*[,/&]\s*(?:{CRED}))*", re.I)
NAME = r"(?:(?:Mr|Ms|Mrs|Dr)\.?\s+)?([A-Z][a-zA-Z'\-]+(?:\s+(?:[A-Z]\.?|[A-Z][a-zA-Z'\-]+))?(?:\s+(?:[A-Z][a-zA-Z'\-]+|O'[A-Z][a-z]+|Mc[A-Z][a-z]+|De[A-Z][a-z]+|Van\s[A-Z][a-z]+|St\.\s[A-Z][a-z]+))(?:,?\s+(?:Jr|Sr|II|III|IV)\.?)?)"
NAME_CRED = re.compile(rf"{NAME}\s*,\s*((?:{CRED})(?:\s*[,/&]\s*(?:{CRED}))*)(?![a-z])")

ROLE_WORDS = (r"traffic\s+(?:engineer|consultant|expert)|civil\s+engineer|professional\s+engineer|licensed\s+engineer|consulting\s+engineer|"
              r"engineer|professional\s+planner|licensed\s+planner|planner|architect|landscape\s+architect|attorney|counsel|esquire|"
              r"surveyor|environmental\s+(?:consultant|scientist|expert)|wetlands?\s+(?:consultant|scientist)|"
              r"geotechnical\s+engineer|stormwater\s+(?:engineer|expert)|acoustical\s+(?:engineer|consultant)|noise\s+expert|"
              r"real\s+estate\s+(?:appraiser|expert)|appraiser|fiscal\s+(?:expert|consultant)|economist|hydrogeologist|"
              r"fire\s+(?:protection|safety)\s+(?:engineer|expert|consultant)|lighting\s+(?:designer|consultant|expert)|"
              r"signage\s+expert|arborist|expert\s+witness|expert|consultant|secretary|administrator|clerk|zoning\s+officer|construction\s+official")
BOARD_SIDE = re.compile(
    rf"\b(?:board|board'?s|township|township'?s|borough|borough'?s|city|city'?s|town|town'?s|village|municipal|municipality'?s|"
    rf"the\s+board'?s|conflict|special|alternate)\s+(?:conflict\s+|special\s+|consulting\s+|professional\s+|licensed\s+|traffic\s+|landscape\s+|environmental\s+)?({ROLE_WORDS})\b", re.I)
APPLICANT_SIDE = re.compile(
    rf"\b(?:applicant'?s?|appellant'?s?|objector'?s?|developer'?s?|owner'?s?|on\s+behalf\s+of|for\s+the\s+applicant|representing|"
    rf"retained\s+by|project)\s+(?:traffic\s+|professional\s+|licensed\s+|civil\s+|consulting\s+|landscape\s+|environmental\s+)?({ROLE_WORDS})\b", re.I)
ANY_ROLE = re.compile(rf"\b(?:traffic\s+|professional\s+|licensed\s+|civil\s+|consulting\s+|landscape\s+|environmental\s+)?({ROLE_WORDS})\b", re.I)
TESTIMONY = re.compile(r"\bsworn\b|\btestif|\btestimony|\bwitness|\bqualified\s+as\b|\baccepted\s+as\s+an?\s+expert|\bcredentials|\bpresented\b|\bopined|\bexpert\b|\bunder\s+oath|\bduly\s+sworn|\bwas\s+called\b|\bappeared\s+on\s+behalf", re.I)
FIRM = re.compile(r"\b(?:of|with|from)\s+((?:[A-Z][\w&.'\-]*\s*){1,6}(?:LLC|L\.L\.C\.|Inc\.?|P\.?C\.?|P\.?A\.?|LLP|L\.L\.P\.|Associates|Assoc\.?|Group|Engineering|Engineers|Consultants|Consulting|Partners|Architects|Architecture|Planning|Surveying|Design|Company|Co\.|Corp\.?|Esqs?\.?|Law|& [A-Z][\w]+))\b")

CRED_ROLE = [
    (re.compile(r"PTOE|PTP", re.I), "traffic engineer"),
    (re.compile(r"\bP\.?\s?E\.?\b", re.I), "engineer"),
    (re.compile(r"\bP\.?\s?P\.?\b|AICP", re.I), "planner"),
    (re.compile(r"Esq", re.I), "attorney"),
    (re.compile(r"A\.?I\.?A\.?|\bR\.?A\.?\b|NCARB", re.I), "architect"),
    (re.compile(r"P\.?L\.?S\.?|\bL\.?S\.?\b", re.I), "surveyor"),
    (re.compile(r"L\.?L\.?A\.?|R\.?L\.?A\.?|ASLA|NJLA|CLA", re.I), "landscape architect"),
    (re.compile(r"C\.?M\.?E\.?", re.I), "municipal engineer"),
    (re.compile(r"LSRP|PWS|CPG|P\.?G\.?", re.I), "environmental consultant"),
    (re.compile(r"MAI|SCGREA|RPA|CTA", re.I), "appraiser"),
]

_FIRM_LIKE = re.compile(r"^(of|with|from|and|or)\s|\b(LLC|L\.L\.C\.|Inc|Group|Associates|Assoc|Engineering|Engineers|Architects?|Architecture|Planning|Consultants|Consulting|Partners|Design|P\.?C|P\.?A|LLP|Company|Corp|Law|Surveying|Landscape|Studio|Services)\.?$", re.I)
_BAD_NAMES = re.compile(r"^(The|This|That|Board|Planning|Zoning|Township|Borough|City|Town|Village|County|State|New Jersey|Motion|Resolution|Application|Applicant|Block|Lot|Site Plan|Public|Chairman|Chairwoman|Vice|Member|Members|Secretary|Attorney|Engineer|Planner|Mayor|Council|North|South|East|West|Main|Route|Street|Avenue|Road)\b", re.I)


@dataclass
class Professional:
    name: str
    credentials: str = ""
    role: str = ""
    side: str = "unknown"          # applicant | board | unknown
    firm: str = ""
    testified: bool = False
    context: str = ""
    application: Optional[str] = None
    mentions: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_name(n: str) -> str:
    n = re.sub(r"^(Mr|Ms|Mrs|Dr)\.?\s+", "", n.strip())
    n = re.sub(r"\s+", " ", n)
    return n


def role_from_creds(creds: str) -> str:
    for pat, role in CRED_ROLE:
        if pat.search(creds):
            return role
    return ""


def _role_near(text: str, start: int, end: int, span: int = 110) -> tuple[str, str]:
    """Look around a name for a role phrase; the phrase closest to the name wins."""
    lo = max(0, start - span)
    win = text[lo: min(len(text), end + span)]
    ns, ne = start - lo, end - lo  # name span in window coords
    best: tuple[int, str, str] | None = None
    for pat, side in ((BOARD_SIDE, "board"), (APPLICANT_SIDE, "applicant")):
        for m in pat.finditer(win):
            if m.start() >= ne:
                dist = m.start() - ne
            elif m.end() <= ns:
                dist = ns - m.end()
            else:
                dist = 0
            # a phrase separated from the name by ';' or a newline belongs to someone else
            between = win[m.end(): ns] if m.end() <= ns else win[ne: m.start()]
            if re.search(r"[;\n]|\band\b", between):
                dist += 200
            if best is None or dist < best[0]:
                best = (dist, m.group(1).lower(), side)
    if best is not None and best[0] < 200:
        return best[1], best[2]
    m = ANY_ROLE.search(text[end: min(len(text), end + 70)]) or ANY_ROLE.search(text[max(0, start - 60): start])
    if m:
        return m.group(1).lower(), "unknown"
    return "", "unknown"


def extract_professionals(text: str, application: Optional[str] = None) -> list[Professional]:
    """Return professionals found in text with best-effort role/side/firm."""
    found: dict[str, Professional] = {}

    def add(name: str, creds: str, role: str, side: str, firm: str, testified: bool, ctx: str) -> None:
        key = name.lower()
        if _BAD_NAMES.match(name) or _FIRM_LIKE.search(name) or len(name.split()) < 2:
            return
        p = found.get(key)
        if p is None:
            found[key] = Professional(name=name, credentials=creds, role=role, side=side, firm=firm,
                                      testified=testified, context=squash(ctx)[:240], application=application)
            return
        p.mentions += 1
        if creds and not p.credentials:
            p.credentials = creds
        if role and (not p.role or p.role in ("expert", "consultant")):
            p.role = role
        if side != "unknown" and p.side == "unknown":
            p.side = side
        if firm and not p.firm:
            p.firm = firm
        p.testified = p.testified or testified

    # 1. Name, CREDENTIALS patterns
    for m in NAME_CRED.finditer(text):
        name = _norm_name(m.group(1))
        creds = re.sub(r"\s+", "", m.group(2).upper().replace(" ", ""))
        creds = creds.replace(",", ", ")
        role, side = _role_near(text, m.start(), m.end())
        role = role or role_from_creds(creds)
        if "traffic" in text[max(0, m.start() - 80): m.end() + 80].lower() and role == "engineer":
            role = "traffic engineer"
        after = text[m.end(): m.end() + 160]
        fm = FIRM.search(after)
        firm = squash(fm.group(1)) if fm and fm.start() < 40 else ""
        ctx = text[max(0, m.start() - 120): m.end() + 160]
        testified = bool(TESTIMONY.search(ctx)) and side != "board"
        if side == "unknown" and testified:
            side = "applicant"
        add(name, creds, role, side, firm, testified, ctx)

    # 2. Role-phrase + name without credentials: "Board Engineer John Smith" / "John Smith, the applicant's engineer"
    # role phrases are case-insensitive, but names must keep their capitals
    role_then_name = re.compile(rf"((?i:{BOARD_SIDE.pattern}|{APPLICANT_SIDE.pattern}))[,:]?\s+{NAME}")
    name_then_role = re.compile(rf"{NAME}\s*,\s*(?:the\s+)?((?i:{BOARD_SIDE.pattern}|{APPLICANT_SIDE.pattern}))")
    for pat, name_last in ((role_then_name, True), (name_then_role, False)):
        for m in pat.finditer(text):
            groups = [g for g in m.groups() if g]
            name = _norm_name(groups[-1] if name_last else groups[0])
            phrase = m.group(0)
            side = "board" if BOARD_SIDE.search(phrase) else "applicant"
            rm = ANY_ROLE.search(phrase)
            role = rm.group(1).lower() if rm else ""
            ctx = text[max(0, m.start() - 100): m.end() + 160]
            testified = side == "applicant" and bool(TESTIMONY.search(ctx))
            add(name, "", role, side, "", testified, ctx)

    # 3. Sworn witnesses without credentials: "John Smith was sworn and testified"
    sworn = re.compile(rf"{NAME}\s*(?:,\s*[^,.]{{0,60}},)?\s*(?i:(?:was|were)\s+(?:duly\s+)?sworn)|{NAME}\s+(?i:testified)")
    for m in sworn.finditer(text):
        name = _norm_name(m.group(1) or m.group(2) or "")
        if not name:
            continue
        role, side = _role_near(text, m.start(), m.end())
        add(name, "", role, "applicant" if side == "unknown" else side, "", True, text[max(0, m.start() - 100): m.end() + 160])

    return list(found.values())


def board_roster(text: str) -> dict[str, list[str]]:
    """Board-side professionals mentioned (role -> [names])."""
    roster: dict[str, list[str]] = {}
    for p in extract_professionals(text):
        if p.side == "board" and p.role:
            roster.setdefault(p.role, [])
            if p.name not in roster[p.role]:
                roster[p.role].append(p.name)
    return roster


def attributed_remarks(text: str, roster: dict[str, list[str]]) -> list[dict]:
    """Sentences attributed to board professionals ('Mr. Smith, the Board Engineer, noted that ...')."""
    last_to_role: dict[str, str] = {}
    for role, names in roster.items():
        for n in names:
            last_to_role[n.split()[-1].lower()] = role
    if not last_to_role:
        return []
    speak = re.compile(r"\b(?:Mr|Ms|Mrs|Dr)\.?\s+([A-Z][a-zA-Z'\-]+)\b[^.]{0,80}?\b(stated|said|noted|indicated|commented|questioned|asked|requested|recommended|explained|reviewed|advised|suggested|pointed\s+out|expressed|raised|wanted|would\s+like|confirmed|clarified|added|reminded|cautioned|required|opined|testified)\b", re.I)
    out = []
    for s in sentences(text):
        m = speak.search(s)
        if not m:
            continue
        role = last_to_role.get(m.group(1).lower())
        if role:
            out.append({"speaker": m.group(1), "role": role, "text": squash(s)[:500]})
    return out
