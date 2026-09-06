"""
Kijiji car listing watcher - Winnipeg, 50km radius, $2,500-$5,000,
safetied, under 250,000 km.

Pipeline, step by step:
  1. Reads one or more Kijiji "Cars & Trucks" search URLs from config.py
  2. Opens each one with Playwright and reads title/link/price/snippet off
     the search-results page.
  3. Drops anything that looks SOLD, and anything outside MIN_PRICE/MAX_PRICE.
  4. For everything that survives step 3, opens that listing's OWN page.
     Safety wording and mileage are judged from the full description on the
     ad's own page, not the preview text.
  5. On that same page, tries to read the listing's real coordinates (if the
     page exposes them) and computes actual distance from Winnipeg, with a
     hard 50 km cutoff. Falls back to matching place names in the visible
     text if no coordinates are found.
  6. Classifies safety as SAFETIED / NOT_SAFETIED / UNKNOWN based on the
     full description text using hundreds of pattern variants.
  7. Extracts mileage/km from the listing and filters out anything over
     250,000 km.
  8. Remembers what it's already told you about in seen_listings.json.
  9. Emails you about new SAFETIED or UNKNOWN (if enabled) listings.
     NOT_SAFETIED listings are logged to the console but never emailed.

How to run it:
    python car_scraper.py            checks once, then exits
    python car_scraper.py --loop     checks forever, every CHECK_INTERVAL_MINUTES
"""

import json
import math
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright

import config

SEEN_FILE = Path(__file__).parent / "seen_listings.json"


def _phrase_pattern(phrases):
    """Compile a case-insensitive, word-boundary alternation of phrases."""
    escaped = [re.escape(p) for p in phrases]
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Mileage / odometer extraction
# ---------------------------------------------------------------------------

# Kijiji structured attributes - look for these in the page HTML/text.
# They appear in the listing details sidebar as "Kilometres: 123,456" or
# in JSON-LD / structured data.

# Master regex: finds numbers that look like odometer readings.
# Captures the numeric part. Handles comma/space/dot thousand separators.
# Works for "123,456 km", "123456km", "123 456 km", "123.456 km" (European),
# and also handles "123,456 kms", "123k km" shorthand, miles, etc.
_NUM = r"(\d{1,3}(?:[,.\s]\d{3})*|\d{4,6})"
_KM_UNITS = (
    r"(?:\s*(?:km|kms|kilometres|kilometers|kilom[eè]tres|kilometre|kilometer"
    r"|k\.m\.|k\.m|k m))\b"
)
_MI_UNITS = (
    r"(?:\s*(?:mi|miles|mileage|mile))\b"
)

# "123k" shorthand - e.g. "only 180k on it"
_K_SHORT = r"(\d{2,3})\s*k\b"

# All the different ways people write mileage on Kijiji:
MILEAGE_PATTERNS = [
    # ---- Kijiji structured field (sidebar / attributes table) ----
    re.compile(r"(?:kilometres|kilometers|kilom[eè]tres)\s*[:：]\s*" + _NUM, re.I),
    re.compile(r"(?:mileage|odometer|odo)\s*[:：]\s*" + _NUM, re.I),
    re.compile(r"(?:km|kms)\s*[:：]\s*" + _NUM, re.I),

    # ---- "X km" / "X kms" / "X kilometres" ----
    re.compile(_NUM + _KM_UNITS, re.I),

    # ---- "X miles" (we'll convert later) ----
    re.compile(_NUM + _MI_UNITS, re.I),

    # ---- "odometer reads X" / "odo X" / "showing X" ----
    re.compile(r"(?:odometer|odo|mileage)\s+(?:reads?|shows?|at|is|of|:)\s*" + _NUM, re.I),
    re.compile(r"(?:showing|reads?|currently at|sitting at|just (?:hit|over|under))\s+" + _NUM + r"(?:" + _KM_UNITS + r")?", re.I),

    # ---- "X on the odometer" / "X on the clock" / "X on it" ----
    re.compile(_NUM + r"\s+(?:on the (?:odometer|odo|clock|dash)|on it|klicks)", re.I),

    # ---- "only X km" / "just X km" / "low X km" ----
    re.compile(r"(?:only|just|low|under|below|about|approx(?:imately)?|around|roughly|nearly|barely|less than|fewer than)\s+" + _NUM + _KM_UNITS, re.I),

    # ---- "driven X km" / "has X km" / "with X km" ----
    re.compile(r"(?:driven|has|with|at|total(?:ling)?)\s+" + _NUM + _KM_UNITS, re.I),

    # ---- "X original km" / "X highway km" ----
    re.compile(_NUM + r"\s+(?:original|highway|hwy|city|mostly highway|mostly city|mixed)" + _KM_UNITS, re.I),

    # ---- "Xk" shorthand - "180k", "only 120k" ----
    re.compile(r"(?:only|just|under|about|approx(?:imately)?|around|~|has|with|at|driven)?\s*" + _K_SHORT, re.I),

    # ---- JSON-LD / structured data ----
    re.compile(r'"mileageFromOdometer"\s*:\s*\{\s*"value"\s*:\s*"?' + _NUM, re.I),
    re.compile(r'"vehicleMileage"\s*:\s*"?' + _NUM, re.I),
    re.compile(r'"odometer"\s*:\s*"?' + _NUM, re.I),
    re.compile(r'"numberOfKilometers"\s*:\s*"?' + _NUM, re.I),
    re.compile(r'"mileage"\s*:\s*"?' + _NUM, re.I),

    # ---- Other common formats ----
    re.compile(r"kilo(?:m[eè]trage|metrage)\s*[:：]?\s*" + _NUM, re.I),
    re.compile(_NUM + r"\s*(?:klicks|clicks|klms)", re.I),
    re.compile(r"(?:distance|dist)\s*[:：]\s*" + _NUM, re.I),
]

