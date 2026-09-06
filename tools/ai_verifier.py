"""
AI Verifier Module
Leverages Google Gemini API to audit candidate deals before alerting.
Applies a deep multi-point forensic audit:
  1. Brand Authenticity (detects off-brand clones, generic Amazon junk, "compatible with" fakes)
  2. Completeness / Package Check (detects empty boxes, cases only, accessories, missing main unit)
  3. Operational Integrity & Condition (detects broken/untested/parts-only/as-is traps)
  4. Ad Intent (detects WANTED / ISO ads masquerading as cheap listings)
  5. Security & Account Locks (detects iCloud, MDM, BIOS locks, passcodes)
  6. Exact Model & Spec Tier (detects lower-tier brushed vs Fuel, 12V vs 20V/60V, standard vs industrial)
"""

import os
import json
import logging
import re
import requests
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

@dataclass
class AIVerificationResult:
    is_approved: bool
    verdict: str  # APPROVED, GENERIC_KNOCKOFF, WRONG_MODEL, ACCESSORY_ONLY, DAMAGED_OR_PARTS, WANTED_AD, LOCKED_OR_SUSPICIOUS, UNCERTAIN
    detected_brand: str
    detected_model: str
    confidence: int
    authenticity: str       # GENUINE, AFTERMARKET_CLONE, UNKNOWN
    completeness: str       # COMPLETE_UNIT, COMBO_KIT, BARE_TOOL, ACCESSORY_ONLY, PARTS_ONLY
    condition_status: str   # WORKING, UNTESTED, DEFECTIVE_FOR_PARTS
    seller_intent: str      # SELLING, WANTED_TO_BUY
    risk_factors: List[str]
    reasoning: str

class AIVerifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.ai_cfg = cfg.get('ai_verification', {})
        self.enabled = bool(self.ai_cfg.get('enabled', False))
        self.api_key = os.environ.get('GEMINI_API_KEY', '').strip() or self.ai_cfg.get('api_key', '').strip()
        self.model = self.ai_cfg.get('model', 'gemini-3.6-flash')
        self.min_confidence = int(self.ai_cfg.get('min_confidence', 75))
        self.strict_brand = bool(self.ai_cfg.get('strict_brand_check', True))
        
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def verify_deal(
        self,
        listing_title: str,
        listing_description: str,
        listing_price_cad: float,
        target_name: str,
        target_brand: str,
        target_model_numbers: List[str],
        ebay_resale_usd: float
    ) -> AIVerificationResult:
        """
        Runs a forensic AI audit on candidate listings to eliminate false positives,
        protecting against knockoffs, broken units, accessory traps, and mismatched models.
        """
        if not self.enabled or not self.api_key:
            return AIVerificationResult(
                is_approved=True,
                verdict="BYPASSED",
                detected_brand="Unknown",
                detected_model="Unknown",
                confidence=100,
                authenticity="UNKNOWN",
                completeness="UNKNOWN",
                condition_status="UNKNOWN",
                seller_intent="SELLING",
                risk_factors=[],
                reasoning="AI verification is disabled in config."
            )

        prompt = f"""You are a master product authenticator and professional resale arbitrage auditor.
A scraping engine found a local Kijiji classified ad and matched it to a high-value product target.
Your mission is to perform a rigorous 6-point verification to decide if this listing is 100% genuine and safe to flip for profit, or a trap/false positive.

=== TARGET PRODUCT FROM OUR CATALOG ===
Target Name: {target_name}
Target Brand: {target_brand}
Expected Model Numbers/Codes: {', '.join(target_model_numbers) if target_model_numbers else 'N/A'}
Target Resale Value: ${ebay_resale_usd:.2f} USD

=== LIVE KIJIJI LISTING TO AUDIT ===
Title: {listing_title}
Asking Price: ${listing_price_cad:.2f} CAD
Description:
{listing_description[:1200] if listing_description else 'No description provided.'}

=== 6-POINT AUDIT CHECKLIST ===
1. BRAND AUTHENTICITY:
   - Is this genuine original {target_brand}, or a cheap generic/knockoff/clone?
   - Beware of "fits {target_brand}", "for {target_brand}", or generic unbranded items claiming compatibility (e.g. unbranded ELM327 OBD2 reader vs Autel scanner; aftermarket knockoff batteries/chargers).
2. COMPLETENESS & WHOLE UNIT:
   - Is the actual working main tool/device included?
   - Trap check: Is it an "EMPTY BOX ONLY", "CARRYING CASE ONLY", "MANUAL ONLY", "CABLE ONLY", or "CHARGER ONLY"?
   - If target is a combo kit, is the full kit present or just a single piece?
3. CONDITION & OPERATIONAL INTEGRITY:
   - Does it power on and work properly?
   - Reject immediately if seller mentions: "untested", "for parts or repair", "doesn't turn on", "as-is", "broken", "needs repair", "smoked/burnt", "water damaged", "glitching".
4. SELLER INTENT (WANTED AD CHECK):
   - Is the seller actually selling this item?
   - Reject if this is a "WANTED", "IN SEARCH OF", "ISO", or "LOOKING TO BUY" post where someone is seeking the item.
5. ACCOUNT / DEVICE LOCKS:
   - For electronics: Are there iCloud locks, Google FRP locks, MDM profiles, BIOS passwords, or passcode locks?
6. EXACT SPECIFICATION & MODEL TIER:
   - Is it the exact target model ({target_name}), or an inferior, cheaper lower-tier variant?
   - E.g. Brushed vs Brushless / Fuel; 12V vs 18V/20V; Standard multimeter vs True RMS Industrial.

=== VERDICT DEFINITIONS ===
- "APPROVED": Legitimate, working, genuine brand, exact/equivalent complete unit.
- "GENERIC_KNOCKOFF": Off-brand, clone, aftermarket copy, or unbranded item.
- "ACCESSORY_ONLY": Case, box, battery, cable, or parts without the main unit.
- "DAMAGED_OR_PARTS": Broken, defective, untested, or sold for parts/repair.
- "WANTED_AD": Person wants to buy the item, not selling it.
- "WRONG_MODEL": Genuine brand, but a different or lower-value model.
- "LOCKED_OR_SUSPICIOUS": Software locked, stolen indicators, or suspicious discrepancies.
- "UNCERTAIN": Insufficient details to confirm authenticity or completeness.

Respond ONLY with a valid JSON object matching this exact structure:
{{
  "verdict": "APPROVED" | "GENERIC_KNOCKOFF" | "ACCESSORY_ONLY" | "DAMAGED_OR_PARTS" | "WANTED_AD" | "WRONG_MODEL" | "LOCKED_OR_SUSPICIOUS" | "UNCERTAIN",
  "confidence_score": 0-100,
  "detected_brand": "Brand or Generic/Unbranded",
  "detected_model": "Model number or Unknown",
  "authenticity": "GENUINE" | "AFTERMARKET_CLONE" | "UNKNOWN",
  "completeness": "COMPLETE_UNIT" | "COMBO_KIT" | "BARE_TOOL" | "ACCESSORY_ONLY" | "PARTS_ONLY",
  "condition_status": "WORKING" | "UNTESTED" | "DEFECTIVE_FOR_PARTS",
  "seller_intent": "SELLING" | "WANTED_TO_BUY",
  "risk_factors": ["risk 1", "risk 2"],
  "reasoning": "Clear, concise 1-2 sentence explanation of your decision"
}}
"""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json"
            }
        }
        
        candidate_models = [self.model, "gemini-2.5-flash", "gemini-1.5-flash"]
        # Deduplicate while preserving order
        candidate_models = list(dict.fromkeys(candidate_models))
        
        last_error = ""
        resp = None

        for mod in candidate_models:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{mod}:generateContent?key={self.api_key}"
            try:
                resp = self.session.post(endpoint, json=payload, timeout=25)
                if resp.status_code == 200:
                    break
                elif resp.status_code in [503, 429]:
                    logging.warning(f"Model {mod} returned HTTP {resp.status_code}. Falling back to next available model...")
                    last_error = f"HTTP {resp.status_code} on {mod}"
                    continue
                else:
                    last_error = f"HTTP {resp.status_code} ({resp.text[:150]})"
                    break
            except Exception as e:
                last_error = str(e)
                continue

        if not resp or resp.status_code != 200:
            logging.warning(f"AI Verification failed across models: {last_error}")
            return AIVerificationResult(
                is_approved=False,
                verdict="UNCERTAIN",
                detected_brand="Unknown",
                detected_model="Unknown",
                confidence=0,
                authenticity="UNKNOWN",
                completeness="UNKNOWN",
                condition_status="UNKNOWN",
                seller_intent="SELLING",
                risk_factors=[last_error],
                reasoning=f"AI API error: {last_error}"
            )

        try:
            data = resp.json()
            candidates = data.get('candidates', [])
            if not candidates:
                return AIVerificationResult(
                    is_approved=False,
                    verdict="UNCERTAIN",
                    detected_brand="Unknown",
                    detected_model="Unknown",
                    confidence=0,
                    authenticity="UNKNOWN",
                    completeness="UNKNOWN",
                    condition_status="UNKNOWN",
                    seller_intent="SELLING",
                    risk_factors=["No candidates returned from API"],
                    reasoning="No candidates returned from AI API."
                )

            part = candidates[0].get('content', {}).get('parts', [{}])[0]
            raw_text = part.get('text', '{}').strip()
            
            parsed = json.loads(raw_text)
            verdict = parsed.get('verdict', 'UNCERTAIN').upper()
            confidence = int(parsed.get('confidence_score', 50))
            reasoning = parsed.get('reasoning', '')
            detected_brand = parsed.get('detected_brand', 'Unknown')
            detected_model = parsed.get('detected_model', 'Unknown')
            authenticity = parsed.get('authenticity', 'UNKNOWN')
            completeness = parsed.get('completeness', 'UNKNOWN')
            condition_status = parsed.get('condition_status', 'UNKNOWN')
            seller_intent = parsed.get('seller_intent', 'SELLING')
            risk_factors = parsed.get('risk_factors', [])
            
            # An item is ONLY approved if it is genuinely APPROVED and confidence meets threshold
            is_approved = (
                (verdict == "APPROVED")
                and (confidence >= self.min_confidence)
                and (seller_intent == "SELLING")
                and (condition_status != "DEFECTIVE_FOR_PARTS")
                and (completeness not in ["ACCESSORY_ONLY", "PARTS_ONLY"])
            )
            
            return AIVerificationResult(
                is_approved=is_approved,
                verdict=verdict,
                detected_brand=detected_brand,
                detected_model=detected_model,
                confidence=confidence,
                authenticity=authenticity,
                completeness=completeness,
                condition_status=condition_status,
                seller_intent=seller_intent,
                risk_factors=risk_factors,
                reasoning=reasoning
            )

        except Exception as e:
            logging.error(f"Error during AI verification: {e}")
            return AIVerificationResult(
                is_approved=False,
                verdict="UNCERTAIN",
                detected_brand="Unknown",
                detected_model="Unknown",
                confidence=0,
                authenticity="UNKNOWN",
                completeness="UNKNOWN",
                condition_status="UNKNOWN",
                seller_intent="SELLING",
                risk_factors=[str(e)],
                reasoning=f"Verification exception: {e}"
            )

