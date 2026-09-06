"""
Database Loader Module
Loads product targets from Excel, normalizes model codes and variants,
and builds optimized structures for fast multi-tier matching.
"""

import os
import re
import glob
import logging
import pandas as pd
from typing import List, Dict, Any, Optional

def clean_str(val: Any) -> str:
    if pd.isna(val) or val is None:
        return ""
    return str(val).strip()

GENERIC_STOPWORDS = {
    'multimeter', 'tester', 'meter', 'scope', 'analyzer', 'camera', 'calibrator',
    'digital', 'intrinsically', 'safe', 'processmeter', 'industrial', 'electrical',
    'installation', 'power', 'logger', 'quality', 'energy', 'thermal', 'imager',
    'infrared', 'fluke', 'agilent', 'keysight', 'tektronix', 'keithley', 'testo',
    'milwaukee', 'dewalt', 'bosch', 'makita', 'snap-on', 'snapon', 'tools',
    'oscilloscope', 'sourcemeter', 'acquisition', 'combo', 'kit', 'fuel', 'digit',
    'system', 'series', 'standard', 'pro', 'plus', 'true', 'rms', 'clamp', 'ground',
    'resistance', 'insulation', 'high', 'voltage', 'current', 'cable', 'locator',
    'network', 'networks', 'fiber', 'microscanner', 'optifiber', 'certifiber',
    '100mhz', '200mhz', '50mhz', '60mhz', '1ghz', '500mhz', '300mhz', '25mhz',
    '12v', '18v', '20v', '60v', '120v', '240v', '600v', '1000v', '2amp', '10amp', '20amp',
    '128gb', '256gb', '512gb', '64gb', '32gb', '16gb', '8gb', '1tb', '2tb', '4tb',
    '30g', '100g', '500g', '2-prong', '3-prong', '4-prong',
    'm18', 'm12', '20v', '18v', '12v', '20vmax', '18vmax', '40v', '60vmax'
}

# Explicit models for items in the dataset that do not have digits in their model names
KNOWN_DIGITLESS_MODELS = {
    'milwaukee m18 fuel combo kit': ['m18 fuel combo', 'm18 combo kit', 'fuel combo kit'],
    'dewalt 20v max combo kit': ['20v max combo', '20v combo kit', 'dewalt combo kit'],
    'universal audio apollo twin': ['apollo twin'],
    'strymon timeline delay pedal': ['strymon timeline', 'timeline delay'],
    'dji ronin-s gimbal': ['ronin-s', 'ronin s'],
    'atomos ninja v monitor recorder': ['ninja v'],
    'dji avata fpv drone': ['dji avata', 'avata drone'],
    'nikon forestry pro ii rangefinder': ['forestry pro ii', 'forestry pro 2'],
    'ubiquiti unifi dream machine pro': ['dream machine pro', 'udm pro', 'udm-pro'],
    'valve index vr headset': ['valve index', 'index vr'],
    'sega saturn console': ['sega saturn'],
    'analogue pocket handheld': ['analogue pocket'],
    'nintendo switch oled': ['switch oled']
}

def extract_model_candidates(text: str, brand: str) -> List[str]:
    """Extract strict alphanumeric model identifiers containing digits, or explicit digitless model phrases."""
    if not text:
        return []
        
    lower_text = text.lower().strip()
    
    # 1. Check known digitless models
    for key, phrases in KNOWN_DIGITLESS_MODELS.items():
        if key in lower_text or (brand and brand.lower() in lower_text and any(p in lower_text for p in phrases)):
            return phrases
            
    # 2. Extract ONLY tokens containing at least one digit (e.g. 879B, SM-4TZ, 87V, TDS2024B, M18, FR-301)
    tokens = re.findall(r'\b[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*\b', text)
    candidates = []
    
    for token in tokens:
        token_clean = token.strip()
        lower_token = token_clean.lower()
        
        # Skip if in generic stopwords or matches brand name
        if lower_token in GENERIC_STOPWORDS or (brand and lower_token == brand.lower()):
            continue
            
        # Must contain at least one digit, at least one letter, and length >= 3
        # e.g. TR-8S, SM-4TZ, CA6116, 87V, 34970A, TDS2024B, FR-301
        has_digit = any(c.isdigit() for c in token_clean)
        has_letter = any(c.isalpha() for c in token_clean)
        
        if has_digit and len(token_clean) >= 3:
            # Skip if it's just a generic single digit + letter suffix like 8S, 3G
            if len(token_clean) == 2 and token_clean.endswith(('s', 'g', 'v', 'a', 'w', 'p')):
                continue
            candidates.append(token_clean)
            # Also append unhyphenated variant (e.g. TR-8S -> TR8S, FR-301 -> FR301)
            unhyphenated = token_clean.replace('-', '')
            if unhyphenated != token_clean and len(unhyphenated) >= 3:
                candidates.append(unhyphenated)
        elif has_digit and is_purely_numeric(token_clean) and len(token_clean) >= 3:
            # Purely numeric model codes like 2400, 1735, 789
            candidates.append(token_clean)
            
    return list(dict.fromkeys(candidates))

