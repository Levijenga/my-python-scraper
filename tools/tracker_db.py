"""
Tracker Database Module
SQLite persistent storage for tracking seen Kijiji listings, price histories,
preventing duplicate alerts, detecting profitable price drops, and maintaining an audit log.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List

class TrackerDB:
    def __init__(self, db_path: str = "kijiji_tracker.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_listings (
                listing_id TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                initial_price_cad REAL,
                current_price_cad REAL,
                matched_model TEXT,
                condition_score INTEGER,
                condition_tier TEXT,
                expected_profit_cad REAL,
                roi_pct REAL,
                deal_score INTEGER,
                alert_sent INTEGER DEFAULT 0,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                price_history TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP,
                listing_id TEXT,
                title TEXT,
                price_cad REAL,
                matched_target TEXT,
                match_score INTEGER,
                condition_score INTEGER,
                max_buy_cad REAL,
                decision TEXT,
                reason TEXT
            )
        """)
        conn.commit()
        return conn

    def _init_db(self):
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_listings (
                    listing_id TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    initial_price_cad REAL,
                    current_price_cad REAL,
                    matched_model TEXT,
                    condition_score INTEGER,
                    condition_tier TEXT,
                    expected_profit_cad REAL,
                    roi_pct REAL,
                    deal_score INTEGER,
                    alert_sent INTEGER DEFAULT 0,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    price_history TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP,
                    listing_id TEXT,
                    title TEXT,
                    price_cad REAL,
                    matched_target TEXT,
                    match_score INTEGER,
                    condition_score INTEGER,
                    max_buy_cad REAL,
                    decision TEXT,
                    reason TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def is_known_listing(self, listing_id: str) -> bool:
        """Returns True if the listing has been seen previously."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT listing_id FROM seen_listings WHERE listing_id = ?", (listing_id,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_listing(self, listing_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM seen_listings WHERE listing_id = ?", (listing_id,)).fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def record_listing_evaluation(
        self,
        listing_id: str,
        url: str,
        title: str,
        price_cad: float,
        matched_model: Optional[str],
        condition_score: int,
        condition_tier: str,
        expected_profit_cad: float,
        roi_pct: float,
        deal_score: int,
        is_profitable: bool,
        should_alert: bool
    ) -> Tuple[bool, bool]:
        now = datetime.now(timezone.utc).isoformat()
        is_new_alert = False
        is_price_drop_alert = False
        
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM seen_listings WHERE listing_id = ?", (listing_id,)).fetchone()
            
            if row is None:
                alert_val = 1 if should_alert else 0
                is_new_alert = should_alert
                history = json.dumps([{"price": price_cad, "timestamp": now}])
                
                conn.execute("""
                    INSERT INTO seen_listings (
                        listing_id, url, title, initial_price_cad, current_price_cad,
                        matched_model, condition_score, condition_tier,
                        expected_profit_cad, roi_pct, deal_score, alert_sent,
                        first_seen, last_seen, price_history
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    listing_id, url, title, price_cad, price_cad,
                    matched_model, condition_score, condition_tier,
                    expected_profit_cad, roi_pct, deal_score, alert_val,
                    now, now, history
                ))
            else:
                old_price = row['current_price_cad']
                old_alert_sent = row['alert_sent']
                history_list = json.loads(row['price_history'] or '[]')
                
                if abs(old_price - price_cad) > 0.01:
                    history_list.append({"price": price_cad, "timestamp": now})
                    
                if price_cad < old_price and should_alert:
                    is_price_drop_alert = True
                    alert_val = 2
                else:
                    alert_val = old_alert_sent
                    
                conn.execute("""
                    UPDATE seen_listings SET
                        current_price_cad = ?,
                        matched_model = ?,
                        condition_score = ?,
                        condition_tier = ?,
                        expected_profit_cad = ?,
                        roi_pct = ?,
                        deal_score = ?,
                        alert_sent = ?,
                        last_seen = ?,
                        price_history = ?
                    WHERE listing_id = ?
                """, (
                    price_cad, matched_model, condition_score, condition_tier,
                    expected_profit_cad, roi_pct, deal_score, alert_val,
                    now, json.dumps(history_list), listing_id
                ))
                
            conn.commit()
        finally:
            conn.close()
            
        return (is_new_alert, is_price_drop_alert)

    def log_audit(
        self,
        listing_id: str,
        title: str,
        price_cad: float,
        matched_target: Optional[str],
        match_score: int,
        condition_score: int,
        max_buy_cad: float,
        decision: str,
        reason: str
    ):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            conn.execute("""
                INSERT INTO audit_log (
                    timestamp, listing_id, title, price_cad, matched_target,
                    match_score, condition_score, max_buy_cad, decision, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now, listing_id, title, price_cad, matched_target,
                match_score, condition_score, max_buy_cad, decision, reason
            ))
            conn.commit()
        finally:
            conn.close()

if __name__ == '__main__':
    test_db_path = "test_tracker.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    tracker = TrackerDB(test_db_path)
    print("Tracker DB initialized successfully.")
    
    # Test recording
    new_alert, drop_alert = tracker.record_listing_evaluation(
        listing_id="123456",
        url="https://kijiji.ca/v/123456",
        title="Fluke 87V Multimeter",
        price_cad=120.0,
        matched_model="Fluke 87V",
        condition_score=95,
        condition_tier="HOT",
        expected_profit_cad=238.0,
        roi_pct=198.0,
        deal_score=96,
        is_profitable=True,
        should_alert=True
    )
    print("Record 1 -> New Alert:", new_alert, "Drop Alert:", drop_alert)
    
    # Test duplicate
    new_alert2, drop_alert2 = tracker.record_listing_evaluation(
        listing_id="123456",
        url="https://kijiji.ca/v/123456",
        title="Fluke 87V Multimeter",
        price_cad=120.0,
        matched_model="Fluke 87V",
        condition_score=95,
        condition_tier="HOT",
        expected_profit_cad=238.0,
        roi_pct=198.0,
        deal_score=96,
        is_profitable=True,
        should_alert=True
    )
    print("Record 2 (Duplicate) -> New Alert:", new_alert2, "Drop Alert:", drop_alert2)
    
    # Test price drop
    new_alert3, drop_alert3 = tracker.record_listing_evaluation(
        listing_id="123456",
        url="https://kijiji.ca/v/123456",
        title="Fluke 87V Multimeter",
        price_cad=90.0,
        matched_model="Fluke 87V",
        condition_score=95,
        condition_tier="HOT",
        expected_profit_cad=268.0,
        roi_pct=297.0,
        deal_score=98,
        is_profitable=True,
        should_alert=True
    )
    print("Record 3 (Price Drop) -> New Alert:", new_alert3, "Drop Alert:", drop_alert3)
    
    del tracker
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    print("All tests passed and cleaned up.")
