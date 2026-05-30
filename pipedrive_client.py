"""
Pipedrive API v1 client.
Used to populate the 'Meetings Booked' KPI on the Message Analysis section.
"""

import os
import logging
import requests
from typing import Dict, List, Optional, Iterable
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PipedriveClient:
    def __init__(self, api_token: Optional[str] = None, company_domain: Optional[str] = None):
        self.api_token = (api_token or os.environ.get('PIPEDRIVE_API_TOKEN', '')).strip()
        raw_domain = (company_domain or os.environ.get('PIPEDRIVE_COMPANY_DOMAIN', '')).strip()
        # Be lenient: accept 'ospri', 'ospri.pipedrive.com', 'https://ospri.pipedrive.com', etc.
        # Strip protocol, strip trailing slashes/paths, strip '.pipedrive.com'
        d = raw_domain.lower()
        for prefix in ('https://', 'http://'):
            if d.startswith(prefix):
                d = d[len(prefix):]
        d = d.split('/')[0]
        if d.endswith('.pipedrive.com'):
            d = d[:-len('.pipedrive.com')]
        self.company_domain = d
        # Stage IDs that count as a "meeting booked" or further. Configurable via env.
        # For OSPR: stage 4 = "Scheduling", stage 6 = "Active Opp"
        stages_env = os.environ.get('PIPEDRIVE_MEETING_STAGES', '4,6').strip()
        try:
            self.meeting_stage_ids = [int(s.strip()) for s in stages_env.split(',') if s.strip()]
        except Exception:
            self.meeting_stage_ids = [4, 6]
        self.session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self.api_token and self.company_domain)

    @property
    def base_url(self) -> str:
        return f'https://{self.company_domain}.pipedrive.com/api/v1'

    def _get(self, path: str, params: Optional[Dict] = None, timeout: int = 30) -> Dict:
        if not self.is_configured():
            return {}
        params = dict(params or {})
        params['api_token'] = self.api_token
        try:
            r = self.session.get(f'{self.base_url}/{path.lstrip("/")}', params=params, timeout=timeout)
            if r.status_code >= 400:
                logger.warning(f'Pipedrive {path} HTTP {r.status_code}: {r.text[:200]}')
                return {}
            return r.json()
        except Exception as e:
            logger.warning(f'Pipedrive {path} failed: {e}')
            return {}

    def ping(self) -> Dict:
        """Lightweight auth check via /users/me.

        Tries the configured company_domain first, then falls back to
        api.pipedrive.com (which works for personal API tokens regardless of subdomain).
        """
        if not self.is_configured():
            return {'ok': False, 'error': 'PIPEDRIVE_API_TOKEN or PIPEDRIVE_COMPANY_DOMAIN not set'}

        # Try configured domain
        d = self._get('users/me')
        if d.get('success'):
            u = d.get('data', {})
            return {
                'ok': True,
                'user': u.get('name'),
                'company': u.get('company_name'),
                'company_domain': u.get('company_domain'),
                'error': None,
            }

        # Fallback: try api.pipedrive.com directly (some tokens work there)
        try:
            r = self.session.get(
                'https://api.pipedrive.com/v1/users/me',
                params={'api_token': self.api_token},
                timeout=20,
            )
            if r.status_code == 200:
                fallback = r.json()
                if fallback.get('success'):
                    u = fallback.get('data', {})
                    # Auto-fix the company_domain for future calls
                    correct_domain = u.get('company_domain')
                    if correct_domain and correct_domain != self.company_domain:
                        logger.warning(
                            f'Pipedrive: configured domain "{self.company_domain}" did not work; '
                            f'auto-correcting to "{correct_domain}" from API response')
                        self.company_domain = correct_domain
                    return {
                        'ok': True,
                        'user': u.get('name'),
                        'company': u.get('company_name'),
                        'company_domain': u.get('company_domain'),
                        'error': None,
                        'auto_corrected': True,
                    }
        except Exception as e:
            logger.warning(f'Pipedrive fallback ping failed: {e}')

        # Both failed — surface a useful error
        return {
            'ok': False,
            'error': f'Pipedrive auth failed for domain "{self.company_domain}". '
                     f'Check that PIPEDRIVE_API_TOKEN is correct and PIPEDRIVE_COMPANY_DOMAIN '
                     f'is just the subdomain (e.g. "ospri"), not the full URL.',
        }

    def get_stages(self) -> List[Dict]:
        d = self._get('stages')
        return d.get('data') or []

    def get_pipelines(self) -> List[Dict]:
        d = self._get('pipelines')
        return d.get('data') or []

    def get_deals(self, stage_id: Optional[int] = None, status: str = 'all_not_deleted',
                  limit: int = 500) -> List[Dict]:
        params = {'limit': min(limit, 500), 'status': status}
        if stage_id is not None:
            params['stage_id'] = stage_id
        d = self._get('deals', params=params)
        return d.get('data') or []

    def get_funnel(self) -> List[Dict]:
        """Return [{stage_id, stage_name, count, value}] for the whole pipeline."""
        stages = self.get_stages()
        deals = self.get_deals()
        by_stage = {}
        for d in deals:
            sid = d.get('stage_id')
            if sid is None:
                continue
            entry = by_stage.setdefault(sid, {'count': 0, 'value': 0})
            entry['count'] += 1
            try:
                entry['value'] += float(d.get('value') or 0)
            except (ValueError, TypeError):
                pass
        result = []
        for s in stages:
            sid = s.get('id')
            entry = by_stage.get(sid, {'count': 0, 'value': 0})
            result.append({
                'stage_id': sid,
                'stage_name': s.get('name'),
                'pipeline_id': s.get('pipeline_id'),
                'order_nr': s.get('order_nr'),
                'count': entry['count'],
                'value': entry['value'],
                'is_meeting_stage': sid in self.meeting_stage_ids,
            })
        # Sort by pipeline + order
        result.sort(key=lambda r: (r.get('pipeline_id') or 0, r.get('order_nr') or 0))
        return result

    def count_meetings_booked(self, start_date: Optional[str] = None,
                              end_date: Optional[str] = None) -> Dict:
        """
        Count deals currently in meeting-booked-or-later stages.

        We use 'currently in stage' as the metric. If date range is provided,
        we additionally filter by add_time within range.
        """
        all_deals = []
        for sid in self.meeting_stage_ids:
            all_deals.extend(self.get_deals(stage_id=sid))

        if not start_date and not end_date:
            return {
                'meetings_booked': len(all_deals),
                'meeting_stage_ids': self.meeting_stage_ids,
                'in_range_filter': False,
            }

        # Filter by add_time within [start_date, end_date]
        start_iso = f'{start_date}T00:00:00' if start_date else None
        end_iso = f'{end_date}T23:59:59' if end_date else None
        in_range = []
        for d in all_deals:
            ts = d.get('add_time') or d.get('update_time')
            if not ts:
                continue
            if start_iso and ts < start_iso:
                continue
            if end_iso and ts > end_iso:
                continue
            in_range.append(d)

        return {
            'meetings_booked': len(in_range),
            'meetings_booked_lifetime': len(all_deals),
            'meeting_stage_ids': self.meeting_stage_ids,
            'in_range_filter': True,
            'date_range': {'start': start_date, 'end': end_date},
        }

    def get_meeting_deals(self, start_date: Optional[str] = None,
                          end_date: Optional[str] = None,
                          limit: int = 50) -> List[Dict]:
        """Return the most recent deals in meeting-booked stages (for the dashboard list)."""
        deals = []
        for sid in self.meeting_stage_ids:
            deals.extend(self.get_deals(stage_id=sid))
        # Filter by date if provided
        if start_date or end_date:
            start_iso = f'{start_date}T00:00:00' if start_date else None
            end_iso = f'{end_date}T23:59:59' if end_date else None
            deals = [
                d for d in deals
                if (not start_iso or (d.get('add_time') or '') >= start_iso)
                and (not end_iso or (d.get('add_time') or '') <= end_iso)
            ]
        # Sort most recent first
        deals.sort(key=lambda d: d.get('add_time') or '', reverse=True)
        return deals[:limit]
