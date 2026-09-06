"""
Kijiji Winnipeg Listings Scraper
Fetches newest listings within Winnipeg 20km radius from Kijiji GraphQL Apollo Next.js state.
Handles pagination, extraction of prices, locations, descriptions, images, and attributes.
"""

import time
import json
import logging
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class KijijiListing:
    def __init__(self, data: Dict[str, Any]):
        self.id: str = str(data.get('id', ''))
        self.title: str = data.get('title', '').strip()
        self.description: str = data.get('description', '').strip()
        self.url: str = data.get('url', '')
        
        # Price extraction (amount is in cents in Apollo state)
        price_obj = data.get('price', {}) or {}
        price_type = price_obj.get('type', '')
        amount_cents = price_obj.get('amount')
        
        if price_type == 'FREE' or amount_cents == 0:
            self.price_cad: float = 0.0
        elif amount_cents is not None:
            self.price_cad: float = float(amount_cents) / 100.0
        else:
            self.price_cad: float = -1.0 # Contact / Missing
            
        # Images
        image_urls = data.get('imageUrls', [])
        self.image_url: Optional[str] = image_urls[0] if image_urls else None
        
        # Location & Distance
        loc_obj = data.get('location', {}) or {}
        self.location_name: str = loc_obj.get('name', '')
        self.address: str = loc_obj.get('address', '')
        self.distance_km: float = round(float(loc_obj.get('distance', 0)) / 1000.0, 1)
        
        # Dates
        self.sorting_date: str = data.get('sortingDate', '')
        self.activation_date: str = data.get('activationDate', '')
        
        # Attributes & Flags
        self.attributes: List[Dict[str, Any]] = data.get('attributes', {}).get('all', [])
        self.flags: Dict[str, Any] = data.get('flags', {})
        self.is_price_drop_badge: bool = bool(self.flags.get('priceDrop', False))
        
        # Seller
        poster = data.get('posterInfo', {}) or {}
        self.seller_rating: Optional[float] = poster.get('rating')
        self.seller_verified: bool = bool(poster.get('verified', False))

    def __repr__(self):
        return f"<KijijiListing {self.id}: '{self.title}' (${self.price_cad} CAD, {self.distance_km}km)>"

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0'
]

