"""
Condition Evaluator Module
Context-aware evaluation of item condition from listing titles, descriptions, and attributes.
Handles negations (e.g. "not broken" vs "broken") and generates a 0-100 confidence score.
"""

import re
from typing import Tuple, List, Dict, Any

# Negation patterns to neutralize before scanning for defect keywords
NEGATION_PATTERNS = [
    r'\b(?:not|isn\'t|is not|never|no|without|hardly|zero)\s+(?:broken|damaged|cracked|defective|faulty|issues|problems|defects|flaws|scratch|scratches)\b',
    r'\b(?:no|nothing|not|zero)\s+(?:missing|parts missing|missing parts)\b',
    r'\b(?:not\s+for\s+parts)\b',
    r'\b(?:does\s+not\s+have\s+(?:any\s+)?(?:issues|problems|defects))\b',
    r'\b(?:has\s+no\s+(?:issues|problems|defects))\b'
]

# Hard defect / parts-only patterns that result in an immediate score of 0
HARD_REJECT_PATTERNS = [
    r'\bfor\s+parts\b',
    r'\bparts\s+(?:only|or\s+repair|/repair)\b',
    r'\b(?:not|doesn\'t|does not|won\'t)\s+(?:working|work|turn\s+on|power\s+on|start|boot)\b',
    r'\b(?:broken|defective|faulty|dead|bricked|wrecked)\b',
    r'\b(?:cracked|smashed|shattered)\s+(?:screen|display|glass|body|casing|housing)\b',
    r'\b(?:water|liquid)\s+damage\b',
    r'\bneeds?\s+(?:repair|fixing|new\s+battery|service)\b',
    r'\bas[- ]is\s+(?:for\s+parts|condition|broken)\b'
]

# High confidence positive indicators
HIGH_POSITIVE_PATTERNS = [
    (r'\btested\s+(?:and\s+)?(?:working|works|functional|100%)\b', 35, "Explicitly tested and working"),
    (r'\b(?:fully|100%)\s+(?:functional|working|operational)\b', 30, "Fully functional"),
    (r'\bworks?\s+(?:perfectly|great|flawlessly|like\s+new|100%)\b', 25, "Works perfectly"),
    (r'\b(?:mint|pristine|flawless|immaculate)\s+condition\b', 25, "Mint/pristine condition"),
    (r'\b(?:brand\s+new|bnib|nib|never\s+used|sealed\s+in\s+box)\b', 30, "Brand new / unused"),
    (r'\blike\s+new\b', 20, "Like new condition"),
    (r'\bexcellent\s+condition\b', 20, "Excellent condition")
]

# Moderate positive indicators
MODERATE_POSITIVE_PATTERNS = [
    (r'\bgood\s+(?:working\s+)?condition\b', 15, "Good condition"),
    (r'\bworks?\s+fine\b', 15, "Works fine"),
    (r'\btested\b', 15, "Tested"),
    (r'\bworking\b', 10, "Working stated"),
    (r'\bbarely\s+used\b', 15, "Barely used"),
    (r'\bgently\s+used\b', 10, "Gently used")
]

# Uncertain / ambiguous condition indicators
UNCERTAIN_PATTERNS = [
    (r'\buntested\b', -30, "Untested item"),
    (r'\bas[- ]is\b', -20, "Sold as-is"),
    (r'\bpowers\s+on\s+(?:only|but\s+not\s+tested)\b', -25, "Powers on only"),
    (r'\b(?:don\'t|do not|not\s+sure)\s+know\s+if\s+it\s+works\b', -30, "Seller unsure if working"),
    (r'\bunable\s+to\s+test\b', -25, "Unable to test"),
    (r'\bmissing\s+(?:cords?|cables?|chargers?|batter(?:y|ies)|power\s+supply|accessories)\b', -15, "Missing accessories/parts"),
    (r'\bestate\s+(?:sale\s+)?find\b', -10, "Estate find (history unknown)")
]