def is_purely_numeric(text: str) -> bool:
    return text.replace('-', '').isdigit()

class ProductTarget:
    def __init__(self, row: pd.Series):
        self.master_id: int = int(row.get('Master ID', 0)) if pd.notna(row.get('Master ID')) else 0
        self.canonical_name: str = clean_str(row.get('Canonical Tool / Model'))
        self.brand: str = clean_str(row.get('Brand'))
        self.category: str = clean_str(row.get('Category'))
        
        resale_val = row.get('Approx Used eBay Resale (USD)')
        self.ebay_resale_usd: float = float(resale_val) if pd.notna(resale_val) else 0.0
        
        low_val = row.get('Used eBay Low (USD)')
        self.ebay_low_usd: Optional[float] = float(low_val) if pd.notna(low_val) else None
        
        high_val = row.get('Used eBay High (USD)')
        self.ebay_high_usd: Optional[float] = float(high_val) if pd.notna(high_val) else None
        
        # Collect all variants
        variants = []
        for i in range(1, 6):
            v = clean_str(row.get(f'Variant {i}'))
            if v:
                variants.append(v)
        self.variants: List[str] = list(dict.fromkeys(variants))
        
        # Collect model tokens and variations
        model_candidates = extract_model_candidates(self.canonical_name, self.brand)
        for v in self.variants:
            model_candidates.extend(extract_model_candidates(v, self.brand))
        self.model_numbers: List[str] = list(dict.fromkeys(model_candidates))
        
        # Generic Exclude Terms (split by semicolon or comma)
        exclude_raw = clean_str(row.get('Generic Exclude Terms'))
        if exclude_raw:
            self.exclude_terms: List[str] = [
                term.strip().lower() for term in re.split(r'[;,]', exclude_raw) if term.strip()
            ]
        else:
            self.exclude_terms: List[str] = []
            
        self.ebay_url: str = clean_str(row.get('eBay Sold/Search URL'))
        self.notes: str = clean_str(row.get('Scraper Note'))

    def __repr__(self):
        return f"<Target {self.master_id}: {self.canonical_name} (${self.ebay_resale_usd} USD)>"

def load_product_catalog(excel_path: str) -> List[ProductTarget]:
    """Reads Excel file and returns list of validated ProductTarget objects.
    If the exact path doesn't exist, auto-detects the newest .xlsx file in the same folder.
    """
    import glob
    if not os.path.exists(excel_path):
        folder = os.path.dirname(os.path.abspath(excel_path)) or '.'
        candidates = sorted(glob.glob(os.path.join(folder, '*.xlsx')), key=os.path.getmtime, reverse=True)
        if candidates:
            detected = candidates[0]
            logging.warning(f"Excel file not found at '{excel_path}'. Auto-detected: '{os.path.basename(detected)}'")
            excel_path = detected
        else:
            raise FileNotFoundError(f"No .xlsx file found in '{folder}'. Please add your product dataset.")

    df = pd.read_excel(excel_path)
    targets = []
    for _, row in df.iterrows():
        name = clean_str(row.get('Canonical Tool / Model'))
        if not name:
            continue
        target = ProductTarget(row)
        if target.ebay_resale_usd > 0:
            targets.append(target)
    return targets

if __name__ == '__main__':
    targets = load_product_catalog('combined_deduplicated_electronic_tools_scraper_dataset_v3.xlsx')
    print(f"Loaded {len(targets)} valid targets from Excel.")
    for t in targets[:5]:
        print(t, "Models:", t.model_numbers, "Variants:", t.variants[:2], "Excludes:", t.exclude_terms[:3])