# Patterns we must NOT treat as mileage (year, price, phone numbers, etc.)
MILEAGE_FALSE_POSITIVE_GUARDS = [
    re.compile(r"\$\s*\d"),               # price
    re.compile(r"\b(?:19|20)\d{2}\b"),     # year (1990-2099)
    re.compile(r"\d{3}[-.\s]\d{3,4}"),     # phone number
    re.compile(r"\b\d{1,3}\s*hp\b", re.I), # horsepower
    re.compile(r"\b\d\.\d\s*[Ll]\b"),      # engine displacement
]


def _clean_number(raw: str) -> int | None:
    """Turn '123,456' or '123 456' or '123.456' into 123456."""
    stripped = re.sub(r"[,\s]", "", raw.strip())
    # Handle European dot-as-thousands: "123.456" -> 123456
    # but "12.5" is probably not an odometer reading so skip it
    if "." in stripped:
        parts = stripped.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            stripped = "".join(parts)
        else:
            return None
    try:
        return int(stripped)
    except ValueError:
        return None


def extract_mileage(text: str) -> int | None:
    """
    Extract the most likely odometer reading in kilometres from listing text.
    Returns km as an integer, or None if nothing found.
    Converts miles to km if the listing uses miles.
    """
    if not text:
        return None

    candidates = []

    for pattern in MILEAGE_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1)
            value = _clean_number(raw)

            if value is None:
                continue

            # "Xk" shorthand: multiply by 1000
            if pattern.pattern.find(_K_SHORT) != -1 or (
                len(raw.strip()) <= 3 and value < 1000
            ):
                # Could be a k shorthand
                context_start = max(0, match.start() - 5)
                context = text[context_start:match.end() + 3].lower()
                if "k" in context and value < 1000:
                    value *= 1000

            # Skip obviously wrong values
            if value < 100:
                continue
            if value > 2_000_000:
                continue

            # Check for years that look like mileage
            context_around = text[max(0, match.start() - 30):match.end() + 30]
            is_year = False
            if 1970 <= value <= 2030:
                # Probably a year, not mileage
                is_year = True
                # Unless it's explicitly labeled as km
                if re.search(r"km|kms|kilometres|kilometers", context_around, re.I):
                    is_year = False

            if is_year:
                continue

            # Check if this looks like miles (convert to km)
            full_match = match.group(0)
            if re.search(r"\b(?:mi|miles?|mileage)\b", full_match, re.I):
                value = int(value * 1.60934)

            candidates.append(value)

    if not candidates:
        return None

    # If we have multiple candidates, prefer the one most likely to be real
    # mileage (typically the larger one from structured data, or the one
    # in the most common range for used cars: 10,000 - 500,000)
    reasonable = [c for c in candidates if 1000 <= c <= 900_000]
    if reasonable:
        # Return the most common value (mode), or the first reasonable one
        from collections import Counter
        counts = Counter(reasonable)
        return counts.most_common(1)[0][0]

    return candidates[0]


# ---------------------------------------------------------------------------
# Safety-status classification  (hundreds of patterns)
# ---------------------------------------------------------------------------

# Unambiguous on their own - if one of these appears anywhere, it's a real
# signal even inside a clause that also trips a "false positive trap" below.
STRONG_MARKER_PATTERN = _phrase_pattern([
    # Core certified/inspected terms
    "safetied",
    "safetyed",
    "saftied",
    "saftyed",
    "safety'd",
    "safetied out",
    "safety certificate",
    "safety cert",
    "safety certification",
    "mvi certified",
    "mvi passed",
    "mvi done",
    "mvi completed",
    "mvi inspection passed",
    "manitoba safety certified",
    "manitoba safety inspection passed",

    # French
    "certificat de securite",
    "certificat de sécurité",
])


