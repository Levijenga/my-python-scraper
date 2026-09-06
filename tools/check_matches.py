import sqlite3

conn = sqlite3.connect('kijiji_tracker.db')
c = conn.cursor()

# Breakdown of all audit log decisions
c.execute("SELECT reason, COUNT(*) as cnt FROM audit_log GROUP BY reason ORDER BY cnt DESC")
reasons = c.fetchall()
print("=== AUDIT LOG BREAKDOWN ===")
for r in reasons:
    print(f"  {r[0]}: {r[1]}")

# Any listings that were name-matched (regardless of price/condition outcome)
c.execute("SELECT title, price_cad, reason FROM audit_log WHERE reason != 'NO_MATCH' ORDER BY reason")
matches = c.fetchall()
print(f"\n=== MATCHED LISTINGS (name hit, any outcome) — {len(matches)} total ===")
for m in matches[:50]:
    print(f"  [{m[2]}] ${m[1]:.0f} CAD — {m[0][:90]}")

conn.close()
