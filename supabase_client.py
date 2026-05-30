"""
Lightweight Supabase client using direct PostgREST HTTP calls.
Avoids the heavy supabase-py dependency.

Used for V1.1 message analysis (HeyReach replies + AI sentiment).
"""

import os
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SupabaseMessageStore:
    """Reads from heyreach_messages table via PostgREST."""

    def __init__(self, url: Optional[str] = None, service_role_key: Optional[str] = None):
        self.url = (url or os.environ.get('SUPABASE_URL', '')).rstrip('/')
        self.key = service_role_key or os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
        self.table = 'heyreach_messages'
        self.session = requests.Session()
        if self.key:
            self.session.headers.update({
                'apikey': self.key,
                'Authorization': f'Bearer {self.key}',
                'Content-Type': 'application/json',
            })

    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    def _rest_url(self, path: str = '') -> str:
        return f'{self.url}/rest/v1/{path}'

    def ping(self) -> Dict:
        """Test connection. Returns {'ok': bool, 'error': str|None, 'table_exists': bool}."""
        if not self.is_configured():
            return {'ok': False, 'error': 'Supabase not configured (SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing)', 'table_exists': False}
        try:
            r = self.session.get(self._rest_url(self.table), params={'limit': 0}, timeout=10)
            if r.status_code == 200:
                return {'ok': True, 'error': None, 'table_exists': True}
            elif r.status_code == 404:
                return {'ok': True, 'error': f'Table "{self.table}" does not exist. Run supabase_schema.sql in the SQL editor.', 'table_exists': False}
            else:
                return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:200]}', 'table_exists': False}
        except Exception as e:
            return {'ok': False, 'error': str(e), 'table_exists': False}

    def count(self, **filters) -> int:
        """Get row count matching filters. Uses PostgREST HEAD + Prefer: count=exact."""
        if not self.is_configured():
            return 0
        try:
            params = {**filters, 'select': 'id'}
            headers = {'Prefer': 'count=exact', 'Range': '0-0'}
            r = self.session.get(self._rest_url(self.table), params=params, headers=headers, timeout=15)
            if r.status_code in (200, 206):
                cr = r.headers.get('Content-Range', '0/0')
                # Format: "0-0/N" or "*/N"
                if '/' in cr:
                    return int(cr.split('/')[-1])
            return 0
        except Exception as e:
            logger.warning(f"Supabase count failed: {e}")
            return 0

    def get_messages(self, sender_id: Optional[int] = None, start_date: Optional[str] = None,
                     end_date: Optional[str] = None, limit: int = 1000) -> List[Dict]:
        """Fetch messages with optional filters. Returns list of conversation rows."""
        if not self.is_configured():
            return []
        try:
            params = {
                'select': 'id,conversation_id,sender_id,sender_full_name,lead_full_name,lead_company_name,'
                          'campaign_id,campaign_name,latest_timestamp,latest_event_type,'
                          'ai_evaluated,is_open_conversation,is_interested,is_meeting_booked,'
                          'ai_confidence,ai_reasoning,lead,conversation_thread',
                'order': 'latest_timestamp.desc',
                'limit': str(limit),
            }
            if sender_id:
                params['sender_id'] = f'eq.{sender_id}'
            if start_date:
                params['latest_timestamp'] = f'gte.{start_date}T00:00:00Z'
            if end_date:
                # PostgREST allows multiple constraints on the same column via separate params... but
                # we can use the and= form. Simpler: use latest_timestamp twice in URL using a list.
                pass

            r = self.session.get(self._rest_url(self.table), params=params, timeout=20)
            # Graceful fallback: if is_meeting_booked column doesn't exist yet
            # (schema migration not run), retry without it so the rest of the
            # dashboard keeps working.
            if r.status_code == 400 and 'is_meeting_booked' in r.text:
                logger.warning("is_meeting_booked column missing; retrying without it "
                               "(run the schema migration in Supabase)")
                params['select'] = params['select'].replace(',is_meeting_booked', '')
                r = self.session.get(self._rest_url(self.table), params=params, timeout=20)
            if r.status_code == 200:
                rows = r.json()
                # If we have an end_date, filter client-side (PostgREST doesn't easily allow two
                # filters on same column via params dict)
                if end_date:
                    end_iso = f'{end_date}T23:59:59Z'
                    rows = [r for r in rows if (r.get('latest_timestamp') or '') <= end_iso]
                return rows
            logger.warning(f"Supabase get_messages HTTP {r.status_code}: {r.text[:200]}")
            return []
        except Exception as e:
            logger.warning(f"Supabase get_messages failed: {e}")
            return []

    def get_stats(self, sender_id: Optional[int] = None, start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> Dict:
        """Aggregate stats for the dashboard's Message Analysis section.

        Returns:
            {
                'total_conversations': int,
                'open_conversations': int,
                'interested': int,
                'meetings_booked': int,
                'ai_evaluated': int,
                'ai_unevaluated': int,
                'evaluation_coverage': float (0-100),
            }
        """
        rows = self.get_messages(sender_id=sender_id, start_date=start_date, end_date=end_date, limit=5000)
        total = len(rows)
        open_count = sum(1 for r in rows if r.get('is_open_conversation') is True)
        interested = sum(1 for r in rows if r.get('is_interested') is True)
        booked = sum(1 for r in rows if self._is_moved_to_email(r))
        evaluated = sum(1 for r in rows if r.get('ai_evaluated') is True)
        unevaluated = total - evaluated

        return {
            'total_conversations': total,
            'open_conversations': open_count,
            'interested': interested,
            'meetings_booked': booked,
            'ai_evaluated': evaluated,
            'ai_unevaluated': unevaluated,
            'evaluation_coverage': round((evaluated / total * 100), 2) if total > 0 else 0,
        }

    def update_evaluation(self, conversation_id: str, is_open: bool, is_interested: bool,
                          confidence: float, reasoning: str, model: str,
                          is_meeting_booked: bool = False) -> bool:
        """Write AI evaluation back to a conversation row."""
        if not self.is_configured():
            return False
        try:
            payload = {
                'ai_evaluated': True,
                'ai_evaluation_timestamp': datetime.utcnow().isoformat() + 'Z',
                'is_open_conversation': is_open,
                'is_interested': is_interested,
                'is_meeting_booked': bool(is_meeting_booked),
                'ai_confidence': round(float(confidence), 2),
                'ai_reasoning': reasoning,
                'ai_model_version': model,
            }
            r = self.session.patch(
                self._rest_url(self.table),
                params={'conversation_id': f'eq.{conversation_id}'},
                json=payload,
                headers={'Prefer': 'return=minimal'},
                timeout=15,
            )
            return r.status_code in (200, 204)
        except Exception as e:
            logger.warning(f"Supabase update_evaluation failed: {e}")
            return False

    def get_unevaluated(self, limit: int = 50) -> List[Dict]:
        """Fetch conversations that haven't been AI-evaluated yet (for batch processing)."""
        if not self.is_configured():
            return []
        try:
            params = {
                'select': 'id,conversation_id,conversation_thread,latest_timestamp',
                'ai_evaluated': 'eq.false',
                'order': 'latest_timestamp.desc',
                'limit': str(limit),
            }
            r = self.session.get(self._rest_url(self.table), params=params, timeout=20)
            return r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.warning(f"Supabase get_unevaluated failed: {e}")
            return []

    @staticmethod
    def _latest_snippet(thread: list) -> str:
        """Pull a 1-line snippet of the most recent message text from a thread."""
        if not thread:
            return ''
        last_text = ''
        for evt in thread:
            if not isinstance(evt, dict):
                continue
            for m in (evt.get('recent_messages') or []):
                if isinstance(m, dict):
                    t = (m.get('message') or '').strip()
                    if t:
                        last_text = t
        return (last_text[:120] + '…') if len(last_text) > 120 else last_text

    @staticmethod
    def _tag_text(t) -> str:
        """Normalize a HeyReach tag (string OR {name,...} dict) to its text."""
        if isinstance(t, str):
            return t
        if isinstance(t, dict):
            return t.get('name') or t.get('tag') or t.get('label') or ''
        return ''

    @classmethod
    def _has_moved_to_email_tag(cls, row: dict) -> bool:
        """True if the lead carries the HeyReach 'Emailed ✅' tag (client's
        source of truth for the Moved-To-Email stage). Emoji/case tolerant.

        Handles BOTH tag shapes HeyReach returns: plain strings
        ("Emailed ✅") and objects ({"name": "Emailed ✅", "campaignId": ...}).
        Takes precedence over the AI classification.
        """
        lead = row.get('lead') or {}
        if not isinstance(lead, dict):
            return False
        tags = lead.get('tags') or []
        if not isinstance(tags, list):
            return False
        for t in tags:
            if 'emailed' in cls._tag_text(t).strip().lower():
                return True
        return False

    @classmethod
    def _is_moved_to_email(cls, row: dict) -> bool:
        """Moved-To-Email = HeyReach 'Emailed ✅' tag OR AI is_meeting_booked."""
        return cls._has_moved_to_email_tag(row) or (row.get('is_meeting_booked') is True)

    @classmethod
    def _bucket(cls, row: dict):
        """Classify a row into exactly one Kanban column (highest state wins).

        Returns None for conversations that don't qualify for any column
        (not engaged / brush-off / OOO / not yet AI-evaluated) — these are
        excluded from the board entirely.
        """
        if cls._is_moved_to_email(row):
            return 'meeting_booked'  # column key stays; UI label is "Moved To Email"
        if row.get('is_interested') is True:
            return 'interested'
        if row.get('is_open_conversation') is True:
            return 'open'
        # Not engaged, or not yet AI-evaluated -> not shown on the board
        return None

    def get_leads_board(self, sender_id: Optional[int] = None,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> Dict:
        """
        Return leads grouped into Kanban columns with light card data only
        (no full conversation_thread — that's fetched on demand by the drawer).
        """
        rows = self.get_messages(sender_id=sender_id, start_date=start_date,
                                 end_date=end_date, limit=5000)
        columns = {'open': [], 'interested': [], 'meeting_booked': []}
        excluded = 0
        camp_moved = {}  # campaign_name -> count of moved-to-email
        for r in rows:
            if self._is_moved_to_email(r):
                cname = r.get('campaign_name') or 'Unknown campaign'
                camp_moved[cname] = camp_moved.get(cname, 0) + 1
            bucket = self._bucket(r)
            if bucket is None:
                excluded += 1
                continue
            card = {
                'conversation_id': r.get('conversation_id'),
                'lead_name': r.get('lead_full_name') or 'Unknown',
                'company': r.get('lead_company_name') or '',
                'sender_name': r.get('sender_full_name') or '',
                'last_activity': r.get('latest_timestamp') or '',
                'snippet': self._latest_snippet(r.get('conversation_thread') or []),
                'confidence': r.get('ai_confidence'),
                'ai_evaluated': r.get('ai_evaluated') is True,
            }
            columns[bucket].append(card)

        # Sort each column most-recent-activity first
        for c in columns.values():
            c.sort(key=lambda x: x.get('last_activity') or '', reverse=True)

        top_campaigns = sorted(
            ({'campaign': k, 'moved_to_email': v} for k, v in camp_moved.items()),
            key=lambda x: x['moved_to_email'], reverse=True,
        )

        return {
            'columns': columns,
            'counts': {k: len(v) for k, v in columns.items()},
            'total': sum(len(v) for v in columns.values()),
            'excluded': excluded,  # not-engaged convos hidden from the board
            'top_campaigns': top_campaigns,  # ranked by Moved-To-Email for Top Performer
        }

    def get_conversation(self, conversation_id: str) -> Optional[Dict]:
        """Full conversation row for the slide-over drawer."""
        if not self.is_configured():
            return None
        try:
            params = {
                'select': '*',
                'conversation_id': f'eq.{conversation_id}',
                'limit': '1',
            }
            r = self.session.get(self._rest_url(self.table), params=params, timeout=20)
            if r.status_code == 200:
                rows = r.json()
                return rows[0] if rows else None
            return None
        except Exception as e:
            logger.warning(f"Supabase get_conversation failed: {e}")
            return None