if __name__ == '__main__':
    with open('config.json', 'r') as f:
        cfg = json.load(f)
        
    verifier = AIVerifier(cfg)
    
    test_cases = [
        {
            "name": "Case 1: Generic Knockoff OBD2 Reader",
            "title": "Universal Car OBD2 Diagnostic Scanner Code Reader",
            "desc": "Mini ELM327 Bluetooth v2.1 code scanner for Android. Clears codes on all cars.",
            "price": 20.0,
            "target": "Autel MaxiCOM MK808 Diagnostic Scanner",
            "brand": "Autel",
            "models": ["MK808", "MaxiCOM"],
            "resale": 450.0
        },
        {
            "name": "Case 2: Empty Box / Case Only Trap",
            "title": "DeWalt 20V ToughSystem Heavy Duty Storage Case ONLY",
            "desc": "Just the empty plastic blow-molded case for DeWalt 20V drill combo. NO TOOLS OR BATTERIES INCLUDED.",
            "price": 35.0,
            "target": "DeWalt 20V Max Combo Kit",
            "brand": "DeWalt",
            "models": ["DCK280C2", "DCK240C2"],
            "resale": 220.0
        },
        {
            "name": "Case 3: Defective / For Parts Trap",
            "title": "Milwaukee M18 Fuel 1/2 Impact Wrench - For Parts or Repair",
            "desc": "Tool smoked last week on jobsite and stopped spinning. Trigger clicks but motor is dead. As-is for parts.",
            "price": 40.0,
            "target": "Milwaukee M18 Fuel 1/2 Impact Wrench",
            "brand": "Milwaukee",
            "models": ["2767-20", "2863-20"],
            "resale": 240.0
        },
        {
            "name": "Case 4: Wanted / In Search Of Ad",
            "title": "WANTED: Fluke 87V or 117 Multimeter",
            "desc": "Looking to buy a Fluke 87V multimeter in good shape for electrician apprentice. Cash in hand up to $150.",
            "price": 100.0,
            "target": "Fluke 87V Industrial Multimeter",
            "brand": "Fluke",
            "models": ["87V", "87-5"],
            "resale": 380.0
        },
        {
            "name": "Case 5: Genuine Profitable Deal",
            "title": "Fluke 87-V True RMS Industrial Multimeter - Tested Working",
            "desc": "Fluke 87V digital multimeter in excellent working condition. Tested on bench, includes original Fluke TL75 silicone test leads and yellow holster.",
            "price": 140.0,
            "target": "Fluke 87V Industrial Multimeter",
            "brand": "Fluke",
            "models": ["87V", "87-5"],
            "resale": 380.0
        }
    ]
    
    print("\n================ RUNNING DEEP AI VERIFICATION AUDIT TESTS ================\n")
    for tc in test_cases:
        print(f"--- {tc['name']} ---")
        res = verifier.verify_deal(
            listing_title=tc['title'],
            listing_description=tc['desc'],
            listing_price_cad=tc['price'],
            target_name=tc['target'],
            target_brand=tc['brand'],
            target_model_numbers=tc['models'],
            ebay_resale_usd=tc['resale']
        )
        status_icon = "APPROVED" if res.is_approved else "REJECTED"
        print(f"  Status       : {status_icon}")
        print(f"  Verdict      : {res.verdict} ({res.confidence}% confidence)")
        print(f"  Brand        : {res.detected_brand} ({res.authenticity})")
        print(f"  Completeness : {res.completeness}")
        print(f"  Condition    : {res.condition_status}")
        print(f"  Intent       : {res.seller_intent}")
        if res.risk_factors:
            print(f"  Risk Factors : {', '.join(res.risk_factors)}")
        print(f"  Reasoning    : {res.reasoning}\n")
    print("=========================================================================\n")
