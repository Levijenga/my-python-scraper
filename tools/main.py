"""
Kijiji Arbitrage Radar - Orchestrator & Runner
Executes reverse-search scrape cycles, runs multi-tier matching, condition evaluation,
profit calculations, SQLite tracking, price-drop detection, and Discord alerts.
"""

import sys
import os
import time
import json
import logging
import argparse
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from db_loader import load_product_catalog
from matcher import ProductMatcher
from condition_evaluator import evaluate_condition
from profit_calc import calculate_deal_profit
from tracker_db import TrackerDB
from scraper import KijijiScraper
from notifier import ArbitrageNotifier
from ai_verifier import AIVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class ArbitrageEngine:
    def __init__(self, config_path: str = "config.json"):
        with open(config_path, 'r') as f:
            self.cfg = json.load(f)
            
        excel_path = self.cfg.get('database', {}).get('excel_path', 'combined_deduplicated_electronic_tools_scraper_dataset_v3.xlsx')
        sqlite_path = self.cfg.get('database', {}).get('sqlite_path', 'kijiji_tracker.db')
        
        logging.info(f"Loading product catalog from {excel_path}...")
        self.targets = load_product_catalog(excel_path)
        logging.info(f"Loaded {len(self.targets)} product targets.")
        
        min_fuzzy = self.cfg.get('matching', {}).get('min_fuzzy_score', 75)
        self.matcher = ProductMatcher(self.targets, min_score=min_fuzzy)
        self.tracker = TrackerDB(sqlite_path)
        self.scraper = KijijiScraper(self.cfg)
        self.notifier = ArbitrageNotifier(self.cfg)
        self.ai_verifier = AIVerifier(self.cfg)
        
    def run_scan_cycle(self) -> Dict[str, int]:
        """Runs a complete scan cycle across all configured categories."""
        categories = self.cfg.get('search', {}).get('categories', [])
        max_pages = self.cfg.get('search', {}).get('max_pages_per_category', 3)
        req_delay = self.cfg.get('search', {}).get('request_delay_seconds', 2.0)
        min_cond_score = self.cfg.get('condition_scoring', {}).get('min_alert_score', 40)
        
        stats = {
            'total_scraped': 0,
            'new_listings': 0,
            'targets_matched': 0,
            'profitable_deals': 0,
            'alerts_sent': 0,
            'price_drop_alerts': 0
        }
        
        cycle_matches = []
        seen_matched_ids = set()
        
        logging.info("Starting Winnipeg 20km scrape cycle...")
        
        for cat in categories:
            cat_name = cat.get('name', 'Category')
            cat_id = cat.get('category_id')
            cat_path = cat.get('path')
            
            logging.info(f"Scanning category: {cat_name} ({cat_path})...")
            
            for page in range(1, max_pages + 1):
                listings = self.scraper.fetch_page_listings(cat_path, cat_id, page=page)
                if not listings:
                    logging.info(f"No listings found on page {page} of {cat_name}. Moving to next category.")
                    break
                    
                known_count_on_page = 0
                
                for item in listings:
                    stats['total_scraped'] += 1
                    is_known = self.tracker.is_known_listing(item.id)
                    if is_known:
                        known_count_on_page += 1
                    else:
                        stats['new_listings'] += 1
                        
                    # 1. Evaluate Product Match
                    match = self.matcher.match_listing(item.title, item.description)
                    if not match:
                        # Log un-matched item to DB if new
                        if not is_known:
                            self.tracker.record_listing_evaluation(
                                listing_id=item.id,
                                url=item.url,
                                title=item.title,
                                price_cad=item.price_cad,
                                matched_model=None,
                                condition_score=50,
                                condition_tier="NEUTRAL",
                                expected_profit_cad=0.0,
                                roi_pct=0.0,
                                deal_score=0,
                                is_profitable=False,
                                should_alert=False
                            )
                        continue
                        
                    stats['targets_matched'] += 1
                    target = match.target
                    
                    # 2. Evaluate Condition Score
                    cond_score, cond_tier, cond_summary, cond_evidence = evaluate_condition(
                        item.title, item.description, item.attributes
                    )
                    
                    # 3. Calculate Financials & 75% ROI Hurdle
                    financials_obj = calculate_deal_profit(target.ebay_resale_usd, item.price_cad, self.cfg)
                    fin_dict = financials_obj.to_dict()
                    
                    # Condition check
                    passes_condition = (cond_score >= min_cond_score) and (cond_tier != 'HARD_REJECT')
                    is_profitable = financials_obj.is_profitable
                    
                    should_alert = passes_condition and is_profitable
                    
                    # Compute composite deal score (0-100)
                    deal_score = int(
                        0.40 * match.match_score +
                        0.30 * cond_score +
                        0.30 * min(100.0, max(0.0, fin_dict['roi_pct'] / 1.5))
                    )
                    
                    # 4. Record to SQLite Tracker & Check Alert Trigger
                    new_alert, drop_alert = self.tracker.record_listing_evaluation(
                        listing_id=item.id,
                        url=item.url,
                        title=item.title,
                        price_cad=item.price_cad,
                        matched_model=target.canonical_name,
                        condition_score=cond_score,
                        condition_tier=cond_tier,
                        expected_profit_cad=fin_dict['expected_profit_cad'],
                        roi_pct=fin_dict['roi_pct'],
                        deal_score=deal_score,
                        is_profitable=is_profitable,
                        should_alert=should_alert
                    )
                    
                    # 5. Dispatch Alert if Triggered (Protected by AI Verification)
                    ai_verdict = "NOT_EVALUATED"
                    ai_reason = ""
                    
                    if new_alert or drop_alert:
                        # Call Google Gemini AI Gatekeeper
                        logging.info(f"Candidate deal found: '{item.title}' (${item.price_cad:.2f} CAD). Running AI verification...")
                        ai_res = self.ai_verifier.verify_deal(
                            listing_title=item.title,
                            listing_description=item.description,
                            listing_price_cad=item.price_cad,
                            target_name=target.canonical_name,
                            target_brand=target.brand,
                            target_model_numbers=target.model_numbers,
                            ebay_resale_usd=target.ebay_resale_usd
                        )
                        ai_verdict = ai_res.verdict
                        ai_reason = ai_res.reasoning
                        
                        if not ai_res.is_approved:
                            decision = f"REJECT_AI_{ai_res.verdict}"
                            reason = f"AI Rejected ({ai_res.verdict}, {ai_res.confidence}%): {ai_res.reasoning}"
                            logging.info(f"🚫 AI Gatekeeper REJECTED: '{item.title}' -> {ai_res.verdict} ({ai_res.reasoning})")
                            self.tracker.log_audit(
                                listing_id=item.id,
                                title=item.title,
                                price_cad=item.price_cad,
                                matched_target=target.canonical_name,
                                match_score=match.match_score,
                                condition_score=cond_score,
                                max_buy_cad=fin_dict['max_buy_cad'],
                                decision=decision,
                                reason=reason
                            )
                        else:
                            if new_alert:
                                stats['alerts_sent'] += 1
                                decision = "ALERT_SENT"
                            else:
                                stats['price_drop_alerts'] += 1
                                decision = "PRICE_DROP_ALERT"
                                
                            logging.info(f"✅ AI Gatekeeper APPROVED: '{item.title}' ({ai_res.confidence}% confidence)")
                            self.notifier.send_deal_alert(
                                listing_title=item.title,
                                listing_url=item.url,
                                listing_price_cad=item.price_cad,
                                location_name=item.location_name or "Winnipeg",
                                distance_km=item.distance_km,
                                image_url=item.image_url,
                                matched_name=target.canonical_name,
                                match_score=match.match_score,
                                match_type=match.match_type,
                                condition_score=cond_score,
                                condition_tier=cond_tier,
                                condition_summary=cond_summary,
                                condition_evidence=cond_evidence,
                                financials=fin_dict,
                                is_price_drop=drop_alert,
                                ai_reasoning=ai_res.reasoning,
                                ai_confidence=ai_res.confidence
                            )
                            
                            self.tracker.log_audit(
                                listing_id=item.id,
                                title=item.title,
                                price_cad=item.price_cad,
                                matched_target=target.canonical_name,
                                match_score=match.match_score,
                                condition_score=cond_score,
                                max_buy_cad=fin_dict['max_buy_cad'],
                                decision=decision,
                                reason=f"AI Approved ({ai_res.confidence}%): {ai_res.reasoning}"
                            )
                    else:
                        # Log why it didn't alert
                        if not passes_condition:
                            decision = "REJECT_CONDITION"
                            reason = f"Condition score {cond_score} ({cond_tier}) < threshold"
                            ai_verdict = "SKIPPED (Condition low)"
                        elif not is_profitable:
                            decision = "REJECT_PRICE"
                            reason = f"Price ${item.price_cad:.2f} > Max Buy limit ${fin_dict['max_buy_cad']:.2f}"
                            ai_verdict = "SKIPPED (Price too high)"
                        else:
                            decision = "ALREADY_ALERTED"
                            reason = "Listing previously alerted"
                            ai_verdict = "ALREADY_ALERTED"
                            
                        self.tracker.log_audit(
                            listing_id=item.id,
                            title=item.title,
                            price_cad=item.price_cad,
                            matched_target=target.canonical_name,
                            match_score=match.match_score,
                            condition_score=cond_score,
                            max_buy_cad=fin_dict['max_buy_cad'],
                            decision=decision,
                            reason=reason
                        )
                        
                    if item.id not in seen_matched_ids:
                        seen_matched_ids.add(item.id)
                        cycle_matches.append({
                            'id': item.id,
                            'title': item.title,
                            'matched_name': target.canonical_name,
                            'price_cad': item.price_cad,
                            'max_buy_cad': fin_dict['max_buy_cad'],
                            'url': item.url,
                            'decision': decision,
                            'match_score': match.match_score,
                            'cond_score': cond_score,
                            'cond_tier': cond_tier,
                            'ai_verdict': ai_verdict,
                            'ai_reason': ai_reason
                        })
                        
                # Smart Pagination Stop: If > 80% of items on page are already known, don't keep paging deeper
                if len(listings) > 0 and (known_count_on_page / len(listings)) >= 0.80:
                    logging.info(f"Page {page} has {known_count_on_page}/{len(listings)} known listings. Stopping pagination for {cat_name}.")
                    break
                    
                time.sleep(req_delay)
                
        # Display matched items in terminal with links
        if cycle_matches:
            display_items = cycle_matches[:5]
            print("\n" + "=" * 70)
            print(f" [MATCHES] TARGET ITEMS FOUND IN THIS RUN ({len(cycle_matches)} matched item{'s' if len(cycle_matches) > 1 else ''})")
            print("=" * 70)
            for idx, m in enumerate(display_items, 1):
                print(f" {idx}. [{m['matched_name']}] {m['title']}")
                print(f"    Asking Price : ${m['price_cad']:.2f} CAD (Max Buy: ${m['max_buy_cad']:.2f} CAD) | Status: {m['decision']}")
                print(f"    Evaluation   : Match {m['match_score']}/100 | Condition {m['cond_score']}/100 [{m['cond_tier']}]")
                if m.get('ai_verdict') not in ['NOT_EVALUATED', '']:
                    print(f"    AI Check     : {m['ai_verdict']}{(' - ' + m['ai_reason']) if m.get('ai_reason') else ''}")
                print(f"    Kijiji Link  : {m['url']}")
            if len(cycle_matches) > 5:
                print(f"    ... and {len(cycle_matches) - 5} more items omitted from terminal to prevent spam.")
            print("=" * 70 + "\n")

        logging.info(
            f"Cycle finished. Scraped: {stats['total_scraped']} | "
            f"New: {stats['new_listings']} | Matched: {stats['targets_matched']} | "
            f"Alerts: {stats['alerts_sent']} | Price Drops: {stats['price_drop_alerts']}"
        )
        return stats