# ============================================================
# POSITIVE patterns  –  listing IS safetied or has a current
#                        safety certificate.
# ============================================================
POSITIVE_PATTERN = _phrase_pattern([
    # ---- Core word forms / common misspellings ----
    "safetied",
    "safetyed",
    "saftied",
    "saftyed",
    "safety'd",
    "safetied out",
    "safetyied",
    "safetid",
    "safteyed",
    "safeied",
    "safetiyd",
    "safeteed",
    "safty'd",
    "safitied",
    "safteed",
    "safteied",

    # ---- Passed / approved ----
    "safety passed",
    "passed safety",
    "passed the safety",
    "passed mb safety",
    "passed manitoba safety",
    "passed provincial safety",
    "passed prov safety",
    "safety approved",
    "approved safety",
    "passed inspection",
    "passed the inspection",
    "inspection passed",
    "passed safety inspection",
    "safety inspection passed",
    "passed mvi",
    "mvi passed",
    "mvi done",
    "mvi completed",
    "mvi certified",
    "passed vehicle inspection",
    "vehicle inspection passed",
    "passed the vehicle inspection",
    "inspection complete",
    "inspection completed",
    "inspection done",
    "safety done",
    "safety completed",
    "safety complete",
    "safety is done",
    "safety has been done",
    "safety was done",
    "safety already done",
    "safety's done",
    "done the safety",
    "got the safety",
    "got the safety done",
    "got safety done",
    "has passed safety",
    "has passed the safety",

    # ---- Certificate / cert ----
    "safety certificate",
    "safety cert",
    "safety certification",
    "safety certificate included",
    "safety cert included",
    "has a safety certificate",
    "has safety certificate",
    "has a valid safety",
    "has valid safety",
    "has its safety",
    "has a safety",
    "has the safety",
    "has safety",
    "have a safety",
    "have safety",
    "comes with safety",
    "comes with a safety",
    "comes with safety certificate",
    "comes with safety cert",
    "comes with the safety",
    "includes safety",
    "includes a safety",
    "includes the safety",
    "includes safety certificate",
    "includes safety cert",
    "including safety",
    "safety included",
    "cert included",
    "safety cert included",
    "certificate included",
    "with safety",
    "with a safety",
    "with safety certificate",
    "with safety cert",
    "with the safety",
    "w safety",
    "w/ safety",
    "w/safety",

    # ---- Freshness / recency ----
    "new safety",
    "new safety certificate",
    "new safety cert",
    "brand new safety",
    "fresh safety",
    "fresh safety certificate",
    "fresh safety cert",
    "current safety",
    "valid safety",
    "good safety",
    "clean safety",
    "up to date safety",
    "up-to-date safety",
    "recent safety",
    "recently safetied",
    "just safetied",
    "just got safetied",
    "freshly safetied",
    "newly safetied",
    "recently safety'd",
    "just got safety",
    "just got the safety",
    "just passed safety",
    "safety is current",
    "safety is valid",
    "safety is good",
    "safety is still valid",
    "safety is still good",
    "safety still valid",
    "safety still good",
    "safety still current",

    # ---- Manitoba-specific ----
    "manitoba safety",
    "mb safety",
    "manitoba safety passed",
    "mb safety passed",
    "passed mb safety",
    "safety certified in manitoba",
    "manitoba certified",
    "mb certified",
    "manitoba inspection",
    "mb inspection",
    "manitoba vehicle inspection",
    "mb vehicle inspection",
    "manitoba safety inspection",
    "mb safety inspection",
    "provincial safety",
    "provincial inspection",
    "prov safety",
    "prov inspection",

    # ---- Date-bound ----
    "safety until",
    "safety till",
    "safety good until",
    "safety good till",
    "safety valid until",
    "safety valid till",
    "safety expires",
    "safety expiry",
    "safety exp",
    "safety good for",
    "safety good thru",
    "safety good through",

    # ---- Ready-to-drive ----
    "comes safetied",
    "sold safetied",
    "safetied and ready",
    "safetied and ready to go",
    "safetied and ready to drive",
    "safetied ready to go",
    "drive away safetied",
    "safetied and on the road",
    "road ready safetied",
    "road ready with safety",
    "road worthy",
    "roadworthy",
    "ready for the road with safety",
    "safetied and e-tested",
    "safetied and etested",
    "safety and e-test",
    "safety and etest",
    "safetied and emission",
    "safetied and emissions",
    "safety and emission",
    "safety and emissions",
    "safety and emissioned",

    # ---- Dealer / sale framing ----
    "price includes safety",
    "price includes the safety",
    "price with safety",
    "asking price includes safety",
    "asking includes safety",
    "will provide safety",
    "providing safety",
    "provide a safety",
    "comes with a fresh safety",
    "comes freshly safetied",
    "will come safetied",
    "will be sold safetied",
    "selling safetied",
    "selling with safety",
    "sold with safety",
    "sold with a safety",
    "this vehicle is safetied",
    "this car is safetied",
    "vehicle is safetied",
    "car is safetied",
    "truck is safetied",
    "van is safetied",
    "suv is safetied",
    "it is safetied",
    "it's safetied",
    "its safetied",
    "already safetied",
    "been safetied",
    "has been safetied",
    "was safetied",
    "got safetied",
    "gets safetied",
    "fully safetied",
    "fully inspected",
    "fully safety inspected",
    "fully certified",
    "certified and safetied",
    "safetied and certified",
    "inspected and certified",
    "certified and inspected",

    # ---- Abbreviations / shorthand ----
    "sfty",
    "sfty'd",
    "sfty cert",
    "sfty included",
    "sfty done",
    "sftied",
    "sftyd",
    "sfy",
    "sfy'd",

    # ---- French / bilingual ----
    "securite valide",
    "sécurité valide",
    "securite incluse",
    "sécurité incluse",
    "securite fraiche",
    "sécurité fraîche",
    "avec securite",
    "avec sécurité",
    "certificat securite",
    "certificat sécurité",
    "inspection reussie",
    "inspection réussie",
    "inspection passee",
    "inspection passée",
    "inspecte et certifie",
    "inspecté et certifié",
])


