"""
Instantly API v2 client (email outreach).
Mirrors the shape of heyreach_client.py so the dashboard can consume it
uniformly.
"""

import os
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BASE_URL = 'https://api.instantly.ai/api/v2'


class InstantlyClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get('INSTANTLY_API_KEY', '')).strip()
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            })

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: Optional[Dict] = None, timeout: int = 30) -> Dict:
        url = f'{BASE_URL}/{path.lstrip("/")}'
        try:
            r = self.session.get(url, params=params, timeout=timeout)
            if r.status_code >= 400:
                logger.warning(f'Instantly {path} HTTP {r.status_code}: {r.text[:200]}')
                return {}
            return r.json()
        except Exception as e:
            logger.warning(f'Instantly {path} failed: {e}')
            return {}

    def get_accounts(self, limit: int = 100) -> List[Dict]:
        """Email sender accounts. Instantly caps `limit` at 100 per page."""
        d = self._get('accounts', params={'limit': min(limit, 100)})
        return d.get('items', []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    def get_campaigns(self, limit: int = 100) -> List[Dict]:
        """Email campaigns. Instantly caps `limit` at 100 per page."""
        d = self._get('campaigns', params={'limit': min(limit, 100)})
        return d.get('items', []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    def get_overview(self) -> Dict:
        """Aggregate analytics across all campaigns."""
        return self._get('campaigns/analytics/overview') or {}

    def get_daily_analytics(self, start_date: str, end_date: str) -> List[Dict]:
        """Daily series: sent / opened / replies / opportunities / etc."""
        d = self._get('campaigns/analytics/daily',
                      params={'start_date': start_date, 'end_date': end_date})
        return d if isinstance(d, list) else d.get('items', []) if isinstance(d, dict) else []

    def get_campaign_analytics(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """Per-campaign aggregated analytics."""
        params = {}
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
        d = self._get('campaigns/analytics', params=params)
        return d if isinstance(d, list) else d.get('items', []) if isinstance(d, dict) else []

    def get_dashboard_data(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict:
        """
        One-call wrapper for the dashboard: daily series + campaign breakdown +
        overview KPIs, all in one response.
        Mirrors the structure of get_sender_daily_performance from heyreach_client.
        """
        if not end_date:
            end_obj = datetime.now()
        else:
            end_obj = datetime.strptime(end_date, '%Y-%m-%d')
        if not start_date:
            start_obj = end_obj - timedelta(days=28)
        else:
            start_obj = datetime.strptime(start_date, '%Y-%m-%d')

        start_str = start_obj.strftime('%Y-%m-%d')
        end_str = end_obj.strftime('%Y-%m-%d')

        # Daily series
        daily = self.get_daily_analytics(start_str, end_str)

        # Aggregate from daily for the requested window (more accurate than overview which is lifetime)
        total_sent = 0
        total_opened = 0
        total_unique_opened = 0
        total_replies = 0
        total_unique_replies = 0
        total_clicks = 0
        total_opportunities = 0
        for d in daily:
            total_sent += int(d.get('sent') or 0)
            total_opened += int(d.get('opened') or 0)
            total_unique_opened += int(d.get('unique_opened') or 0)
            total_replies += int(d.get('replies') or 0)
            total_unique_replies += int(d.get('unique_replies') or 0)
            total_clicks += int(d.get('clicks') or 0)
            total_opportunities += int(d.get('opportunities') or 0)

        open_rate = round((total_unique_opened / total_sent * 100), 2) if total_sent > 0 else 0
        reply_rate = round((total_unique_replies / total_sent * 100), 2) if total_sent > 0 else 0
        click_rate = round((total_clicks / total_sent * 100), 2) if total_sent > 0 else 0

        summary = {
            'date_range': {'start': start_str, 'end': end_str},
            'total_sent': total_sent,
            'total_opened': total_opened,
            'total_unique_opened': total_unique_opened,
            'open_rate': open_rate,
            'total_replies': total_replies,
            'total_unique_replies': total_unique_replies,
            'reply_rate': reply_rate,
            'total_clicks': total_clicks,
            'click_rate': click_rate,
            'total_opportunities': total_opportunities,
        }

        return {
            'daily': daily,
            'summary': summary,
        }

    def get_campaigns_enriched(self, start_date: Optional[str] = None,
                               end_date: Optional[str] = None) -> List[Dict]:
        """
        List campaigns enriched with their per-campaign analytics for the date range.
        Mirrors get_campaigns_with_stats from heyreach_client.
        """
        if not end_date:
            end_obj = datetime.now()
        else:
            end_obj = datetime.strptime(end_date, '%Y-%m-%d')
        if not start_date:
            start_obj = end_obj - timedelta(days=28)
        else:
            start_obj = datetime.strptime(start_date, '%Y-%m-%d')

        start_str = start_obj.strftime('%Y-%m-%d')
        end_str = end_obj.strftime('%Y-%m-%d')

        campaigns = self.get_campaigns()
        analytics = self.get_campaign_analytics(start_date=start_str, end_date=end_str)

        # Build lookup by campaign_id
        ana_by_id = {}
        for a in analytics:
            cid = a.get('campaign_id') or a.get('id')
            if cid:
                ana_by_id[cid] = a

        results = []
        # Status mapping for Instantly: 0=draft, 1=active, 2=paused, 3=completed, 4=running but evaluating
        status_map = {0: 'draft', 1: 'active', 2: 'paused', 3: 'completed', 4: 'active'}

        for c in campaigns:
            cid = c.get('id')
            if not cid:
                continue
            a = ana_by_id.get(cid, {})
            sent = int(a.get('emails_sent_count') or a.get('contacted_count') or 0)
            opened = int(a.get('open_count_unique') or 0)
            replied = int(a.get('reply_count_unique') or 0)
            clicks = int(a.get('link_click_count_unique') or 0)
            opportunities = int(a.get('total_opportunities') or 0)

            open_rate = round((opened / sent * 100), 2) if sent > 0 else 0
            reply_rate = round((replied / sent * 100), 2) if sent > 0 else 0

            results.append({
                'id': cid,
                'name': c.get('name') or 'Untitled',
                'status': status_map.get(int(c.get('status') or 0), 'unknown'),
                'audience': '',
                'started_at': c.get('timestamp_created'),
                'sent': sent,
                'accepted': opened,            # for symmetry with the HeyReach card UI (accepted ≈ opened for email)
                'messages': sent,              # in email, sent and messages are the same
                'replies': replied,
                'acceptanceRate': open_rate,   # "Open rate" mapped to acceptance bar in the UI
                'replyRate': reply_rate,
                'opportunities': opportunities,
                'totalLeads': sent,
                'leadsInProgress': 0,
                'leadsFinished': sent,
            })

        return results