def main():
    parser = argparse.ArgumentParser(description="Kijiji Winnipeg Arbitrage Radar")
    parser.add_argument('--once', action='store_true', help="Run a single scan cycle and exit")
    parser.add_argument('--interval', type=int, default=300, help="Interval in seconds between scans (default: 300s / 5 min)")
    parser.add_argument('--stats', action='store_true', help="Display current tracking database statistics")
    parser.add_argument('--test-email', action='store_true', help="Send a test notification email to verify Gmail credentials")
    parser.add_argument('--test-ai', action='store_true', help="Run a test verification with the Gemini AI gatekeeper")
    args = parser.parse_args()
    
    engine = ArbitrageEngine("config.json")
    
    if args.test_ai:
        print("\n=== TESTING GOOGLE GEMINI AI GATEKEEPER ===")
        print("Testing generic knockoff test case (ELM327 OBD2 reader vs Autel MK808)...")
        res = engine.ai_verifier.verify_deal(
            listing_title="Universal Car Diagnostic OBD2 Code Reader Scanner",
            listing_description="Mini ELM327 Bluetooth scanner tool. Works with Android phone to clear check engine codes.",
            listing_price_cad=25.0,
            target_name="Autel MaxiCOM MK808 Diagnostic Scanner",
            target_brand="Autel",
            target_model_numbers=["MK808", "MaxiCOM"],
            ebay_resale_usd=450.0
        )
        print(f"  Result Verdict: {res.verdict}")
        print(f"  Approved: {res.is_approved}")
        print(f"  Confidence: {res.confidence}%")
        print(f"  Detected Brand: {res.detected_brand}")
        print(f"  AI Reasoning: {res.reasoning}")
        print("===========================================\n")
        return

    if args.test_email:
        print("\n=== TESTING GMAIL EMAIL NOTIFICATION ===")
        engine.notifier.send_test_email()
        print("========================================\n")
        return

    if args.stats:
        conn = engine.tracker._get_connection()
        try:
            total_seen = conn.execute("SELECT COUNT(*) FROM seen_listings").fetchone()[0]
            total_matches = conn.execute("SELECT COUNT(*) FROM seen_listings WHERE matched_model IS NOT NULL").fetchone()[0]
            total_alerts = conn.execute("SELECT COUNT(*) FROM seen_listings WHERE alert_sent > 0").fetchone()[0]
            print("\n=== KIJIJI TRACKER DATABASE STATS ===")
            print(f"  Total Seen Listings : {total_seen}")
            print(f"  Target Items Matched: {total_matches}")
            print(f"  Total Alerts Sent   : {total_alerts}")
            print("=====================================\n")
        finally:
            conn.close()
        return

    if args.once:
        engine.run_scan_cycle()
    else:
        logging.info(f"Starting continuous background scanner (Interval: {args.interval} seconds)... Press Ctrl+C to stop.")
        try:
            while True:
                engine.run_scan_cycle()
                logging.info(f"Sleeping {args.interval}s until next scan cycle...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logging.info("Scanner stopped by user.")

if __name__ == '__main__':
    main()
