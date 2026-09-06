"""
Notifier Module
Formats high-density arbitrage alerts and sends rich Discord Webhook embeds.
Includes full financial breakdown, condition evidence, and quick-access links.
"""

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from typing import Dict, Any, List, Optional

EMBED_COLORS = {
    'HOT': 3066993,        # Emerald Green (#2ecc71)
    'GOOD': 3447003,       # Blue (#3498db)
    'VERIFY': 15105570,    # Amber/Orange (#e67e22)
    'PRICE_DROP': 15844367 # Gold (#f1c40f)
}

class ArbitrageNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.notif_cfg = cfg.get('notifications', {})
        self.webhook_url = self.notif_cfg.get('discord_webhook_url', '').strip()
        self.enable_discord = bool(self.notif_cfg.get('enable_discord', False)) and bool(self.webhook_url)
        self.console_output = bool(self.notif_cfg.get('console_output', True))
        
        # Email settings
        self.gmail_address = self.notif_cfg.get('gmail_address', '').strip()
        self.gmail_app_password = self.notif_cfg.get('gmail_app_password', '').strip()
        self.notify_emails = self.notif_cfg.get('notify_emails', [])
        if isinstance(self.notify_emails, str):
            self.notify_emails = [e.strip() for e in self.notify_emails.split(',') if e.strip()]
        self.enable_email = bool(self.notif_cfg.get('enable_email', False)) and bool(self.gmail_address) and bool(self.gmail_app_password) and bool(self.notify_emails)

    def send_deal_alert(
        self,
        listing_title: str,
        listing_url: str,
        listing_price_cad: float,
        location_name: str,
        distance_km: float,
        image_url: Optional[str],
        matched_name: str,
        match_score: int,
        match_type: str,
        condition_score: int,
        condition_tier: str,
        condition_summary: str,
        condition_evidence: List[str],
        financials: Dict[str, Any],
        is_price_drop: bool = False,
        ai_reasoning: str = "",
        ai_confidence: int = 0
    ) -> bool:
        """Dispatches deal notification via Discord and Console."""
        
        # 1. Console Output
        if self.console_output:
            alert_prefix = "[PRICE DROP ALERT]" if is_price_drop else f"[{condition_tier} DEAL DETECTED]"
            print("\n" + "=" * 65)
            print(f" {alert_prefix} - {matched_name}")
            print("=" * 65)
            print(f"  Listing Title : {listing_title}")
            print(f"  Kijiji Asking : ${listing_price_cad:.2f} CAD (Max Buy: ${financials['max_buy_cad']:.2f} CAD)")
            print(f"  Expected Net  : ${financials['net_proceeds_cad']:.2f} CAD (${financials['ebay_resale_usd']:.2f} USD)")
            print(f"  Estimated Gain: ${financials['expected_profit_cad']:.2f} CAD (ROI: {financials['roi_pct']:.1f}%)")
            print(f"  Match Score   : {match_score}/100 ({match_type})")
            print(f"  Condition     : {condition_score}/100 [{condition_tier}] - {condition_summary}")
            if ai_reasoning:
                print(f"  AI Audit      : APPROVED ({ai_confidence}% confidence) - {ai_reasoning}")
            print(f"  Location      : {location_name} (~{distance_km}km from center)")
            print(f"  Link          : {listing_url}")
            if condition_evidence:
                print("  Evidence:")
                for ev in condition_evidence[:3]:
                    print(f"    {ev}")
            print("=" * 65 + "\n")
            
        # 2. Email Alert
        if self.enable_email:
            try:
                self.send_email_alert(
                    listing_title=listing_title,
                    listing_url=listing_url,
                    listing_price_cad=listing_price_cad,
                    location_name=location_name,
                    distance_km=distance_km,
                    image_url=image_url,
                    matched_name=matched_name,
                    match_score=match_score,
                    match_type=match_type,
                    condition_score=condition_score,
                    condition_tier=condition_tier,
                    condition_summary=condition_summary,
                    condition_evidence=condition_evidence,
                    financials=financials,
                    is_price_drop=is_price_drop,
                    ai_reasoning=ai_reasoning,
                    ai_confidence=ai_confidence
                )
            except Exception as e:
                logging.error(f"Error sending email alert: {e}")

        # 3. Discord Webhook Embed
        if not self.enable_discord or not self.webhook_url:
            return True
            
        color = EMBED_COLORS['PRICE_DROP'] if is_price_drop else EMBED_COLORS.get(condition_tier, EMBED_COLORS['GOOD'])
        header_tag = "🚨 PRICE DROP ALERT" if is_price_drop else f"🎯 [{condition_tier}] ARBITRAGE OPPORTUNITY"
        
        fields = [
            {
                "name": "💰 Price vs Max Buy Limit",
                "value": f"**Asking:** ${listing_price_cad:.2f} CAD\n**Max Buy:** ${financials['max_buy_cad']:.2f} CAD\n**Target Resale:** ${financials['ebay_resale_usd']:.2f} USD",
                "inline": True
            },
            {
                "name": "📈 Profit & ROI Projection",
                "value": f"**Net Proceeds:** ${financials['net_proceeds_cad']:.2f} CAD\n**Est. Profit:** ${financials['expected_profit_cad']:.2f} CAD\n**ROI:** {financials['roi_pct']:.1f}%",
                "inline": True
            },
            {
                "name": f"🔍 Match: {matched_name}",
                "value": f"**Match Score:** {match_score}/100 (`{match_type}`)\n**Location:** {location_name} ({distance_km} km)",
                "inline": False
            },
            {
                "name": f"🛠️ Condition Score: {condition_score}/100 ({condition_tier})",
                "value": f"*{condition_summary}*\n" + "\n".join(condition_evidence[:3] if condition_evidence else ["*No explicit defects mentioned*"]),
                "inline": False
            }
        ]
        
        embed = {
            "title": f"{header_tag}: {listing_title}",
            "url": listing_url,
            "color": color,
            "fields": fields,
            "footer": {
                "text": "Kijiji Arbitrage Radar • Winnipeg (20km radius)"
            }
        }
        
        if image_url:
            embed["thumbnail"] = {"url": image_url}
            
        payload = {
            "username": "Kijiji Arbitrage Radar",
            "embeds": [embed]
        }
        
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.status_code in [200, 204]
        except Exception as e:
            logging.error(f"Error sending Discord webhook alert: {e}")
            return False

    def send_email_alert(
        self,
        listing_title: str,
        listing_url: str,
        listing_price_cad: float,
        location_name: str,
        distance_km: float,
        image_url: Optional[str],
        matched_name: str,
        match_score: int,
        match_type: str,
        condition_score: int,
        condition_tier: str,
        condition_summary: str,
        condition_evidence: List[str],
        financials: Dict[str, Any],
        is_price_drop: bool = False,
        ai_reasoning: str = "",
        ai_confidence: int = 0
    ) -> bool:
        """Sends rich multipart HTML/text email notification via Gmail SMTP."""
        if not self.gmail_address or not self.gmail_app_password or not self.notify_emails:
            return False

        tag = "PRICE DROP" if is_price_drop else f"{condition_tier} DEAL"
        subject = f"[{tag}] +${financials['expected_profit_cad']:.0f} profit ({financials['roi_pct']:.0f}% ROI): {matched_name} - ${listing_price_cad:.0f} CAD"

        ai_line = f"AI Audit: APPROVED ({ai_confidence}% confidence) - {ai_reasoning}\n" if ai_reasoning else ""

        # Plain text fallback
        plain_text = f"""KIJIJI RESALE DEAL DETECTED!
=====================================================
Item: {matched_name}
Asking Price: ${listing_price_cad:.2f} CAD
Max Buy Limit: ${financials['max_buy_cad']:.2f} CAD
Target Resale: ${financials['ebay_resale_usd']:.2f} USD (~${financials['net_proceeds_cad']:.2f} CAD net)
Expected Profit: +${financials['expected_profit_cad']:.2f} CAD (ROI: {financials['roi_pct']:.1f}%)

Condition: {condition_score}/100 [{condition_tier}] - {condition_summary}
{ai_line}Match Score: {match_score}/100 ({match_type})
Location: {location_name} (~{distance_km:.1f} km from center)

Link to Kijiji Ad:
{listing_url}
=====================================================
"""

        # Rich HTML version
        evidence_html = "".join(f"<li style='margin-bottom: 4px;'>{ev}</li>" for ev in condition_evidence[:3])
        if not evidence_html:
            evidence_html = "<li>No defects detected</li>"

        badge_color = "#f1c40f" if is_price_drop else ("#2ecc71" if condition_tier == "HOT" else "#3498db")

        ai_html_box = ""
        if ai_reasoning:
            ai_html_box = f"""
      <div style="background: #e8f8f5; border: 1px solid #2ecc71; padding: 12px; margin-bottom: 20px; border-radius: 6px;">
        <b style="color: #27ae60;">🛡️ Gemini AI Verification Passed ({ai_confidence}% confidence):</b>
        <p style="margin: 6px 0 0 0; font-size: 13px; color: #2c3e50;">{ai_reasoning}</p>
      </div>"""

        html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; background-color: #f4f6f8; padding: 20px; color: #333;">
  <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
    <div style="background-color: {badge_color}; padding: 16px 20px; color: #fff; font-weight: bold; font-size: 18px;">
      {'🚨 Price Drop Alert' if is_price_drop else f'🎯 [{condition_tier}] Flip Opportunity'}: {matched_name}
    </div>
    
    <div style="padding: 20px;">
      <h2 style="margin-top: 0; font-size: 20px; color: #2c3e50;">{listing_title}</h2>
      
      <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
        <tr style="background: #eef5fc;">
          <td style="padding: 10px; font-weight: bold; width: 45%;">Asking Price:</td>
          <td style="padding: 10px; color: #c0392b; font-weight: bold; font-size: 16px;">${listing_price_cad:.2f} CAD</td>
        </tr>
        <tr>
          <td style="padding: 10px; font-weight: bold;">Max Buy Limit (75% ROI):</td>
          <td style="padding: 10px; color: #27ae60; font-weight: bold;">${financials['max_buy_cad']:.2f} CAD</td>
        </tr>
        <tr style="background: #eef5fc;">
          <td style="padding: 10px; font-weight: bold;">Est. Net Profit:</td>
          <td style="padding: 10px; color: #27ae60; font-weight: bold; font-size: 16px;">+${financials['expected_profit_cad']:.2f} CAD ({financials['roi_pct']:.1f}% ROI)</td>
        </tr>
        <tr>
          <td style="padding: 10px; font-weight: bold;">eBay Resale Target:</td>
          <td style="padding: 10px;">${financials['ebay_resale_usd']:.2f} USD</td>
        </tr>
        <tr style="background: #eef5fc;">
          <td style="padding: 10px; font-weight: bold;">Condition & Quality:</td>
          <td style="padding: 10px;"><b>{condition_score}/100 ({condition_tier})</b><br><small>{condition_summary}</small></td>
        </tr>
        <tr>
          <td style="padding: 10px; font-weight: bold;">Location:</td>
          <td style="padding: 10px;">{location_name} (~{distance_km:.1f} km)</td>
        </tr>
      </table>

      <div style="background: #f9f9f9; padding: 12px; border-left: 4px solid #3498db; margin-bottom: 20px; border-radius: 4px;">
        <b>Condition Evidence:</b>
        <ul style="margin: 6px 0 0 0; padding-left: 20px; font-size: 13px; color: #555;">
          {evidence_html}
        </ul>
      </div>{ai_html_box}

      <div style="text-align: center; margin-top: 25px;">
        <a href="{listing_url}" style="background-color: #3498db; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-weight: bold; display: inline-block; font-size: 16px;">
          View Kijiji Listing &rarr;
        </a>
      </div>
    </div>

    <div style="background-color: #ecf0f1; padding: 12px 20px; text-align: center; font-size: 12px; color: #7f8c8d;">
      Kijiji Arbitrage Radar • Winnipeg 20km Radius
    </div>
  </div>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.gmail_address
        msg["To"] = ", ".join(self.notify_emails)

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            server.login(self.gmail_address, self.gmail_app_password)
            server.sendmail(self.gmail_address, self.notify_emails, msg.as_string())

        logging.info(f"Email alert sent successfully to {len(self.notify_emails)} recipient(s).")
        return True

    def send_test_email(self) -> bool:
        """Sends a verification test email using configured Gmail credentials."""
        if not self.gmail_address or not self.gmail_app_password or not self.notify_emails:
            print("[ERROR] Email notifications not configured in config.json (gmail_address, gmail_app_password, or notify_emails missing).")
            return False

        print(f"Connecting to smtp.gmail.com:587 as '{self.gmail_address}'...")
        subject = "Kijiji Arbitrage Radar - Test Notification"
        body = "Congratulations! Your Gmail email notification setup is working perfectly.\nYou will receive alerts here whenever profitable arbitrage deals are found on Kijiji."
        
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = self.gmail_address
        msg["To"] = ", ".join(self.notify_emails)

        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.starttls()
                server.login(self.gmail_address, self.gmail_app_password)
                server.sendmail(self.gmail_address, self.notify_emails, msg.as_string())
            print(f"[SUCCESS] Test email successfully sent to: {', '.join(self.notify_emails)}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to send test email: {e}")
            return False

