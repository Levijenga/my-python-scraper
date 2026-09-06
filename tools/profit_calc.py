"""
Profitability & Financial Calculation Engine
Calculates conservative eBay Net proceeds, applies fees, shipping reserve, 
and enforces the >= 75% ROI hurdle rate against Kijiji listing prices.
"""

import os
import json
import time
import requests
from typing import Dict, Any, Optional

RATE_CACHE_FILE = "usd_cad_rate_cache.json"

def get_usd_to_cad_rate(fallback: float = 1.36, cache_hours: int = 24) -> float:
    """Fetches and caches live USD to CAD exchange rate."""
    current_time = time.time()
    
    if os.path.exists(RATE_CACHE_FILE):
        try:
            with open(RATE_CACHE_FILE, 'r') as f:
                data = json.load(f)
                if current_time - data.get('timestamp', 0) < cache_hours * 3600:
                    return float(data.get('rate', fallback))
        except Exception:
            pass
            
    # Attempt to fetch fresh rate from a fast public API
    try:
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        if resp.status_code == 200:
            rate_data = resp.json()
            cad_rate = float(rate_data.get('rates', {}).get('CAD', fallback))
            with open(RATE_CACHE_FILE, 'w') as f:
                json.dump({'rate': cad_rate, 'timestamp': current_time}, f)
            return cad_rate
    except Exception:
        pass
        
    return fallback

class DealFinancials:
    def __init__(
        self,
        ebay_resale_usd: float,
        kijiji_price_cad: float,
        usd_cad_rate: float,
        ebay_fee_pct: float = 13.25,
        conservative_discount_pct: float = 10.0,
        shipping_reserve_usd: float = 15.0,
        min_roi_pct: float = 75.0
    ):
        self.ebay_resale_usd: float = ebay_resale_usd
        self.kijiji_price_cad: float = kijiji_price_cad
        self.usd_cad_rate: float = usd_cad_rate
        
        # 1. Conservative Gross (10% haircut for negotiation/market fluctuation)
        self.conservative_gross_usd: float = ebay_resale_usd * (1.0 - (conservative_discount_pct / 100.0))
        
        # 2. Net USD after eBay fees (13.25%) and shipping reserve ($15 USD)
        fee_amount = self.conservative_gross_usd * (ebay_fee_pct / 100.0)
        self.net_proceeds_usd: float = max(0.0, self.conservative_gross_usd - fee_amount - shipping_reserve_usd)
        
        # 3. Convert Net USD to Net CAD
        self.net_proceeds_cad: float = self.net_proceeds_usd * usd_cad_rate
        
        # 4. Max Buy CAD to guarantee >= 75% profit on cost
        # Net CAD >= (1 + 0.75) * Max Buy => Max Buy <= Net CAD / 1.75
        self.max_buy_cad: float = self.net_proceeds_cad / (1.0 + (min_roi_pct / 100.0))
        
        # 5. Expected Profit and ROI
        self.expected_profit_cad: float = self.net_proceeds_cad - self.kijiji_price_cad
        if self.kijiji_price_cad > 0:
            self.roi_pct: float = (self.expected_profit_cad / self.kijiji_price_cad) * 100.0
        else:
            self.roi_pct: float = 999.0 if self.net_proceeds_cad > 0 else 0.0
            
        # Is it a profitable deal?
        self.is_profitable: bool = (self.kijiji_price_cad <= self.max_buy_cad) and (self.kijiji_price_cad > 0 or self.kijiji_price_cad == 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ebay_resale_usd': round(self.ebay_resale_usd, 2),
            'conservative_gross_usd': round(self.conservative_gross_usd, 2),
            'net_proceeds_usd': round(self.net_proceeds_usd, 2),
            'net_proceeds_cad': round(self.net_proceeds_cad, 2),
            'kijiji_price_cad': round(self.kijiji_price_cad, 2),
            'max_buy_cad': round(self.max_buy_cad, 2),
            'expected_profit_cad': round(self.expected_profit_cad, 2),
            'roi_pct': round(self.roi_pct, 1),
            'usd_cad_rate': round(self.usd_cad_rate, 4),
            'is_profitable': self.is_profitable
        }

def calculate_deal_profit(
    ebay_resale_usd: float,
    kijiji_price_cad: float,
    cfg: Dict[str, Any]
) -> DealFinancials:
    """Calculates deal financials using configuration parameters."""
    fin_cfg = cfg.get('financials', {})
    usd_cad_rate = get_usd_to_cad_rate(
        fallback=fin_cfg.get('fallback_usd_cad_rate', 1.36),
        cache_hours=fin_cfg.get('exchange_rate_cache_hours', 24)
    )
    
    return DealFinancials(
        ebay_resale_usd=ebay_resale_usd,
        kijiji_price_cad=kijiji_price_cad,
        usd_cad_rate=usd_cad_rate,
        ebay_fee_pct=fin_cfg.get('ebay_fee_percent', 13.25),
        conservative_discount_pct=fin_cfg.get('conservative_discount_percent', 10.0),
        shipping_reserve_usd=fin_cfg.get('shipping_reserve_usd', 15.0),
        min_roi_pct=fin_cfg.get('min_roi_percent', 75.0)
    )

if __name__ == '__main__':
    with open('config.json', 'r') as f:
        cfg = json.load(f)
        
    print("=== PROFIT ENGINE TEST ===")
    sample_items = [
        ("Fluke 87V", 350.0, 120.0),
        ("Keithley 2400", 475.0, 150.0),
        ("Testo 330i", 475.0, 300.0),
    ]
    
    for name, resale_usd, k_cad in sample_items:
        deal = calculate_deal_profit(resale_usd, k_cad, cfg)
        d = deal.to_dict()
        print(f"\nItem: {name}")
        print(f"  eBay Resale: ${d['ebay_resale_usd']} USD | Net: ${d['net_proceeds_cad']} CAD")
        print(f"  Kijiji Price: ${d['kijiji_price_cad']} CAD | Max Buy Limit: ${d['max_buy_cad']} CAD")
        print(f"  Expected Profit: ${d['expected_profit_cad']} CAD | ROI: {d['roi_pct']}%")
        print(f"  Profitable Deal? -> {d['is_profitable']}")
