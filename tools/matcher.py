"""
Multi-Layer Product Matching Engine
Matches raw Kijiji listings against the product database using:
1. Exact Model Number Regex
2. Brand + Alias / Variant Lookup
3. Normalized Token-Set Matching
Includes exclusion term filtering to eliminate false positives.
"""

import re
import difflib
from typing import List, Tuple, Optional, Dict, Any
from db_loader import ProductTarget

def normalize_text(text: str) -> str:
    """Normalizes string for comparison: lowercases, collapses whitespace and symbols."""
    if not text:
        return ""
    text = text.lower()
    # Replace separators with single space
    text = re.sub(r'[-_/,\.\:\;]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_alphanumeric_tokens(text: str) -> List[str]:
    return [t for t in re.findall(r'\b[a-z0-9]+\b', text.lower()) if len(t) >= 2]

class MatchResult:
    def __init__(
        self,
        target: ProductTarget,
        match_score: int,
        match_type: str,
        match_reason: str
    ):
        self.target = target
        self.match_score = match_score  # 0 to 100
        self.match_type = match_type    # EXACT_MODEL, VARIANT_MATCH, FUZZY_TOKEN
        self.match_reason = match_reason

    def __repr__(self):
        return f"<Match: {self.target.canonical_name} | Score: {self.match_score} ({self.match_type})>"

class ProductMatcher:
    def __init__(self, targets: List[ProductTarget], min_score: int = 75):
        self.targets = targets
        self.min_score = min_score

    def match_listing(self, title: str, description: str = "") -> Optional[MatchResult]:
        """
        Evaluates title and description against all product targets.
        Returns highest scoring MatchResult if >= min_score, else None.
        """
        if not title:
            return None
            
        full_text = f"{title} {description}".lower()
        norm_title = normalize_text(title)
        norm_title_no_spaces = re.sub(r'\s+', '', norm_title)
        title_tokens = set(extract_alphanumeric_tokens(title))
        
        best_match: Optional[MatchResult] = None
        
        for target in self.targets:
            # 1. Check target-specific generic exclusions
            excluded = False
            for exc in target.exclude_terms:
                if exc and re.search(r'\b' + re.escape(exc) + r'\b', full_text):
                    excluded = True
                    break
            if excluded:
                continue
                
            brand_in_title = bool(target.brand and re.search(r'\b' + re.escape(target.brand.lower()) + r'\b', norm_title))
            
            # 2. Layer 1: Exact Model Number Match
            for model in target.model_numbers:
                norm_model = normalize_text(model)
                norm_model_no_space = re.sub(r'\s+', '', norm_model)
                is_purely_numeric = norm_model_no_space.isdigit()
                has_any_digit = any(c.isdigit() for c in norm_model_no_space)
                
                # Check exact regex with flexible hyphen/space handling
                # E.g. "TR-8S" matches "TR-8S", "TR 8S", "TR8S"
                escaped_model = re.escape(model.lower()).replace(r'\-', r'[- ]?').replace(r'\ ', r'[- ]?')
                model_regex = r'\b' + escaped_model + r'\b'
                matched_model = False
                if re.search(model_regex, norm_title) or re.search(model_regex, title.lower()):
                    matched_model = True
                else:
                    # Token-level unspaced match
                    title_clean_tokens = [re.sub(r'[^a-z0-9]', '', t) for t in norm_title.split()]
                    if len(norm_model_no_space) >= 4 and norm_model_no_space in title_clean_tokens:
                        matched_model = True
                    
                if matched_model:
                    # Case A: Brand is present in title -> High confidence (Score: 100)
                    if brand_in_title:
                        score = 100
                        reason = f"Model '{model}' with brand '{target.brand}' matched in title"
                        if not best_match or score > best_match.match_score:
                            best_match = MatchResult(target, score, "EXACT_MODEL", reason)
                    # Case B: Brand is NOT in title -> Model must be high-entropy (length >= 4, has both digits and letters)
                    else:
                        has_letters = any(c.isalpha() for c in norm_model_no_space)
                        if has_any_digit and has_letters and len(norm_model_no_space) >= 4:
                            score = 90
                            reason = f"Distinctive alphanumeric model '{model}' matched (unbranded)"
                            if not best_match or score > best_match.match_score:
                                best_match = MatchResult(target, score, "EXACT_MODEL", reason)
                            
            # 3. Layer 2: Variant / Alias Matching
            for variant in target.variants:
                norm_var = normalize_text(variant)
                if len(norm_var) >= 4 and (norm_var in norm_title or re.search(r'\b' + re.escape(norm_var) + r'\b', norm_title)):
                    score = 95
                    reason = f"Variant alias '{variant}' matched in title"
                    if not best_match or score > best_match.match_score:
                        best_match = MatchResult(target, score, "VARIANT_MATCH", reason)
                        
            # 4. Layer 3: Token Set Fuzzy Matching (Strict: requires brand and at least 3 tokens)
            target_tokens = set(extract_alphanumeric_tokens(target.canonical_name)) - {target.brand.lower() if target.brand else ''}
            common_tokens = title_tokens.intersection(target_tokens)
            if brand_in_title and len(target_tokens) >= 2 and len(common_tokens) >= 2:
                overlap_ratio = (2.0 * len(common_tokens)) / (len(title_tokens) + len(target_tokens))
                fuzzy_score = int(overlap_ratio * 100)
                if fuzzy_score >= self.min_score:
                    score = min(85, fuzzy_score)
                    reason = f"Brand + high token overlap: {common_tokens}"
                    if not best_match or score > best_match.match_score:
                        best_match = MatchResult(target, score, "FUZZY_TOKEN", reason)
                        
        return best_match

if __name__ == '__main__':
    from db_loader import load_product_catalog
    
    targets = load_product_catalog('combined_deduplicated_electronic_tools_scraper_dataset_v3.xlsx')
    matcher = ProductMatcher(targets)
    
    print("=== MULTI-LAYER MATCHER TEST CASES ===")
    test_listings = [
        ("Fluke 87V Digital Multimeter in Box", "Works great"),
        ("Tektronix TDS 2012B Oscilloscope 100MHz", "Tested working"),
        ("Keithley 2400 SourceMeter with Leads", "Good condition"),
        ("Fluke Multimeter Case Only No Tool", "Case only for 87V"),
        ("Agilent 34970A Data Acquisition Unit", "Powers on fine"),
        ("Random Milwaukee M18 Drill", "Works good")
    ]
    
    for title, desc in test_listings:
        match = matcher.match_listing(title, desc)
        print(f"\nListing: '{title}'")
        if match:
            print(f"  Matched -> {match.target.canonical_name}")
            print(f"  Score: {match.match_score}/100 | Type: {match.match_type} | Reason: {match.match_reason}")
        else:
            print("  No match (filtered or non-target).")