def evaluate_condition(title: str, description: str, attributes: List[Dict[str, Any]] = None) -> Tuple[int, str, str, List[str]]:
    """
    Evaluates item condition score (0 - 100), classification tier, summary reasoning, and evidence snippets.
    
    Returns:
        (score: int, tier: str, summary: str, evidence_list: List[str])
    """
    text = f"{title or ''}\n{description or ''}".lower()
    evidence = []
    
    # 1. First, neutralize negation matches by replacing them with a placeholder
    clean_text = text
    for pat in NEGATION_PATTERNS:
        matches = list(re.finditer(pat, text, flags=re.IGNORECASE))
        for m in matches:
            evidence.append(f"[+] Context verified: '{m.group(0)}'")
        clean_text = re.sub(pat, ' [NEGATION_HANDLED] ', clean_text, flags=re.IGNORECASE)
        
    # 2. Check for Hard Rejects on the cleaned text
    for pat in HARD_REJECT_PATTERNS:
        match = re.search(pat, clean_text, flags=re.IGNORECASE)
        if match:
            # Extract sentence snippet around the match
            start = max(0, match.start() - 20)
            end = min(len(clean_text), match.end() + 20)
            snippet = clean_text[start:end].strip()
            return (0, "HARD_REJECT", f"Hard defect found: '{match.group(0)}'", [f"[-] Defect flag: '...{snippet}...'"])
            
    # 3. Base score starts at neutral (50)
    score = 50
    
    # Check Kijiji attribute metadata if present (e.g. condition: 'usedlikenew', 'usedgood')
    if attributes:
        for attr in attributes:
            if attr.get('canonicalName') == 'condition':
                vals = attr.get('canonicalValues', [])
                if 'new' in vals or 'usedlikenew' in vals:
                    score += 20
                    evidence.append("[+] Kijiji badge: Like New / New")
                elif 'usedgood' in vals:
                    score += 10
                    evidence.append("[+] Kijiji badge: Good Condition")
                elif 'usedfair' in vals:
                    score -= 10
                    evidence.append("[!] Kijiji badge: Fair Condition")
                    
    # 4. Check High Positive matches
    for pat, weight, desc in HIGH_POSITIVE_PATTERNS:
        match = re.search(pat, clean_text, flags=re.IGNORECASE)
        if match:
            score += weight
            evidence.append(f"[+] {desc}: '{match.group(0)}'")
            
    # 5. Check Moderate Positive matches (if not already saturated)
    for pat, weight, desc in MODERATE_POSITIVE_PATTERNS:
        match = re.search(pat, clean_text, flags=re.IGNORECASE)
        if match:
            score += weight
            evidence.append(f"[+] {desc}: '{match.group(0)}'")
            
    # 6. Check Uncertain / Ambiguous indicators
    for pat, penalty, desc in UNCERTAIN_PATTERNS:
        match = re.search(pat, clean_text, flags=re.IGNORECASE)
        if match:
            score += penalty  # penalty is negative
            evidence.append(f"[!] {desc}: '{match.group(0)}'")
            
    # Clamp score between 0 and 100
    score = max(0, min(100, score))
    
    # Determine Tier
    if score >= 85:
        tier = "HOT"
        summary = "Explicitly verified working / excellent condition"
    elif score >= 70:
        tier = "GOOD"
        summary = "Working condition likely good"
    elif score >= 40:
        tier = "VERIFY"
        summary = "Condition ambiguous or unverified - check with seller"
    else:
        tier = "REJECT"
        summary = "Likely defective, untested, or missing critical parts"
        
    return (score, tier, summary, evidence)

if __name__ == '__main__':
    # Test sample cases
    test_cases = [
        ("Fluke 87V Multimeter", "Tested and works perfectly, no missing parts, never dropped. Great condition!"),
        ("Tektronix Oscilloscope", "No issues, not broken at all. Tested working 100%."),
        ("Fluke 789 ProcessMeter", "Broken screen, for parts or repair only. Doesn't turn on."),
        ("AEMC CA6116 Tester", "Estate sale find, powers on but untested otherwise. Sold as-is."),
        ("Milwaukee M18 Fuel Saw", "Lightly used tool, works fine.")
    ]
    
    print("=== CONDITION EVALUATOR TEST CASES ===")
    for title, desc in test_cases:
        score, tier, summary, ev = evaluate_condition(title, desc)
        print(f"\nTitle: {title}")
        print(f"Desc: {desc}")
        print(f"Result -> Score: {score}/100 | Tier: {tier} | Summary: {summary}")
        for e in ev:
            print(f"  {e}")