# Promises about the future, not a current status - don't count as positive
# unless a STRONG_MARKER also appears somewhere in the text.
TENTATIVE_POSITIVE_PATTERN = _phrase_pattern([
    "will be safetied",
    "will safety it",
    "will safety before",
    "will safety upon",
    "will get safetied",
    "will get safety",
    "will get the safety",
    "will have safety",
    "will have it safetied",
    "will include safety",
    "will come with safety",
    "will provide safety",
    "will do safety",
    "will do the safety",
    "will pass safety",
    "can safety it",
    "can safety",
    "can be safetied",
    "can get safetied",
    "can get safety",
    "can get the safety",
    "can get it safetied",
    "can provide safety",
    "can do safety",
    "can do the safety",
    "could safety it",
    "could be safetied",
    "safety before pickup",
    "safety at extra cost",
    "safety at additional cost",
    "safety for extra",
    "safety for additional",
    "safety available for",
    "safety upon request",
    "safety on request",
    "safety negotiable",
    "safety extra",
    "safety is extra",
    "safety if needed",
    "safety if you want",
    "safety if required",
    "safety if desired",
    "safety at buyer's expense",
    "safety at buyers expense",
    "safety at your expense",
    "safety at cost",
    "add safety for",
    "add the safety for",
    "optional safety",
    "safety optional",
    "safety available",
    "safety can be arranged",
    "safety can be done",
    "safety can be provided",
    "safety can be included",
    "about to be safetied",
    "getting safetied",
    "getting the safety",
    "being safetied",
    "in for safety",
    "going in for safety",
    "going for safety",
    "booked for safety",
    "scheduled for safety",
    "awaiting safety",
    "pending safety",
    "safety pending",
    "safety in progress",
])


# ============================================================
# NEGATIVE patterns  –  listing is NOT safetied.
# ============================================================
NEGATIVE_PATTERN = _phrase_pattern([
    # ---- Direct negation ----
    "not safetied",
    "not saftied",
    "not safetyed",
    "not safety'd",
    "isn't safetied",
    "isnt safetied",
    "is not safetied",
    "hasn't been safetied",
    "hasnt been safetied",
    "has not been safetied",
    "wasn't safetied",
    "wasnt safetied",
    "was not safetied",
    "won't be safetied",
    "wont be safetied",
    "will not be safetied",
    "never been safetied",
    "never safetied",
    "never had safety",
    "never had a safety",
    "never been through safety",

    # ---- "no safety" family ----
    "no safety",
    "no safety certificate",
    "no safety cert",
    "no mvi",
    "no inspection",
    "no vehicle inspection",
    "no current safety",
    "no valid safety",
    "no active safety",
    "no existing safety",
    "no safety on it",
    "no safety on the vehicle",
    "no safety whatsoever",
    "no safety at all",

    # ---- "without" family ----
    "without safety",
    "without a safety",
    "without the safety",
    "without safety certificate",
    "without safety cert",
    "without mvi",
    "without inspection",

    # ---- "missing" / "lacking" ----
    "missing safety",
    "missing a safety",
    "missing the safety",
    "missing safety certificate",
    "missing safety cert",
    "lacking safety",
    "lacks safety",
    "lacks a safety",

    # ---- "does not / doesn't" family ----
    "doesn't come with safety",
    "does not come with safety",
    "doesn't include safety",
    "does not include safety",
    "doesn't have safety",
    "does not have safety",
    "doesn't have a safety",
    "does not have a safety",
    "doesn't have the safety",
    "does not have the safety",

    # ---- Expired / failed ----
    "safety expired",
    "expired safety",
    "lapsed safety",
    "safety has expired",
    "safety is expired",
    "safety lapsed",
    "expired mvi",
    "mvi expired",
    "inspection expired",
    "expired inspection",
    "out of safety",
    "safety ran out",
    "safety run out",
    "safety no longer valid",
    "safety no longer good",
    "safety no longer current",
    "safety is no longer valid",
    "safety is no longer good",
    "failed safety",
    "failed the safety",
    "failed mvi",
    "failed inspection",
    "failed the inspection",
    "failed mb safety inspection",
    "failed manitoba safety",
    "didn't pass safety",
    "did not pass safety",
    "didn't pass the safety",
    "did not pass the safety",
    "didn't pass inspection",
    "did not pass inspection",
    "will not pass safety",
    "won't pass safety",
    "wont pass safety",
    "wouldn't pass safety",
    "wouldnt pass safety",
    "would not pass safety",
    "may not pass safety",
    "might not pass safety",
    "probably won't pass safety",
    "probably wont pass safety",
    "unlikely to pass safety",
    "doubt it will pass safety",
    "needs work to pass safety",

    # ---- As-is family ----
    "as is",
    "as-is",
    "as is where is",
    "as-is where-is",
    "sold as is",
    "sold as-is",
    "selling as is",
    "selling as-is",
    "private sale as is",
    "no warranty as is",
    "price is as is",
    "price reflects as is",
    "priced as is",
    "listed as is",
    "being sold as is",
    "goes as is",
    "going as is",
    "cash and carry as is",
    "take it as is",

    # ---- Buyer-responsibility / needs-safety ----
    "buyer responsible for safety",
    "buyer's responsibility to safety",
    "buyers responsibility to safety",
    "buyer responsible for inspection",
    "buyer to get safety",
    "buyer to safety",
    "buyer to arrange safety",
    "buyer to arrange inspection",
    "buyer to obtain safety",
    "buyer must safety",
    "buyer must get safety",
    "buyer needs to safety",
    "buyer needs to get safety",
    "your responsibility to safety",
    "you need to safety it",
    "you need to get safety",
    "you will need to safety",
    "needs safety to drive",
    "need safety to drive",
    "needs safety before driving",
    "requires safety",
    "requires a safety",
    "requires safety inspection",
    "requires an inspection",
    "requires mvi",
    "must be safetied",
    "must be safetied by buyer",
    "must safety",
    "must get safety",
    "must obtain safety",
    "needs a safety",
    "needs safety",
    "needs the safety",
    "needs to be safetied",
    "needs safetying",
    "need to be safetied",
    "need a safety",
    "need safety",
    "needs to pass safety",
    "needs an inspection",
    "needs inspection",
    "needs mvi",
    "needs a mvi",
    "will need safety",
    "will need a safety",
    "will need to be safetied",
    "will need the safety",
    "would need safety",
    "would need a safety",
    "would need to be safetied",
    "still needs safety",
    "still needs a safety",
    "still needs to be safetied",
    "still requires safety",

    # ---- Parts / mechanic-special / not driveable ----
    "parts only",
    "parts car",
    "parts truck",
    "parts van",
    "parts vehicle",
    "for parts",
    "for parts only",
    "parting out",
    "parting it out",
    "being parted out",
    "breaking for parts",
    "mechanic special",
    "mechanics special",
    "mechanic's special",
    "fixer upper",
    "fixer-upper",
    "project car",
    "project truck",
    "project van",
    "project vehicle",
    "winter project",
    "summer project",
    "barn find",
    "field car",
    "yard car",
    "not driveable",
    "not drivable",
    "not road worthy",
    "not roadworthy",
    "not road-worthy",
    "not street legal",
    "not street-legal",
    "cannot be driven",
    "can't be driven",
    "can not be driven",
    "does not run",
    "doesn't run",
    "does not drive",
    "doesn't drive",
    "non runner",
    "non-runner",
    "non running",
    "non-running",
    "doesn't start",
    "does not start",
    "won't start",
    "wont start",
    "will not start",
    "not running",
    "not starting",
    "not operational",
    "inoperable",
    "inop",
    "scrap",
    "salvage",
    "salvage title",
    "write off",
    "write-off",
    "writeoff",
    "written off",
    "total loss",
    "totalled",
    "totaled",
    "flood damage",
    "flood damaged",
    "fire damage",
    "fire damaged",
    "rebuilt status",
    "rebuilt title",

    # ---- No registration / not plated ----
    "unregistered",
    "not registered",
    "not plated",
    "no plates",
    "no registration",
    "off road only",
    "off-road only",

    # ---- French / bilingual ----
    "tel quel",
    "vendu tel quel",
    "sans securite",
    "sans sécurité",
    "pas de securite",
    "pas de sécurité",
    "aucune securite",
    "aucune sécurité",
    "securite expiree",
    "sécurité expirée",
    "securite echouee",
    "sécurité échouée",
    "pour les pieces",
    "pour les pièces",
    "pieces seulement",
    "pièces seulement",
])