class KijijiScraper:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.ua_index = 0
        self._init_session()
        
    def _init_session(self):
        """Initializes requests session with retry strategy and realistic headers."""
        import random
        from urllib3.util import Retry
        from requests.adapters import HTTPAdapter
        
        self.session = requests.Session()
        
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        ua = USER_AGENTS[self.ua_index % len(USER_AGENTS)]
        self.ua_index += 1
        
        self.session.headers.update({
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1'
        })
        
    def build_url(self, category_path: str, category_id: str, page: int = 1) -> str:
        search_cfg = self.cfg.get('search', {})
        city = search_cfg.get('city', 'winnipeg')
        loc_id = search_cfg.get('location_id', '1700192')
        radius = search_cfg.get('radius_km', 20.0)
        lat = search_cfg.get('latitude', 49.895136)
        lng = search_cfg.get('longitude', -97.138374)
        
        page_segment = f"/page-{page}" if page > 1 else ""
        url = (
            f"https://www.kijiji.ca/{category_path}/{city}{page_segment}/c{category_id}l{loc_id}"
            f"?radius={radius}&address=Winnipeg%2C+MB&ll={lat}%2C{lng}"
        )
        return url

    def fetch_via_playwright(self, url: str) -> List[KijijiListing]:
        """Fallback fetcher using Playwright headless Chromium to bypass 429 rate limits."""
        try:
            from playwright.sync_api import sync_playwright
            logging.info(f"Using Playwright headless browser fallback for: {url}")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                
                html = page.content()
                listings = self._parse_html(html)
                if listings:
                    browser.close()
                    logging.info(f"Playwright successfully parsed {len(listings)} listings from Apollo state on {url}")
                    return listings
                    
                # JS DOM extraction fallback (similar to car scraper)
                raw_items = page.evaluate("""() => {
                    const results = [];
                    const anchors = Array.from(document.querySelectorAll('a[href*="/v-"]'));
                    for (const a of anchors) {
                        const href = a.href;
                        const title = a.textContent.trim();
                        if (title && title.length > 5 && !href.includes('/page-') && !results.some(r => r.link === href)) {
                            let parent = a.closest('li, div, article, section');
                            let priceStr = "$0";
                            if (parent) {
                                const match = parent.innerText.match(/\\$\\s*([\\d,]+(?:\\.\\d{2})?)/);
                                if (match) priceStr = match[0];
                            }
                            results.push({title: title, link: href, price: priceStr});
                        }
                    }
                    return results;
                }""")
                
                browser.close()
                
                for item in raw_items:
                    href = item['link']
                    title = item['title']
                    price_str = item['price']
                    price_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', price_str)
                    price_cad = float(price_match.group(1).replace(',', '')) if price_match else 0.0
                    
                    listing_id_match = re.search(r'/(\d+)(?:\?|$)', href)
                    listing_id_str = listing_id_match.group(1) if listing_id_match else str(abs(hash(href)))
                    
                    dummy_data = {
                        'id': listing_id_str,
                        'title': title,
                        'description': title,
                        'url': href,
                        'price': {'amount': int(price_cad * 100)},
                        'location': {'name': "Winnipeg"}
                    }
                    listings.append(KijijiListing(dummy_data))
                    
                if listings:
                    logging.info(f"Playwright JS extraction successfully gathered {len(listings)} listings from {url}")
                return listings
        except Exception as e:
            logging.error(f"Playwright fallback error on {url}: {e}")
            return []

    def fetch_page_listings(self, category_path: str, category_id: str, page: int = 1, retries: int = 3) -> List[KijijiListing]:
        url = self.build_url(category_path, category_id, page)
        
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    listings = self._parse_html(resp.text)
                    if listings:
                        return listings
                    else:
                        if "429 Too Many Requests" in resp.text or "Captcha" in resp.text:
                            logging.warning(f"Rate limit / captcha detected on {url}. Falling back to Playwright...")
                            return self.fetch_via_playwright(url)
                        return []
                elif resp.status_code in [403, 429]:
                    logging.warning(f"Rate limited (HTTP {resp.status_code}) on {url}. Falling back to Playwright headless browser...")
                    return self.fetch_via_playwright(url)
                else:
                    logging.warning(f"HTTP {resp.status_code} on {url}. Retrying ({attempt}/{retries})...")
                    time.sleep(2.0 * attempt)
            except Exception as e:
                logging.warning(f"Error fetching {url}: {e}. Retrying ({attempt}/{retries})...")
                time.sleep(2.0 * attempt)
                
        logging.warning(f"Requests failed for {url}. Attempting Playwright fallback...")
        return self.fetch_via_playwright(url)

    def _parse_html(self, html: str) -> List[KijijiListing]:
        soup = BeautifulSoup(html, 'html.parser')
        script = soup.find('script', id='__NEXT_DATA__')
        
        if script and script.string:
            try:
                data = json.loads(script.string)
                apollo = data.get('props', {}).get('pageProps', {}).get('__APOLLO_STATE__', {})
                listings = []
                
                for key, val in apollo.items():
                    if isinstance(val, dict) and val.get('__typename') in ['StandardListing', 'Listing']:
                        listing_obj = KijijiListing(val)
                        if listing_obj.id and listing_obj.title:
                            listings.append(listing_obj)
                            
                if listings:
                    return listings
            except Exception as e:
                logging.error(f"Error parsing Apollo state JSON: {e}")

        # Fallback: Parse HTML DOM cards directly if __NEXT_DATA__ missing or empty
        return self._parse_html_dom(soup)

    def _parse_html_dom(self, soup: BeautifulSoup) -> List[KijijiListing]:
        """Parses HTML DOM elements directly when __NEXT_DATA__ is not present."""
        listings = []
        title_links = soup.find_all('a', href=lambda h: h and '/v-' in h)
        seen_urls = set()
        
        for a in title_links:
            href = a.get('href', '')
            if not href or href in seen_urls or '/page-' in href:
                continue
            seen_urls.add(href)
            
            full_url = href if href.startswith('http') else f"https://www.kijiji.ca{href}"
            title = a.get_text(strip=True)
            if not title or len(title) < 4 or title.lower() in ['view ad', 'details', 'next', 'previous']:
                continue
                
            parent = a.find_parent(['li', 'div', 'article', 'section'])
            price_cad = 0.0
            image_url = None
            
            if parent:
                parent_text = parent.get_text(" ", strip=True)
                price_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', parent_text)
                if price_match:
                    try:
                        price_cad = float(price_match.group(1).replace(',', ''))
                    except ValueError:
                        pass
                
                img = parent.find('img')
                if img:
                    image_url = img.get('src') or img.get('data-src')
                    
            listing_id_match = re.search(r'/(\d+)(?:\?|$)', href)
            listing_id_str = listing_id_match.group(1) if listing_id_match else str(abs(hash(href)))
            
            dummy_data = {
                'id': listing_id_str,
                'title': title,
                'description': title,
                'url': full_url,
                'price': {'amount': int(price_cad * 100)},
                'location': {'name': "Winnipeg"},
                'media': [{'url': image_url}] if image_url else []
            }
            listings.append(KijijiListing(dummy_data))
            
        return listings

if __name__ == '__main__':
    with open('config.json', 'r') as f:
        cfg = json.load(f)
        
    scraper = KijijiScraper(cfg)
    print("Testing Winnipeg Tools category (page 1)...")
    results = scraper.fetch_page_listings('b-tool', '110', page=1)
    print(f"Extracted {len(results)} listings.")
    for l in results[:5]:
        print(f"\n- ID: {l.id} | Price: ${l.price_cad} CAD | Dist: {l.distance_km}km")
        print(f"  Title: {l.title}")
        print(f"  Desc: {l.description[:100]}...")
        print(f"  URL: {l.url}")
