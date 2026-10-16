"""Shared extraction primitives used by all document parsers: comment
taxonomy, application identifiers, application types, decisions, waivers."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..util import sentences, squash

# ---------------------------------------------------------------- taxonomy
# Category -> regex. Used to tag review comments, conditions of approval and
# board-professional remarks so they can be counted across towns.
CATEGORIES: dict[str, re.Pattern] = {
    "comply_with_review_letters": re.compile(r"comply\s+with\s+(all\s+)?(the\s+)?(comments|conditions|recommendations|requirements)\s+(in|of|set\s+forth\s+in|contained\s+in)\s+the\s+(board|township|borough|city)?\s*(engineer|planner|professional)|review\s+(letter|report|memo)s?\s+dated|address\s+(all\s+)?(the\s+)?(comments|items)\s+(in|of)\s+the", re.I),
    "stormwater": re.compile(r"storm\s*water|drainage|detention|retention|infiltration|basin|runoff|run-off|N\.?J\.?A\.?C\.?\s*7:8|green\s+infrastructure|\bBMP|water\s+quality|recharge|outfall|inlet|catch\s+basin|pipe\s+siz|hydrolog|hydraulic|TR-?55|\bHGL\b", re.I),
    "grading": re.compile(r"\bgrading|topograph|contour|spot\s+elevation|retaining\s+wall|\bslope|cut\s+and\s+fill|earthwork|finished\s+floor", re.I),
    "parking": re.compile(r"parking|\bstalls?\b|loading\s+(space|zone|area|berth)|\bADA\b|handicap|accessible\s+(space|route|parking)|electric\s+vehicle|\bEV\b|make-?ready|EVSE|bicycle\s+parking|\bRSIS\b", re.I),
    "traffic": re.compile(r"traffic|circulation|sight\s+(distance|triangle)|driveway|curb\s+cut|\bingress|\begress|turning\s+(movement|template|radius)|truck\s+(turning|route)|AASHTO|NJDOT|access\s+(permit|management)|level\s+of\s+service|\bLOS\b|trip\s+generation|queu", re.I),
    "lighting": re.compile(r"\blight(ing|s)?\b|foot-?candle|photometric|fixture|glare|full\s+cut-?off|illuminat|pole\s+height|mounting\s+height", re.I),
    "landscaping": re.compile(r"landscap|buffer|screen(ing)?\b|plant(ing|ings)?\b|\btrees?\b|shade\s+tree|shrub|street\s+tree|replacement\s+tree|species|caliper|ground\s*cover|irrigation|berm", re.I),
    "signage": re.compile(r"\bsign(age|s)?\b|monument\s+sign|wall\s+sign|freestanding\s+sign|directional\s+sign", re.I),
    "utilities": re.compile(r"sanitary|sewer|water\s+(main|service|line|supply)|utilit(y|ies)|hydrant|gas\s+(main|service)|electric\s+service|transformer|generator|well\b|septic|treatment\s+works\s+approval|\bTWA\b|water\s+allocation", re.I),
    "soil_erosion": re.compile(r"soil\s+erosion|sediment\s+control|\bSESC\b|soil\s+conservation|\bSCD\b|silt\s+fence|stabiliz", re.I),
    "environmental": re.compile(r"wetland|flood|FEMA|riparian|NJDEP|\bDEP\b|freshwater|CAFRA|environmental|contaminat|remediat|LSRP|threatened|endangered|steep\s+slope|tree\s+removal|woodland|stream|\bLOI\b|letter\s+of\s+interpretation|highlands|pinelands|flood\s+hazard|green\s+acres|open\s+space|conservation\s+easement|\bEIS\b|environmental\s+impact", re.I),
    "fire_safety": re.compile(r"fire\s+(department|official|marshal|lane|truck|apparatus|access|hydrant|code|subcode|chief|company|bureau)|emergency\s+(access|vehicle)|knox\s+box|\bFDC\b|sprinkler|standpipe|aerial\s+apparatus", re.I),
    "refuse": re.compile(r"refuse|trash|garbage|dumpster|recycling|solid\s+waste|enclosure", re.I),
    "architecture": re.compile(r"architectur|elevation(s)?\b|fa[cç]ade|building\s+height|roof|building\s+material|\bHVAC\b|mechanical\s+(equipment|unit)|rooftop|floor\s+plan|canopy|awning|color|rendering", re.I),
    "zoning_bulk": re.compile(r"setback|lot\s+coverage|impervious|\bFAR\b|floor\s+area\s+ratio|\bbulk\b|\bheight\b|density|lot\s+area|frontage|lot\s+width|lot\s+depth|variance|non-?conform|permitted\s+use|use\s+variance|accessory|zoning\s+(table|schedule|compliance)|schedule\s+of\s+(area|bulk)|d\(\d\)|c\(\d\)|40:55D", re.I),
    "completeness": re.compile(r"checklist|completeness|deemed\s+(complete|incomplete)|submission\s+waiver|waiver", re.I),
    "outside_agencies": re.compile(r"county\s+planning|county\s+(board|engineer|approval)|soil\s+conservation|NJDOT|\bDEP\b|utilities\s+authority|\bMUA\b|sewerage\s+authority|water\s+company|American\s+Water|board\s+of\s+health|fire\s+official|NJ\s*Transit|highlands\s+council|pinelands\s+commission|meadowlands|\bHPC\b|historic\s+preservation|outside\s+agenc|other\s+(governmental\s+)?agenc|D&R\s+Canal|\bSHPO\b|\bFAA\b|\bPSE&G|JCP&L|Elizabethtown|NJ\s+American", re.I),
    "fees_escrow": re.compile(r"escrow|\bfees?\b|\bbond\b|performance\s+guarantee|maintenance\s+guarantee|inspection\s+fee|developer'?s?\s+agreement|surety|cost\s+estimate|engineer'?s?\s+estimate", re.I),
    "easements_title": re.compile(r"easement|\bdeed\b|dedication|right[-\s]of[-\s]way|\bROW\b|\btitle\b|metes\s+and\s+bounds|survey|monument(s|ation)?\b|lot\s+consolidation|subdivision\s+(plat|map|deed)|map\s+filing|outbound|filed\s+map|cross[-\s]access|sight\s+easement", re.I),
    "affordable_housing": re.compile(r"affordable|COAH|mount\s+laurel|inclusionary|UHAC|set[-\s]aside|fair\s+share|development\s+fee", re.I),
    "operations": re.compile(r"hours\s+of\s+operation|\bnoise\b|deliver(y|ies)|employees|snow\s+(storage|removal|plow)|outdoor\s+(storage|seating|display)|security|loitering|maintenance|idling|odor|vibration|construction\s+(hours|schedule|management|traffic)|phasing", re.I),
    "pedestrian": re.compile(r"sidewalk|crosswalk|pedestrian|bicycle|bike\s+rack|curb\s+ramp|walkway|depressed\s+curb|ADA\s+ramp|detectable\s+warning|complete\s+streets", re.I),
    "plan_corrections": re.compile(r"\brevise|revised\s+plan|label|\bnotes?\b|legend|dimension|title\s+block|\bscale\b|north\s+arrow|\bsheet|typo|signed\s+and\s+sealed|\bseal(ed)?\b|key\s+map|zoning\s+table|schedule\s+of|\bdetail(s)?\b|cross[-\s]section|profile|datum|benchmark|graphic|clarif|discrepanc|inconsisten|should\s+be\s+(shown|indicated|provided|noted|added|revised|corrected|clarified)", re.I),
    "testimony": re.compile(r"testimony|testif|witness|should\s+(address|describe|explain)|applicant\s+should\s+(provide|describe|discuss|confirm)|clarify\s+at\s+the\s+hearing|at\s+the\s+(public\s+)?hearing", re.I),
}

# Checklist / submission item topics used to label waiver requests.
SUBMISSION_TOPICS: dict[str, re.Pattern] = {
    "environmental impact statement": re.compile(r"environmental\s+impact\s+(statement|assessment|report)|\bEIS\b|\bEIA\b", re.I),
    "traffic impact study": re.compile(r"traffic\s+(impact\s+)?(study|report|analysis|statement)|trip\s+generation", re.I),
    "stormwater management report": re.compile(r"storm\s*water\s+(management\s+)?(report|plan|calculation|analysis|narrative)|drainage\s+(report|calculation|analysis|plan)|hydrolog|infiltration\s+test|soil\s+(log|boring|test)|percolation|permeability", re.I),
    "landscape plan": re.compile(r"landscap(e|ing)\s+plan|planting\s+plan|tree\s+(survey|inventory|removal\s+plan|protection)", re.I),
    "lighting plan": re.compile(r"lighting\s+plan|photometric|lighting\s+detail", re.I),
    "architectural plans": re.compile(r"architectural\s+(plan|drawing|elevation|rendering)|floor\s+plan|building\s+elevation|rendering", re.I),
    "survey / topography": re.compile(r"\bsurvey|topograph|contour|existing\s+conditions|boundary|spot\s+elevation|elevation\s+datum|benchmark|monument", re.I),
    "200-foot radius / property owner list": re.compile(r"200\s*(ft|feet|foot|')|property\s+owner|certified\s+list|radius\s+map", re.I),
    "aerial photograph": re.compile(r"aerial|orthophoto|photograph", re.I),
    "key map / location map": re.compile(r"key\s+map|location\s+map|vicinity\s+map|zoning\s+map", re.I),
    "utility plan": re.compile(r"utilit(y|ies)\s+plan|sanitary\s+sewer|water\s+(main|service)|sewer\s+(capacity|calculation|design)|will[-\s]serve", re.I),
    "grading plan": re.compile(r"grading\s+(plan|and\s+drainage)|cut\s+and\s+fill|earthwork|retaining\s+wall\s+(design|detail|calculation)", re.I),
    "soil erosion plan": re.compile(r"soil\s+erosion|sediment\s+control|\bSESC\b|soil\s+conservation", re.I),
    "signage plan": re.compile(r"sign(age)?\s+(plan|detail|package)", re.I),
    "construction details": re.compile(r"construction\s+detail|standard\s+detail|detail\s+sheet|cross[-\s]section|profile", re.I),
    "wetlands / LOI": re.compile(r"wetland|letter\s+of\s+interpretation|\bLOI\b|flood\s+hazard|riparian|freshwater", re.I),
    "parking / circulation plan": re.compile(r"parking\s+(plan|study|analysis|calculation)|circulation\s+plan|truck\s+turning|turning\s+(template|movement)|sight\s+(triangle|distance)|loading", re.I),
    "fiscal / economic impact": re.compile(r"fiscal\s+impact|economic\s+impact|community\s+impact", re.I),
    "affordable housing plan": re.compile(r"affordable\s+housing|COAH|inclusionary", re.I),
    "recycling / refuse plan": re.compile(r"recycling\s+plan|refuse|solid\s+waste|dumpster", re.I),
    "phasing / construction schedule": re.compile(r"phasing|construction\s+schedule|construction\s+sequence|staging", re.I),
    "outside agency approvals": re.compile(r"outside\s+agenc|other\s+agenc|county\s+(planning|approval)|NJDOT|NJDEP|\bDEP\b|soil\s+conservation|utilities\s+authority|\bMUA\b|water\s+company|\bTWA\b", re.I),
    "deed / easement / title": re.compile(r"\bdeed|easement|title\s+(report|certificate|search)|covenant|restriction|protective\s+covenant|owner'?s?\s+consent|disclosure\s+statement|corporate\s+disclosure|40:55D-48", re.I),
    "tax / assessment certification": re.compile(r"tax(es)?\s+(paid|certification|collector)|certification\s+of\s+(taxes|payment)|tax\s+map", re.I),
    "fee / escrow": re.compile(r"\bfees?\b|escrow", re.I),
    "digital / electronic submission": re.compile(r"digital|electronic|\bCAD\b|\bGIS\b|\bdwg\b|pdf\s+cop", re.I),
    "zoning / bulk table": re.compile(r"zoning\s+(table|schedule|compliance|data)|bulk\s+(table|schedule|requirement|chart)|schedule\s+of\s+(area|bulk)|variance\s+(list|table|plan)", re.I),
    "number of plan copies / plan size": re.compile(r"\d+\s+(copies|sets|prints)|sheet\s+size|plan\s+size|24\s*[x×]\s*36|30\s*[x×]\s*42|full[-\s]size|half[-\s]size", re.I),
    "open space / recreation": re.compile(r"open\s+space|recreation\s+(plan|area)|park\s+contribution", re.I),
    "fire / emergency plan": re.compile(r"fire\s+(protection|access|flow|truck)|emergency\s+(access|vehicle)|hydrant\s+flow", re.I),
    "historic / archaeological": re.compile(r"historic|archaeolog|cultural\s+resource|\bSHPO\b|\bHPC\b", re.I),
    "steep slope / tree": re.compile(r"steep\s+slope|slope\s+analysis|tree\s+(removal|replacement|survey|inventory)|woodland|forest", re.I),
    "noise / air": re.compile(r"\bnoise\b|acoustic|air\s+quality", re.I),
    "green building / sustainability": re.compile(r"green\s+building|sustainab|LEED|energy|solar", re.I),
}


def categorize(text: str, top: int = 2) -> list[str]:
    scores = []
    for cat, pat in CATEGORIES.items():
        n = len(pat.findall(text))
        if n:
            scores.append((n, cat))
    scores.sort(key=lambda t: (-t[0], t[1]))
    cats = [c for _, c in scores[:top]]
    # 'plan_corrections' and 'testimony' are weak categories; demote if anything else matched
    weak = {"plan_corrections", "testimony", "completeness"}
    strong = [c for c in cats if c not in weak]
    return (strong + [c for c in cats if c in weak])[:top] or ["general"]


def primary_category(text: str) -> str:
    return categorize(text, top=1)[0]


def submission_topic(text: str) -> Optional[str]:
    best, best_n = None, 0
    for topic, pat in SUBMISSION_TOPICS.items():
        n = len(pat.findall(text))
        if n > best_n:
            best, best_n = topic, n
    return best


# ----------------------------------------------------------- identifiers
APP_NUMBER = re.compile(
    r"(?:\b(?:Application|App\.?|Case|Docket|Cal(?:endar)?|File|Matter|Appl\.?)\s*(?:No\.?|#|Number|Num\.?)?\s*:?\s*"
    r"([A-Z]{0,5}[-\s]?\d{1,4}[-/]\d{1,4}(?:[-/]\d{1,4})?[A-Z]?))"
    r"|\b((?:PB|ZB|ZBA|BOA|BA|LUB|SP|SD|SPA|PBA|ZBAA|P|Z|DRB|UPB)[-\s]?\d{2,4}[-/]\d{1,4}(?:[-/]\d{1,4})?[A-Z]?)\b"
    r"|\b(\d{2,4}-\d{2,4}(?:[-/]\d{1,4})?\s*(?:SP|SD|V|UV|BV|CU|S|M|P|Z))\b",
    re.I)
BLOCK_LOT = re.compile(
    r"Block(?:s)?\s*(?:No\.?s?\s*)?#?\s*([\d.]+[A-Z]?(?:\s*(?:,|&|and)\s*[\d.]+[A-Z]?)*)\s*[,;]?\s*(?:&\s*)?"
    r"Lot(?:s)?\s*(?:No\.?s?\s*)?#?\s*([\d.]+[A-Z]?(?:\s*(?:,|&|and|through|thru|to|-)\s*[\d.]+[A-Z]?)*)", re.I)
STREET_ADDR = re.compile(
    r"\b(\d{1,5}(?:-\d{1,5})?[A-Za-z]?\s+(?:[A-Z][\w.'&-]*\s+){0,4}?"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Route|Rt\.?|Highway|Hwy\.?|"
    r"Way|Place|Pl\.?|Court|Ct\.?|Terrace|Ter\.?|Pike|Turnpike|Tpke\.?|Parkway|Pkwy\.?|Circle|Cir\.?|Plaza|Path|Trail|Broadway))\b")
ZONE = re.compile(r"\b([A-Z]{1,4}-?\d{0,3}[A-Z]?(?:/[A-Z0-9\-]+)?)\s+(?:Zone|Zoning\s+District|District)\b|\bZone(?:\s+District)?\s*:?\s*([A-Z]{1,4}-?\d{0,3}[A-Z]?)\b")

APP_TYPES: list[tuple[str, re.Pattern]] = [
    ("preliminary & final major site plan", re.compile(r"preliminary\s*(and|&|/)\s*final\s*(major\s*)?site\s*plan", re.I)),
    ("preliminary & final major subdivision", re.compile(r"preliminary\s*(and|&|/)\s*final\s*(major\s*)?subdivision", re.I)),
    ("final major site plan", re.compile(r"final\s+(major\s+)?site\s*plan", re.I)),
    ("preliminary major site plan", re.compile(r"preliminary\s+(major\s+)?site\s*plan", re.I)),
    ("amended site plan", re.compile(r"amended\s+(preliminary\s+|final\s+|major\s+|minor\s+)*site\s*plan|site\s*plan\s+amendment", re.I)),
    ("minor site plan", re.compile(r"minor\s+site\s*plan", re.I)),
    ("major site plan", re.compile(r"major\s+site\s*plan", re.I)),
    ("site plan waiver", re.compile(r"site\s*plan\s+(waiver|exemption)|waiver\s+of\s+site\s*plan", re.I)),
    ("site plan", re.compile(r"\bsite\s*plan\b", re.I)),
    ("final major subdivision", re.compile(r"final\s+(major\s+)?subdivision", re.I)),
    ("preliminary major subdivision", re.compile(r"preliminary\s+(major\s+)?subdivision", re.I)),
    ("minor subdivision", re.compile(r"minor\s+subdivision", re.I)),
    ("major subdivision", re.compile(r"major\s+subdivision", re.I)),
    ("subdivision", re.compile(r"\bsubdivision\b", re.I)),
    ("use variance (d)", re.compile(r"use\s+variance|\bd\s*\(?\s*1\s*\)?\s*variance|\bd[-\s]?variance|40:55D-70\s*\(?\s*d|d\s*\(\s*[1-6]\s*\)", re.I)),
    ("bulk variance (c)", re.compile(r"bulk\s+variance|\bc\s*\(?\s*[12]\s*\)?\s*variance|\bc[-\s]?variance|40:55D-70\s*\(?\s*c|c\s*\(\s*[12]\s*\)|hardship\s+variance|dimensional\s+variance|variance\s+relief", re.I)),
    ("conditional use", re.compile(r"conditional\s+use", re.I)),
    ("interpretation", re.compile(r"\binterpretation\b", re.I)),
    ("appeal of zoning officer", re.compile(r"appeal\s+(of|from)\s+(the\s+)?(zoning|decision|determination)", re.I)),
    ("certificate of nonconformity", re.compile(r"certificate\s+of\s+non-?conform|certif(y|ication)\s+(of\s+)?(a\s+)?pre-?existing", re.I)),
    ("extension", re.compile(r"\bextension\s+of\s+(time|approval|preliminary|final)", re.I)),
    ("concept / informal review", re.compile(r"concept(ual)?\s+(plan|review)|informal\s+(review|discussion)|courtesy\s+review", re.I)),
    ("variance", re.compile(r"\bvariances?\b", re.I)),
]

DECISIONS: list[tuple[str, re.Pattern]] = [
    ("approved_with_conditions", re.compile(r"approved?\s+(with|subject\s+to)\s+(the\s+following\s+)?conditions|granted?\s+(with|subject\s+to)\s+(the\s+following\s+)?conditions|conditionally\s+approved|conditional\s+approval", re.I)),
    ("denied", re.compile(r"\b(is|was|be|are|were|hereby)\s+denied\b|\bden(y|ied|ial)\b.*\b(application|variance|approval|relief)|motion\s+to\s+deny|application\s+(is|was)\s+denied|vote(d)?\s+to\s+deny|\bdenied\b", re.I)),
    ("approved", re.compile(r"\b(is|was|be|are|were|hereby)\s+(approved|granted)\b|motion\s+to\s+(approve|grant)|unanimously\s+(approved|granted)|approval\s+(is|was)\s+(hereby\s+)?granted|\bapproved\b|\bgranted\b", re.I)),
    ("withdrawn", re.compile(r"\bwithdrawn|\bwithdrew|\bdismissed\s+without\s+prejudice", re.I)),
    ("carried", re.compile(r"\bcarried\s+(to|until|over)|\bcontinued\s+(to|until)|\badjourned\s+(to|until)|\btabled\b|\bpostponed|will\s+be\s+heard\s+on|rescheduled", re.I)),
    ("deemed_complete", re.compile(r"deemed\s+complete(?!\s*ness)|found\s+(to\s+be\s+)?complete|determined\s+(to\s+be\s+)?complete", re.I)),
    ("deemed_incomplete", re.compile(r"deemed\s+incomplete|found\s+(to\s+be\s+)?incomplete|determined\s+(to\s+be\s+)?incomplete", re.I)),
    ("memorialized", re.compile(r"memorializ", re.I)),
]

CLOSING = re.compile(r"^(Should\s+you|If\s+you\s+have|Please\s+(do\s+not|feel\s+free|contact)|Very\s+truly|Sincerely|Respectfully|Regards|cc\s*:|Enclosure|Attachment|Thank\s+you|"
                     r"Subject\s+to\s+the\s+(Board|above|foregoing)|Based\s+on\s+the\s+(above|foregoing)|In\s+(summary|conclusion)|We\s+(trust|reserve|look\s+forward)|This\s+office\s+(reserves|will)|The\s+above\s+comments)", re.I)
NUMBERED_ITEM = re.compile(r"^\s*(?:(\d{1,3})|([A-Za-z])|([ivxIVX]{1,5}))\s*[.):\-]\s+(?=\S)")
BULLET_ITEM = re.compile(r"^\s*[•●▪■◦\-–—*]\s+(?=\S)")


def app_numbers(text: str) -> list[str]:
    out = []
    for m in APP_NUMBER.finditer(text):
        v = next(g for g in m.groups() if g)
        v = squash(v).upper().replace(" ", "")
        if v not in out and not re.fullmatch(r"\d{4}", v):
            out.append(v)
    return out


def block_lots(text: str) -> list[dict]:
    out = []
    for m in BLOCK_LOT.finditer(text):
        bl = {"block": squash(m.group(1)), "lots": squash(m.group(2))}
        if bl not in out:
            out.append(bl)
    return out


def addresses(text: str) -> list[str]:
    out = []
    for m in STREET_ADDR.finditer(text):
        a = squash(m.group(1))
        if a not in out and not re.match(r"^\d{4}\s", a):  # skip "2024 Route"-like year hits
            out.append(a)
    return out


def zones(text: str) -> list[str]:
    out = []
    for m in ZONE.finditer(text):
        z = (m.group(1) or m.group(2) or "").strip()
        if z and z.upper() not in ("THE", "A", "IN", "OF") and len(z) <= 10 and z not in out:
            out.append(z)
    return out


def app_types(text: str) -> list[str]:
    found = [name for name, pat in APP_TYPES if pat.search(text)]
    # 'major site plan' is implied by 'preliminary & final major site plan'; keep the most specific
    return [f for f in found if not any(f != g and f in g for g in found)]


_APP_LEVEL = re.compile(r"application|motion|approv|hearing|matter|vote|resolved|relief|variance|subdivision|site\s*plan|board", re.I)


def decision(text: str) -> Optional[str]:
    """Best-guess outcome for an application section.

    Works sentence by sentence so that a granted/denied *waiver* inside the
    discussion does not read as the application's outcome. Specific outcomes
    (conditions, denial) win over generic ones; 'carried' wins when nothing
    else was decided.
    """
    found: set[str] = set()
    fallback: set[str] = set()
    for s in sentences(text):
        target = found if not re.search(r"\bwaiv", s, re.I) else fallback
        if not _APP_LEVEL.search(s):
            # bare 'Approved.' / 'Carried.' lines are common on agendas
            if len(s) > 60:
                continue
        for name, pat in DECISIONS:
            if pat.search(s):
                target.add(name)
                break
    pool = found or fallback
    for name, _ in DECISIONS:
        if name in pool:
            return name
    return None


# ---------------------------------------------------------------- waivers
WAIVER_WORD = re.compile(r"\bwaiv(e|er|ers|ed|ing)\b|\bexception\b", re.I)
WAIVER_KIND = [
    ("checklist", re.compile(r"checklist|submission|completeness|complete|item\s*(no|#|\d)|application\s+requirement", re.I)),
    ("design", re.compile(r"design\s+(waiver|exception|standard)|\bRSIS\b|residential\s+site\s+improvement|de\s+minimis|exception\s+(from|to)\s+(the\s+)?(design|RSIS)", re.I)),
]
_GRANT_NEG_SAFE = re.compile(r"no\s+objection|not\s+object|does\s+not\s+object|did\s+not\s+object|no\s+issue", re.I)
_DENIED = re.compile(
    r"\b(not|wasn'?t|was\s+not|is\s+not|are\s+not|were\s+not|be\s+not|cannot\s+be|can\s+not\s+be|should\s+not\s+be|shall\s+not\s+be|will\s+not\s+be|would\s+not\s+be|not\s+be)\s+(granted|approved|recommended|supported|waived|accepted)"
    r"|\bdenied\b|\bdeny\b|\bdenial\b|\brejected\b|\bdeclin|\brefus|\bobject(s|ed|ion)?\s+to\b|\bopposed\b"
    r"|\b(do|does|did|can|could|would)\s+not\s+(recommend|support|concur|agree)\b|\bnot\s+recommended\b|\bnot\s+supported\b|\bnot\s+in\s+a\s+position\b|\bunable\s+to\s+(recommend|support|grant)"
    r"|\b(must|shall|should|needs?\s+to|is\s+required\s+to|are\s+required\s+to|required\s+to|will\s+need\s+to|will\s+be\s+required\s+to|has\s+to|have\s+to)\s+(be\s+)?(submit|provid|includ|address|compl|prepar|furnish|suppl)"
    r"|\bno\s+waiver\b|\bwaiver\s+(is|was)\s+not\s+(appropriate|warranted|justified|necessary|supported)\b|\bnot\s+waiv(e|ed)\b|\binstead\s+(the\s+)?applicant\s+(shall|must|will)\b|\brequire(s|d)?\s+(the\s+)?(submission|submittal)",
    re.I)
_GRANTED = re.compile(
    r"\b(is|was|are|were|be|been|hereby|being)\s+(granted|approved|recommended|supported|waived|accepted)\b|\bgrant(s|ed)?\b|\bapprov(e|ed|es|al)\b|\bwaived\b"
    r"|\bno\s+objection|\bnot\s+object|\bcan\s+support|\bsupports?\b|\bconcur|\brecommends?\s+(approval|granting|that\s+the\s+(waiver|request)s?\s+be\s+(granted|approved))|\bacceptable\b|\bhas\s+no\s+issue|\bagree(s|d)?\s+(to|with)\b|\bconsent",
    re.I)
_DEFERRED = re.compile(r"\bdefer|\bpending\b|\bcarried\b|\btabled\b|\bto\s+be\s+(determined|addressed)\b|\breserve", re.I)
_REQUESTED = re.compile(r"\brequest(s|ed|ing)?\b|\bseek(s|ing)?\b|\bsought\b|\bask(s|ed|ing)?\b|\bapplying\s+for\b|\bproposes?\b", re.I)
_NEEDED = re.compile(r"\b(waiver|variance|exception|relief)s?\s+(?:from\s+[^.;]{0,80}?\s+)?(is|are|will\s+be|would\s+be|may\s+be|shall\s+be|appears?\s+to\s+be)\s+(required|necessary|needed|warranted)\b|\b(requires?|necessitates?|will\s+require|would\s+require)\s+(a\s+|an\s+)?(design\s+)?(waiver|variance|exception)", re.I)
ITEM_REF = re.compile(
    r"(?:checklist\s+)?item(?:s)?\s*(?:no\.?|#|number|nos\.?)?\s*:?\s*"
    r"((?:[A-Z]?\d{1,3}[A-Za-z]?(?:\.\d+)?)(?:\s*(?:,|&|and|through|thru|to|-)\s*(?:[A-Z]?\d{1,3}[A-Za-z]?(?:\.\d+)?))*)"
    r"|#\s*(\d{1,3}[A-Za-z]?)\b|§\s*([\d\-.:A-Za-z]+)|section\s+([\d\-.:A-Za-z]+)", re.I)


@dataclass
class Waiver:
    text: str
    kind: str                      # checklist | design | other
    disposition: str               # granted | denied | needed | requested | deferred | mentioned
    items: list[str] = field(default_factory=list)
    topic: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None   # doc id / type
    application: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def waiver_disposition(sent: str) -> str:
    s = sent
    # 'a design waiver is required' is a reviewer flagging a need, not a decision;
    # judge it on the clause that names the waiver, not on the rest of the comment
    core = " ".join(c for c in sentences(s) if WAIVER_WORD.search(c)) or s
    if _NEEDED.search(core) and not _GRANTED.search(re.sub(r"required|necessary|needed|warranted", " ", core)) and not _DENIED.search(core):
        return "needed"
    if _GRANT_NEG_SAFE.search(s):
        s2 = _GRANT_NEG_SAFE.sub(" ", s)
        if not _DENIED.search(s2):
            return "granted"
    if _DENIED.search(s):
        return "denied"
    if _GRANTED.search(s):
        return "granted"
    if _NEEDED.search(s):
        return "needed"
    if _DEFERRED.search(s):
        return "deferred"
    if _REQUESTED.search(s):
        return "requested"
    return "mentioned"


def waiver_items(sent: str) -> list[str]:
    out = []
    for m in ITEM_REF.finditer(sent):
        v = next((g for g in m.groups() if g), None)
        if v:
            v = squash(v)
            for part in re.split(r"\s*(?:,|&|\band\b)\s*", v):
                if part and part not in out:
                    out.append(part)
    return out


def waiver_kind(sent: str, ctx: str, topic: Optional[str]) -> str:
    if WAIVER_KIND[1][1].search(sent):
        return "design"
    if WAIVER_KIND[0][1].search(sent) or topic is not None:
        return "checklist"
    if WAIVER_KIND[0][1].search(ctx) and not WAIVER_KIND[1][1].search(ctx):
        return "checklist"
    if WAIVER_KIND[1][1].search(ctx):
        return "design"
    return "other"


def _waiver_from_unit(unit: str, before: str, after: str, source, application) -> Waiver:
    """Build one Waiver from a text unit plus its neighbours.

    The topic and item numbers come from the unit itself, else from the text
    *before* it (a request precedes its disposition). The disposition comes
    from the unit, else from the text after it when that text does not open a
    new waiver discussion of its own.
    """
    intro = bool(re.search(r"(items?|following)\s*:\s*(\d{1,3}\.?)?$", unit.strip(), re.I))  # "...following checklist items: 1."
    generic = bool(re.search(r"\bwaivers\b|above\s+waiver|said\s+waiver|waiver\s+requests", unit, re.I))  # refers to several
    topic = submission_topic(unit) or (None if (intro or generic) else submission_topic(before))
    items = ([] if intro else waiver_items(unit)) or ([] if intro else waiver_items(before))
    disp = waiver_disposition(unit)
    if disp in ("mentioned", "requested") and after and not WAIVER_WORD.search(after):
        d2 = waiver_disposition(after)
        if d2 in ("granted", "denied", "deferred"):
            disp = d2
    kind = waiver_kind(unit, before + " " + unit, topic)
    if kind == "design":
        topic = submission_topic(unit)  # only trust the unit itself for design waivers
    return Waiver(text=squash(unit)[:600], kind=kind, disposition=disp, items=items, topic=topic,
                  category=primary_category(unit), source=source, application=application)


def extract_waivers(text: str, source: Optional[str] = None, application: Optional[str] = None,
                    window: int = 2) -> list[Waiver]:
    """Find waiver statements and classify each by kind, disposition, items and topic.

    Review letters and checklists are numbered: each numbered item is one
    unit. Minutes and resolutions are prose: each sentence is a unit and the
    `window` previous sentences give context.
    """
    items = split_numbered(text, min_len=15)
    structured = bool(items) and sum(len(i["text"]) for i in items) > 0.35 * len(text) and \
        sorted(len(i["text"]) for i in items)[len(items) // 2] < 700
    units: list[tuple[str, str, str]] = []
    if structured:
        for n, it in enumerate(items):
            before = (it["section"] + " " + (items[n - 1]["text"] if n else ""))[-400:]
            units.append((it["text"], before, ""))
        # prose outside the numbered items (cover letters, closing paragraphs)
        covered = " || ".join(squash(it["text"]) for it in items)
        sents = sentences(text)
        for i, s in enumerate(sents):
            if WAIVER_WORD.search(s) and squash(s)[:80] not in covered:
                units.append((s, " ".join(sents[max(0, i - window): i]), " ".join(sents[i + 1: i + 2])))
    else:
        sents = sentences(text)
        for i, s in enumerate(sents):
            units.append((s, " ".join(sents[max(0, i - window): i]), " ".join(sents[i + 1: i + 2])))
    out: list[Waiver] = []
    seen = set()
    for unit, before, after in units:
        if not WAIVER_WORD.search(unit):
            continue
        key = squash(unit).lower()[:160]
        if key in seen:
            continue
        seen.add(key)
        out.append(_waiver_from_unit(unit, before, after, source, application))
    # a bare request immediately followed by a decided record on the same topic is redundant
    final: list[Waiver] = []
    for i, w in enumerate(out):
        if w.disposition == "requested":
            nxt = out[i + 1: i + 3]
            if any(n.disposition in ("granted", "denied", "deferred") and (n.topic == w.topic or (n.items and n.items == w.items)) for n in nxt):
                continue
        final.append(w)
    return final


# ------------------------------------------------------------ numbered items
def split_numbered(text: str, min_len: int = 15) -> list[dict]:
    """Split text into numbered/lettered/bulleted items with section headings.

    Returns [{'number': '12', 'section': 'STORMWATER', 'text': '...'}]. Lines
    that are not item starts are appended to the current item; ALL-CAPS or
    colon-terminated short lines become the current section heading.
    """
    items: list[dict] = []
    section = ""
    cur: Optional[dict] = None
    blank_before = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            blank_before = True
            continue
        stripped = line.strip()
        if cur is not None and CLOSING.match(stripped):
            cur = None          # letter sign-off: stop appending to the last item
            blank_before = False
            continue
        is_heading = (len(stripped) < 80 and not NUMBERED_ITEM.match(line) and
                      (stripped.isupper() and re.search(r"[A-Z]{3}", stripped) or
                       (stripped.endswith(":") and len(stripped.split()) <= 8)))
        m = NUMBERED_ITEM.match(line)
        b = BULLET_ITEM.match(line)
        if is_heading and not m:
            section = stripped.rstrip(":")
            cur = None
            continue
        if m or b:
            num = (m.group(1) or m.group(2) or m.group(3)) if m else "•"
            body = line[m.end():] if m else line[b.end():]
            cur = {"number": num, "section": section, "text": body.strip()}
            items.append(cur)
        elif cur is not None:
            # continuation line. After a blank line, a capitalised line following a
            # finished sentence is a new paragraph, not part of the item.
            new_para = blank_before and stripped[:1].isupper() and cur["text"].rstrip().endswith((".", ";", ":", ")"))
            if not new_para and len(cur["text"]) < 4000:
                cur["text"] += " " + stripped
            elif new_para:
                cur = None
        # lines before any item are ignored (letter headers etc.)
        blank_before = False
    out = []
    for it in items:
        it["text"] = squash(it["text"])
        if len(it["text"]) >= min_len:
            out.append(it)
    return out


def comment_key(text: str) -> str:
    """Normalization key used to group near-identical comments across documents."""
    t = text.lower()
    t = re.sub(r"\b(block|lot|lots|sheet|page|item|no|number)\s*[\w.\-]+", " ", t)
    t = re.sub(r"\d+(\.\d+)?\s*(feet|ft|'|\"|inches|in|sf|sq\.?\s*ft|acres?|%|percent|spaces?)", " N ", t)
    t = re.sub(r"\d+", " N ", t)
    t = re.sub(r"[^a-z ]", " ", t)
    stop = {"the", "a", "an", "of", "to", "and", "or", "for", "on", "in", "be", "is", "are", "shall", "should",
            "must", "that", "this", "with", "as", "at", "by", "from", "it", "its", "any", "all", "applicant",
            "plan", "plans", "please", "provide", "provided", "shown", "show", "revise", "revised", "proposed",
            "site", "n", "will", "s", "which", "per", "required", "accordance", "note", "sheet"}
    toks = [w for w in t.split() if w not in stop and len(w) > 2]
    return " ".join(toks[:8])