# Clauses matching these should NOT count toward either side unless a
# STRONG_MARKER is also present.
TRAP_PATTERN = _phrase_pattern([
    # Vehicle feature talk
    "safety feature",
    "safety features",
    "safety rating",
    "safety ratings",
    "safety recall",
    "safety recalls",
    "safety system",
    "safety systems",
    "safety technology",
    "safety tech",
    "safety package",
    "safety suite",
    "safety equipment",
    "safety airbag",
    "safety airbags",
    "advanced safety",
    "active safety",
    "passive safety",
    "safety assist",
    "safety sense",
    "safety shield",
    "safety pilot",
    "safety connect",
    "eyesight safety",
    "honda sensing safety",
    "toyota safety sense",
    "nissan safety shield",
    "subaru eyesight",
    "star safety system",
    "crash safety",
    "collision safety",
    "pedestrian safety",
    "occupant safety",
    "driver safety",
    "passenger safety",
    "child safety",

    # Physical parts
    "child safety lock",
    "child safety locks",
    "safety belt",
    "safety belts",
    "safety glass",
    "safety latch",
    "safety latches",
    "safety harness",
    "safety cage",
    "safety cell",
    "safety restraint",
    "safety restraints",
    "safety net",

    # Vague / non-committal mentions
    "drive safely",
    "drive it home safely",
    "safely stored",
    "safely kept",
    "stored safely",
    "kept safely",
    "safe car",
    "safe vehicle",
    "safe to drive",
    "very safe",
    "so safe",
    "safety first",

    # Buyer-go-get-it-yourself phrasing (negative territory, not positive)
    "safety inspection recommended",
    "arrange your own safety",
    "get your own safety",
    "please get your own safety",
    "bring it for safety",
    "take it for safety",
    "get it safetied yourself",
    "recommend getting a safety",
    "suggest getting safety",
    "advise getting safety",
    "should get a safety",
    "should be safetied",
    "should safety it",
    "might need safety",
    "may need safety",
    "could need safety",
    "could use a safety",
    "up to buyer to safety",
    "up to the buyer to safety",
    "up to you to safety",
    "your call on safety",

    # Kijiji platform phrases / safety tips
    "kijiji auto safety",
    "kijiji's auto safety",
    "kijiji safety",
    "auto safety program",
    "safety program",
    "safety tips",
    "safety center",
    "safety centre",
    "safety checklist",
    "trust and safety",
])


# "as is" followed by one of these is describing normal wear, not a
# no-safety private sale.
AS_IS_EXCEPTION_PATTERN = _phrase_pattern([
    "as is normal",
    "as is typical",
    "as is expected",
    "as is common",
    "as is usual",
    "as is standard",
    "as is customary",
    "as is tradition",
    "as is the case",
    "as is the norm",
    "as is to be expected",
    "as is often the case",
    "as is par for the course",
])


def split_clauses(text: str) -> list[str]:
    """Split on sentence/clause boundaries so a negative word in one clause
    can't combine with a positive claim in a different one."""
    return re.split(r"[.,;:\n\r]+|\s[-\u2013\u2014]\s|\u2022", text)


