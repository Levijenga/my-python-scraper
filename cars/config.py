"""
Personal settings for the car scraper.

Edit the values below. You should not need to touch car_scraper.py at all,
except to adjust the coordinate-extraction patterns if extract_coordinates()
isn't finding lat/lng on real listing pages - see the note in car_scraper.py
and the --save-html option in debug_scraper.py.
"""

# ---- What to search for ----
# Paste one or more Kijiji search-result URLs here. The r50.0 at the end
# tells Kijiji to return listings within 50 km of Winnipeg.
KIJIJI_SEARCH_URLS = [
    "https://www.kijiji.ca/b-cars-trucks/winnipeg/cars/k0c174l1700192r50.0?sort=dateDesc",
]

# Only notify about listings priced in this range (in dollars), inclusive.
MIN_PRICE = 2500
MAX_PRICE = 5000

# ---- Location: hard 50 km limit from downtown Winnipeg ----
# Roughly Portage & Main.
WINNIPEG_CENTER_LAT = 49.8951
WINNIPEG_CENTER_LNG = -97.1384
RADIUS_KM = 50

# ---- Mileage / odometer limit ----
MAX_KM = 250000

# Fallback text-matching lists, used ONLY when a listing's page doesn't
# expose real coordinates. Expanded for 50 km coverage.
NEARBY_PLACE_ALLOWLIST = [
    # Winnipeg proper and neighbourhoods
    "winnipeg", "st. boniface", "st boniface", "st. vital", "st vital",
    "transcona", "east kildonan", "north kildonan", "west kildonan",
    "old kildonan", "st. james", "st james", "assiniboia", "charleswood",
    "fort garry", "river heights", "tuxedo", "wolseley", "elmwood",
    "point douglas", "southdale", "island lakes", "waverley west",
    "sage creek", "the maples", "garden city", "fort richmond",
    "linden woods", "whyte ridge", "bridgwater", "south pointe",
    "river park south", "windsor park", "north river heights",
    "crescentwood", "osborne village", "corydon", "mission gardens",
    "amber trails", "canterbury park", "dakota crossing", "royalwood",
    "richmond west", "inkster", "brooklands", "weston",

    # Within 50 km of downtown Winnipeg
    "east st. paul", "east st paul", "west st. paul", "west st paul",
    "headingley", "oak bluff", "oakbank", "oak bank",
    "stonewall", "lockport", "birds hill", "bird's hill",
    "dugald", "lorette", "st. adolphe", "st adolphe",
    "st. norbert", "st norbert", "la salle", "lasalle",
    "grande pointe", "ile des chenes", "île des chênes",
    "st. andrews", "st andrews", "st. clements", "st clements",
    "rosser", "springfield", "ritchot", "macdonald",
    "st. francois xavier", "st francois xavier",
    "brunkild", "domain", "sanford", "starbuck",
    "anola", "cooks creek", "hazelridge", "oakbluff",
    "kleefeld", "landmark", "niverville",
    "selkirk", "garson", "tyndall", "beausejour",
    "petersfield", "matlock", "winnipeg beach",
]

# Towns that are clearly beyond 50 km from downtown Winnipeg.
FAR_PLACE_DENYLIST = [
    "portage la prairie", "gimli", "winkler", "morden",
    "steinbach", "carman", "altona", "morris",
    "dauphin", "brandon", "thompson", "the pas",
    "lac du bonnet", "pine falls", "pinawa",
    "neepawa", "minnedosa", "virden", "swan river",
    "arborg", "riverton", "fisher branch",
    "emerson", "sprague", "vita", "grunthal",
    "ste. anne", "ste anne",
]

# What to do when a listing's location can't be confidently placed either
# way. "exclude" drops it silently. "flag" keeps it in the results with
# location_status == "UNKNOWN".
UNKNOWN_LOCATION_POLICY = "flag"  # "exclude" or "flag"

# ---- Safety status ----
# Every listing gets classified as SAFETIED / NOT_SAFETIED / UNKNOWN.
# NOT_SAFETIED listings are always dropped/ignored, never emailed.
# Set False so ONLY 100% confirmed SAFETIED cars get emailed.
INCLUDE_UNKNOWN_SAFETY = False

# ---- Checking behavior ----
CHECK_INTERVAL_MINUTES = 5
PAGES_TO_CHECK = 3

# Delay between opening each candidate listing's own detail page, in seconds.
DETAIL_FETCH_DELAY_SECONDS = 1.5

# ---- Email notifications (free, via Gmail) ----
GMAIL_ADDRESS = "jengaleviosa@gmail.com"
GMAIL_APP_PASSWORD = "ratv saoj wdzs pajo"  # the 16-character App Password

# Who gets the alert emails. Usually just your own address.
NOTIFY_EMAILS = [
    "jengaleviosa@gmail.com",
]