if __name__ == '__main__':
    with open('config.json', 'r') as f:
        cfg = json.load(f)
        
    notifier = ArbitrageNotifier(cfg)
    test_financials = {
        'ebay_resale_usd': 350.0,
        'conservative_gross_usd': 315.0,
        'net_proceeds_usd': 258.0,
        'net_proceeds_cad': 358.77,
        'kijiji_price_cad': 120.0,
        'max_buy_cad': 205.01,
        'expected_profit_cad': 238.77,
        'roi_pct': 199.0
    }
    
    notifier.send_deal_alert(
        listing_title="Fluke 87V Digital Multimeter in Box",
        listing_url="https://www.kijiji.ca/v-example/12345",
        listing_price_cad=120.0,
        location_name="St. Vital",
        distance_km=6.5,
        image_url="https://media.kijiji.ca/api/v1/ca-prod-fsbo-ads/images/sample.jpg",
        matched_name="Fluke 87V",
        match_score=100,
        match_type="EXACT_MODEL",
        condition_score=95,
        condition_tier="HOT",
        condition_summary="Explicitly verified working / excellent condition",
        condition_evidence=["[+] Explicitly tested and working: 'tested and works'", "[+] Works perfectly: 'works perfectly'"],
        financials=test_financials,
        is_price_drop=False
    )