def classify_safety(text: str) -> str:
    """Returns 'SAFETIED', 'NOT_SAFETIED', or 'UNKNOWN'."""
    if not text:
        return "UNKNOWN"

    found_positive = False
    found_negative = False

    for clause in split_clauses(text):
        clause = clause.strip()

        if not clause:
            continue

        has_strong_marker = bool(STRONG_MARKER_PATTERN.search(clause))

        # Skip clauses about vehicle safety features (airbags, etc.)
        # unless a strong inspection-related marker is also present.
        if TRAP_PATTERN.search(clause) and not has_strong_marker:
            continue

        negative_clause = clause

        if AS_IS_EXCEPTION_PATTERN.search(clause):
            negative_clause = AS_IS_EXCEPTION_PATTERN.sub("", clause)

        # Check negative FIRST for this clause
        clause_is_negative = bool(NEGATIVE_PATTERN.search(negative_clause))

        if clause_is_negative:
            found_negative = True
            # If the clause is negative, do NOT also count any positive
            # match from this same clause. "not safetied" contains
            # "safetied" but the negative meaning dominates.
            continue

        # Tentative positives (promises, not current state) should NOT
        # count as positive regardless of strong markers.
        if TENTATIVE_POSITIVE_PATTERN.search(clause):
            continue

        if POSITIVE_PATTERN.search(clause):
            found_positive = True

    if found_negative:
        return "NOT_SAFETIED"

    if found_positive:
        return "SAFETIED"

    return "UNKNOWN"



# ---------------------------------------------------------------------------
# Sold-badge detection
# ---------------------------------------------------------------------------

SOLD_PATTERN = re.compile(r"^\s*sold\b", re.IGNORECASE)


def looks_sold(title: str, card_text: str) -> bool:
    return bool(
        SOLD_PATTERN.search(title or "")
        or SOLD_PATTERN.search(card_text or "")
    )


# ---------------------------------------------------------------------------
# Location: real distance from Winnipeg only, place-name fallback otherwise
# ---------------------------------------------------------------------------

COORD_PATTERNS = [
    re.compile(
        r'"latitude"\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*"longitude"\s*:\s*(-?\d{1,3}\.\d+)'
    ),
    re.compile(
        r'"lat"\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*"lng"\s*:\s*(-?\d{1,3}\.\d+)'
    ),
    re.compile(
        r'"lat"\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*"lon"\s*:\s*(-?\d{1,3}\.\d+)'
    ),
    re.compile(
        r"data-latitude=[\"'](-?\d{1,3}\.\d+)[\"']\s+data-longitude=[\"'](-?\d{1,3}\.\d+)[\"']"
    ),
    re.compile(
        r"latitude=(-?\d{1,3}\.\d+)&longitude=(-?\d{1,3}\.\d+)"
    ),
    re.compile(
        r'"geo"\s*:\s*\{[^}]*"latitude"\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*"longitude"\s*:\s*(-?\d{1,3}\.\d+)'
    ),
]


def extract_coordinates(html: str):
    for pattern in COORD_PATTERNS:
        match = pattern.search(html or "")

        if match:
            try:
                return float(match.group(1)), float(match.group(2))
            except ValueError:
                continue

    return None


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dlambda / 2) ** 2
    )

    return 2 * radius * math.asin(math.sqrt(a))


def resolve_location(detail: dict, fallback_text: str):
    """Returns (status, distance_km_or_None, source) where status is
    'IN_RANGE', 'OUT_OF_RANGE', or 'UNKNOWN'.

    Uses Winnipeg center only (no Oakbank).
    """

    coords = detail.get("coords")

    if coords:
        distance = haversine_km(
            config.WINNIPEG_CENTER_LAT,
            config.WINNIPEG_CENTER_LNG,
            coords[0],
            coords[1],
        )

        status = (
            "IN_RANGE"
            if distance <= config.RADIUS_KM
            else "OUT_OF_RANGE"
        )

        return (
            status,
            distance,
            "coordinates (Winnipeg)",
        )

    combined_text = (
        (detail.get("text") or "")
        + " "
        + (fallback_text or "")
    ).lower()

    for place in config.FAR_PLACE_DENYLIST:
        if place in combined_text:
            return "OUT_OF_RANGE", None, place

    for place in config.NEARBY_PLACE_ALLOWLIST:
        if place in combined_text:
            return "IN_RANGE", None, place

    return "UNKNOWN", None, None


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

EXTRACT_LISTINGS_JS = """
() => {
    function findHref(startNode) {
        let node = startNode;

        for (let i = 0; i < 8 && node; i++) {
            if (
                node.tagName === 'A' &&
                node.href &&
                node.href.includes('/v-cars-trucks/')
            ) {
                return node.href;
            }

            const nested = node.querySelector
                ? node.querySelector('a[href*="/v-cars-trucks/"]')
                : null;

            if (nested) {
                return nested.href;
            }

            node = node.parentElement;
        }

        return null;
    }

    function findNearbyPrice(startNode, title) {
        let node = startNode;

        for (let i = 0; i < 8 && node; i++) {
            const text = node.textContent;
            const idx = text.indexOf(title);

            if (idx !== -1) {
                const before = text.slice(
                    Math.max(0, idx - 60),
                    idx
                );

                const match = before.match(
                    /\\$\\d{1,3}(?:,\\d{3})*/
                );

                if (match) {
                    return match[0];
                }
            }

            node = node.parentElement;
        }

        return null;
    }

    const seen = new Set();
    const results = [];
    const headings = document.querySelectorAll('h2, h3, h4');

    for (const heading of headings) {
        const title = heading.textContent.trim();

        if (!title || /^results\\b/i.test(title)) {
            continue;
        }

        const href = findHref(heading);

        if (!href || seen.has(href)) {
            continue;
        }

        seen.add(href);

        const price = findNearbyPrice(heading, title);

        let cardText = title;
        let node = heading;

        for (let i = 0; i < 4 && node; i++) {
            cardText = node.textContent;
            node = node.parentElement;
        }

        results.push({
            title: title,
            link: href,
            price: price || "price not listed",
            cardText: cardText,
        });
    }

    return results;
}
"""


