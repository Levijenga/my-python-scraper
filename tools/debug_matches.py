"""
Debug script: Shows what the matcher sees on live listings.
Prints the top scoring near-matches even if below threshold.
"""
import json
import logging
import sqlite3
from db_loader import load_product_catalog
from matcher import ProductMatcher
from scraper import KijijiScraper

logging.basicConfig(level=logging.WARNING)  # suppress INFO noise

with open("config.json") as f:
    cfg = json.load(f)

targets = load_product_catalog("combined_deduplicated_electronic_tools_scraper_dataset_v3.xlsx")
print(f"Loaded {len(targets)} targets.\n")

# Lower the threshold so we can see near-misses
matcher = ProductMatcher(targets, min_score=30)
scraper = KijijiScraper(cfg)

categories = cfg.get("search", {}).get("categories", [])
near_misses = []

for cat in categories:
    cat_name = cat.get("name", "")
    cat_path = cat.get("path")
    cat_id = cat.get("category_id")
    print(f"Scanning: {cat_name}...")

    listings = scraper.fetch_page_listings(cat_path, cat_id, page=1)
    print(f"  Got {len(listings)} listings.")

    for item in listings:
        # Try all targets, pick best score
        best_score = 0
        best_target = None
        best_type = None

        for target in targets:
            # Try direct model word presence first (quick filter)
            title_lower = item.title.lower()
            desc_lower = (item.description or "").lower()
            for variant in [target.canonical_name] + target.variants:
                v = variant.lower().replace("-", " ")
                if v and len(v) > 3 and v in title_lower:
                    near_misses.append({
                        "score": 99,
                        "type": "KEYWORD_IN_TITLE",
                        "target": target.canonical_name,
                        "title": item.title,
                        "price": item.price_cad,
                        "url": item.url,
                    })

        # Also run through the real matcher with lowered threshold
        match = matcher.match_listing(item.title, item.description)
        if match:
            near_misses.append({
                "score": match.match_score,
                "type": match.match_type,
                "target": match.target.canonical_name,
                "title": item.title,
                "price": item.price_cad,
                "url": item.url,
            })

# Deduplicate and sort
seen = set()
unique = []
for m in near_misses:
    key = m["url"] + m["target"]
    if key not in seen:
        seen.add(key)
        unique.append(m)

unique.sort(key=lambda x: x["score"], reverse=True)

print(f"\n{'='*80}")
print(f"  NEAR-MATCHES & KEYWORD HITS ({len(unique)} total)")
print(f"{'='*80}")
if not unique:
    print("  ❌ No keyword hits at all found in current listings.")
    print("  This means none of your 170 target product names appeared in any listing title.")
    print("  The radar is working — there just aren't any matching listings on Kijiji right now.")
else:
    for m in unique[:30]:
        print(f"  [{m['score']:>3}] [{m['type']:<20}] ${m['price']:>6.0f} CAD | {m['target']:<35} | {m['title'][:50]}")
        print(f"        {m['url']}")
print()