def fetch_listings(search_url: str, page) -> list[dict]:
    for attempt in range(1, 4):
        try:
            page.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=45000,
            )
        except Exception:
            pass

        try:
            page.wait_for_selector('a[href*="/v-cars-trucks/"]', timeout=20000, state="attached")
        except Exception:
            pass

        page.wait_for_timeout(1000)

        listings = []
        for _ in range(6):
            listings = page.evaluate(EXTRACT_LISTINGS_JS)
            if listings:
                break
            page.wait_for_timeout(1500)

        if listings:
            return listings

        if attempt < 3:
            time.sleep(3)

    return []


def fetch_listing_detail(url: str, page) -> dict:
    """Opens a single listing's own page and returns its clean ad text (for
    safety-wording and mileage checks, specifically ad title, attributes, and seller description)
    plus any coordinates found."""

    for attempt in range(1, 3):
        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=35000,
            )
        except Exception:
            pass

        page.wait_for_timeout(1500)

        html = page.content()

        if "upstream request timeout" in html.lower() and attempt < 2:
            time.sleep(2)
            continue

        try:
            body_text = page.evaluate("""() => {
                const title = document.querySelector('h1')?.innerText || '';

                const h1 = document.querySelector('h1');
                let topSection = '';
                if (h1 && h1.parentElement) {
                    topSection = h1.parentElement.innerText;
                }

                let desc = '';
                const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4'));
                for (const h of headings) {
                    if (h.textContent.trim().toLowerCase() === 'description') {
                        let curr = h.parentElement;
                        if (curr) {
                            if (curr.innerText.length < 30 && curr.nextElementSibling) {
                                desc = curr.nextElementSibling.innerText;
                            } else {
                                desc = curr.innerText;
                            }
                        }
                        break;
                    }
                }

                if (desc.length < 20) {
                    const main = (document.querySelector('main') || document.body).cloneNode(true);
                    main.querySelectorAll('footer, header, nav, [class*="similar"], [class*="recommend"], [class*="sponsored"], [class*="carousel"], ul').forEach(el => el.remove());
                    desc = main.innerText;
                }

                return title + "\\n" + topSection + "\\n" + desc;
            }""")
        except Exception:
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = ""

        return {
            "html": html,
            "text": body_text,
            "coords": extract_coordinates(html),
        }

    return {
        "html": "",
        "text": "",
        "coords": None,
    }


def parse_price(price_str: str):
    digits = re.sub(r"[^\d]", "", price_str or "")
    return int(digits) if digits else None


def load_seen_ids() -> set:
    if SEEN_FILE.exists():
        return set(
            json.loads(
                SEEN_FILE.read_text(
                    encoding="utf-8"
                )
            )
        )

    return set()


def save_seen_ids(seen_ids: set) -> None:
    SEEN_FILE.write_text(
        json.dumps(sorted(seen_ids)),
        encoding="utf-8",
    )


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body)

    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = ", ".join(config.NOTIFY_EMAILS)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()

        server.login(
            config.GMAIL_ADDRESS,
            config.GMAIL_APP_PASSWORD,
        )

        server.sendmail(
            config.GMAIL_ADDRESS,
            config.NOTIFY_EMAILS,
            msg.as_string(),
        )


def format_listing_line(item: dict) -> str:
    distance = item.get("distance_km")

    distance_note = (
        f", ~{distance:.1f} km away"
        if distance is not None
        else ""
    )

    mileage = item.get("mileage_km")
    mileage_note = (
        f", {mileage:,} km"
        if mileage is not None
        else ", mileage unknown"
    )

    return (
        f"[{item['safety_status']}] "
        f"{item['title']} - "
        f"{item['price']}"
        f"{mileage_note}"
        f"{distance_note}\n"
        f"{item['link']}"
    )


def check_once() -> None:
    now_str = datetime.now().strftime("%I:%M:%S %p")
    print(f"\n========================================================")
    print(f"  Starting Kijiji Search Routine at {now_str}")
    print(f"========================================================")

    is_first_run = not SEEN_FILE.exists()
    seen_ids = load_seen_ids()
    new_listings = []
    all_matching_this_run = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for search_url in config.KIJIJI_SEARCH_URLS:
            pages_to_check = getattr(config, "PAGES_TO_CHECK", 3)
            seen_links_this_run = set()

            for page_num in range(1, pages_to_check + 1):
                if page_num == 1:
                    url_to_fetch = search_url
                else:
                    if "?" in search_url:
                        base_url, query_str = search_url.split("?", 1)
                        query_part = f"?{query_str}"
                    else:
                        base_url, query_part = search_url, ""

                    if "/page-" in base_url:
                        base_url = re.sub(r'/page-\d+/', f'/page-{page_num}/', base_url)
                    else:
                        base_url = re.sub(r'/([^/]+)$', f'/page-{page_num}/\\1', base_url)

                    url_to_fetch = base_url + query_part

                print(f"\n[Page {page_num}/{pages_to_check}] Fetching listings...")
                try:
                    page_listings = fetch_listings(
                        url_to_fetch,
                        page,
                    )
                    if not page_listings:
                        print(f"  No listings found on page {page_num}.")
                        break
                    print(f"  Scanned {len(page_listings)} ad cards on page {page_num}.")
                except Exception as exc:
                    print(
                        f"  [warning] couldn't check "
                        f"{url_to_fetch}: {exc}"
                    )
                    break

                for item in page_listings:
                    link = item.get("link")
                    if not link or link in seen_links_this_run:
                        continue
                    seen_links_this_run.add(link)

                    if looks_sold(
                        item.get("title", ""),
                        item.get("cardText", ""),
                    ):
                        continue

                    price = parse_price(
                        item.get("price", "")
                    )

                    if (
                        price is None
                        or not (
                            config.MIN_PRICE
                            <= price
                            <= config.MAX_PRICE
                        )
                    ):
                        # Outside price range - skip opening detail page
                        continue

                    print(f"  -> Examining: {item.get('title', '?')} ({item.get('price', '')})...")

                    try:
                        detail = fetch_listing_detail(
                            item["link"],
                            page,
                        )
                    except Exception as exc:
                        print(
                            f"     [warning] couldn't open listing "
                            f"{item['link']}: {exc}"
                        )
                        continue

                    time.sleep(
                        config.DETAIL_FETCH_DELAY_SECONDS
                    )

                    # ---- Location check ----
                    (
                        loc_status,
                        distance_km,
                        _loc_source,
                    ) = resolve_location(
                        detail,
                        item.get("cardText", ""),
                    )

                    if loc_status == "OUT_OF_RANGE":
                        print(f"     [skip] Location out of 50km radius ({_loc_source})")
                        continue

                    if (
                        loc_status == "UNKNOWN"
                        and config.UNKNOWN_LOCATION_POLICY == "exclude"
                    ):
                        print(f"     [skip] Location unknown")
                        continue

                    # ---- Mileage check ----
                    combined_text = (
                        (detail.get("text") or "")
                        + " "
                        + (detail.get("html") or "")
                    )
                    mileage_km = extract_mileage(combined_text)

                    if (
                        mileage_km is not None
                        and mileage_km > config.MAX_KM
                    ):
                        print(
                            f"     [skip] {mileage_km:,} km exceeds "
                            f"{config.MAX_KM:,} km limit"
                        )
                        continue

                    # ---- Safety check ----
                    safety_status = classify_safety(
                        detail.get("text", "")
                    )

                    if safety_status == "NOT_SAFETIED":
                        print(
                            f"     [skip] NOT SAFETIED"
                        )
                        continue

                    if (
                        safety_status == "UNKNOWN"
                        and not config.INCLUDE_UNKNOWN_SAFETY
                    ):
                        print(f"     [skip] Safety unknown")
                        continue

                    tier = {
                        "SAFETIED": 1,
                        "UNKNOWN": 2,
                    }[safety_status]

                    print(f"     [MATCH] Tier {tier} ({safety_status})!")

                    record = dict(item)
                    record["safety_status"] = safety_status
                    record["location_status"] = loc_status
                    record["distance_km"] = distance_km
                    record["mileage_km"] = mileage_km
                    record["tier"] = tier

                    all_matching_this_run.append(record)

                    if record["link"] not in seen_ids:
                        seen_ids.add(record["link"])
                        new_listings.append(record)

        browser.close()

    print("\n--------------------------------------------------------")
    emailable = sorted(
        [
            r
            for r in new_listings
            if r["tier"] in (1, 2)
        ],
        key=lambda r: r["tier"],
    )

    if emailable:
        print(
            f"Found {len(emailable)} NEW listing(s) "
            f"worth emailing! Sending email..."
        )

        for item in emailable:
            mileage_str = (
                f"{item['mileage_km']:,} km"
                if item.get("mileage_km") is not None
                else "mileage unknown"
            )
            print(
                f"  * [Tier {item['tier']} - {item['safety_status']}] "
                f"{item['title']} - {item['price']} ({mileage_str})\n"
                f"    {item['link']}\n"
            )

        body = "\n\n".join(
            format_listing_line(item)
            for item in emailable
        )

        try:
            send_email(
                subject=(
                    f"{len(emailable)} new Kijiji car "
                    f"listing(s) near Winnipeg"
                ),
                body=body,
            )
            print("Email sent successfully!")
        except Exception as exc:
            print(f"[warning] Failed to send email: {exc}")

    else:
        print(
            "Search complete: No new matching listings found this check."
        )

    save_seen_ids(seen_ids)
    print("--------------------------------------------------------")


def main() -> None:
    if "--loop" in sys.argv:
        print("========================================================")
        print(f"  Kijiji Car Scraper Running in Continuous Mode")
        print(f"  Checking every {config.CHECK_INTERVAL_MINUTES} minute(s).")
        print(f"  Press Ctrl+C at any time to stop.")
        print("========================================================")

        while True:
            check_once()

            next_run = datetime.now() + timedelta(minutes=config.CHECK_INTERVAL_MINUTES)
            next_run_str = next_run.strftime("%I:%M:%S %p")
            print(
                f"\n[Status: Sleeping] Finished routine check. Next search in {config.CHECK_INTERVAL_MINUTES} minute(s) at {next_run_str}...\n"
            )

            time.sleep(
                config.CHECK_INTERVAL_MINUTES * 60
            )

    else:
        check_once()


if __name__ == "__main__":
    main()