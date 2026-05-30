#!/usr/bin/env python3
"""
Outreach Performance Dashboard
Flask web application for HeyReach + Instantly outreach tracking
"""

import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, session, redirect
from flask_cors import CORS
import logging
import secrets

from heyreach_client import HeyReachClient
from supabase_client import SupabaseMessageStore
from instantly_client import InstantlyClient
from pipedrive_client import PipedriveClient
import gemini_evaluator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))
CORS(app)


# --- Error handlers ---

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


# --- Configuration ---

def load_config():
    """Load configuration from environment variables."""
    config = {}

    api_key = os.environ.get('HEYREACH_API_KEY', '').strip()
    base_url = os.environ.get('HEYREACH_BASE_URL', 'https://api.heyreach.io')

    sender_ids = []
    sender_names = {}

    sender_ids_str = os.environ.get('HEYREACH_SENDER_IDS', '').strip()
    if sender_ids_str:
        try:
            parsed = json.loads(sender_ids_str)
            if isinstance(parsed, list):
                sender_ids = parsed
        except json.JSONDecodeError:
            logger.warning("Failed to parse HEYREACH_SENDER_IDS")

    sender_names_str = os.environ.get('HEYREACH_SENDER_NAMES', '').strip()
    if sender_names_str:
        try:
            parsed = json.loads(sender_names_str)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    try:
                        sender_names[int(k)] = v
                    except (ValueError, TypeError):
                        sender_names[k] = v
        except json.JSONDecodeError:
            logger.warning("Failed to parse HEYREACH_SENDER_NAMES")

    config['heyreach'] = {
        'api_key': api_key,
        'base_url': base_url,
        'sender_ids': sender_ids,
        'sender_names': sender_names,
    }

    # Instantly config (placeholder for future)
    config['instantly'] = {
        'api_key': os.environ.get('INSTANTLY_API_KEY', '').strip(),
    }

    return config


def get_heyreach_client():
    """Create a HeyReach client from environment config."""
    config = load_config()
    hr = config.get('heyreach', {})
    api_key = hr.get('api_key', '')
    if not api_key:
        return None
    return HeyReachClient(
        api_key=api_key,
        base_url=hr.get('base_url', 'https://api.heyreach.io'),
        sender_ids=hr.get('sender_ids', []),
        sender_names=hr.get('sender_names', {}),
    )


# --- Auth ---

AUTH_USERNAME = os.environ.get('DASHBOARD_USERNAME', '').strip()
AUTH_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', '').strip()


def is_authenticated():
    return session.get('authenticated') is True


@app.route('/login', methods=['GET'])
def login_page():
    if is_authenticated():
        return redirect('/')
    return render_template('login.html')


@app.route('/login', methods=['POST'])
def login_submit():
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        return render_template('login.html', error='Dashboard authentication is not configured.'), 403
    username = (request.form.get('username') or '').strip()
    password = (request.form.get('password') or '').strip()
    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        session['authenticated'] = True
        return redirect('/')
    return render_template('login.html', error='Invalid username or password.'), 401


@app.route('/logout', methods=['POST', 'GET'])
def logout():
    session.pop('authenticated', None)
    return redirect('/login')


# --- Dashboard pages ---

@app.route('/')
def dashboard_page():
    if not is_authenticated():
        return redirect('/login')
    return render_template('dashboard.html')


# --- API: HeyReach ---

@app.route('/api/senders', methods=['GET'])
def api_senders():
    """Return available HeyReach senders.

    Prefers env-configured sender IDs (HEYREACH_SENDER_IDS). If none configured,
    falls back to the live API.
    """
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        config = load_config()
        hr = config.get('heyreach', {})
        configured_ids = hr.get('sender_ids', []) or []
        sender_names = hr.get('sender_names', {}) or {}

        senders = [{'id': 'all', 'name': 'All'}]

        if configured_ids:
            # Use env config as the source of truth
            for sid in configured_ids:
                sid_int = int(sid) if isinstance(sid, str) and str(sid).isdigit() else sid
                name = sender_names.get(sid_int) or sender_names.get(sid) or f'Sender {sid_int}'
                senders.append({'id': sid_int, 'name': name})
            return jsonify({'senders': senders})

        # No env config — fall back to live API
        client = get_heyreach_client()
        if not client:
            return jsonify({'senders': senders})

        accounts = client.get_linkedin_accounts(force_api=True) or []
        for acc in accounts:
            aid = acc.get('id')
            aid_int = int(aid) if aid and isinstance(aid, (str, float)) else aid
            name = (
                sender_names.get(aid_int)
                or sender_names.get(aid)
                or acc.get('linkedInUserListName')
                or acc.get('name')
                or f'Sender {aid}'
            )
            senders.append({'id': aid_int, 'name': name})

        return jsonify({'senders': senders})
    except Exception as e:
        logger.exception("api_senders error")
        return jsonify({'error': str(e)}), 500


@app.route('/api/heyreach', methods=['GET'])
def api_heyreach():
    """Return HeyReach DAILY performance data for the requested date range."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        client = get_heyreach_client()
        if not client:
            return jsonify({'error': 'HeyReach not configured. Set HEYREACH_API_KEY environment variable.'}), 503

        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        sender_id_param = request.args.get('sender_id', 'all')

        if not start_date or not end_date:
            end_d = datetime.now()
            start_d = end_d - timedelta(days=28)
            start_date = start_d.strftime('%Y-%m-%d')
            end_date = end_d.strftime('%Y-%m-%d')

        # Cap date range to 90 days
        start_d = datetime.strptime(start_date, '%Y-%m-%d')
        end_d = datetime.strptime(end_date, '%Y-%m-%d')
        if (end_d - start_d).days > 90:
            start_date = (end_d - timedelta(days=90)).strftime('%Y-%m-%d')

        sender_id_for_api = None if sender_id_param == 'all' else sender_id_param

        performance_data = client.get_sender_daily_performance(
            sender_id=sender_id_for_api,
            start_date=start_date,
            end_date=end_date
        )

        # Build summary across all senders / all days
        summary = {
            'total_senders': len(performance_data.get('senders', {})),
            'date_range': {
                'start': performance_data.get('start_date'),
                'end': performance_data.get('end_date'),
            },
            'total_connections_sent': 0,
            'total_connections_accepted': 0,
            'total_messages_sent': 0,
            'total_message_replies': 0,
        }
        for sender_name, day_rows in performance_data.get('senders', {}).items():
            for day in day_rows:
                summary['total_connections_sent'] += day.get('connections_sent', 0)
                summary['total_connections_accepted'] += day.get('connections_accepted', 0)
                summary['total_messages_sent'] += day.get('messages_sent', 0)
                summary['total_message_replies'] += day.get('message_replies', 0)

        cs = summary['total_connections_sent']
        ca = summary['total_connections_accepted']
        ms = summary['total_messages_sent']
        mr = summary['total_message_replies']
        summary['overall_acceptance_rate'] = round((ca / cs * 100), 2) if cs > 0 else 0
        summary['overall_reply_rate'] = round((mr / ms * 100), 2) if ms > 0 else 0

        return jsonify({'performance': performance_data, 'summary': summary})
    except Exception as e:
        logger.exception("api_heyreach error")
        return jsonify({'error': str(e)}), 500


# --- API: Campaigns ---

@app.route('/api/campaigns', methods=['GET'])
def api_campaigns():
    """Return campaigns enriched with per-campaign stats for the date range."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        client = get_heyreach_client()
        if not client:
            return jsonify({'campaigns': [], 'error': 'HeyReach not configured.'}), 200

        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        sender_id = request.args.get('sender_id', 'all')

        if not start_date or not end_date:
            end_d = datetime.now()
            start_d = end_d - timedelta(days=28)
            start_date = start_d.strftime('%Y-%m-%d')
            end_date = end_d.strftime('%Y-%m-%d')

        # Cap at 90 days
        start_d = datetime.strptime(start_date, '%Y-%m-%d')
        end_d = datetime.strptime(end_date, '%Y-%m-%d')
        if (end_d - start_d).days > 90:
            start_date = (end_d - timedelta(days=90)).strftime('%Y-%m-%d')

        campaigns = client.get_campaigns_with_stats(
            start_date=start_date,
            end_date=end_date,
            sender_id=sender_id if sender_id != 'all' else None,
        )
        return jsonify({'campaigns': campaigns, 'date_range': {'start': start_date, 'end': end_date}})
    except Exception as e:
        logger.exception("api_campaigns error")
        return jsonify({'error': str(e)}), 500


# --- API: Supabase Message Analysis (V1.1) ---

def _get_supabase_store():
    return SupabaseMessageStore()


@app.route('/api/messages/status', methods=['GET'])
def api_messages_status():
    """Returns whether Supabase is configured + reachable + has the table."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    store = _get_supabase_store()
    ping = store.ping()
    return jsonify({
        'supabase_configured': store.is_configured(),
        'gemini_configured': gemini_evaluator.is_configured(),
        **ping,
    })


@app.route('/api/messages/stats', methods=['GET'])
def api_messages_stats():
    """Aggregate KPIs for the Message Analysis section.

    Query params:
      start_date, end_date, sender_id (optional)
      auto_eval: 'true' (default) to evaluate up to 5 unevaluated rows inline
      auto_eval_limit: max rows to evaluate this call (default 5, cap 10)

    Auto-evaluation behavior: every time the dashboard loads, this endpoint
    classifies a small batch of unevaluated conversations through Gemini before
    computing stats — so the Message Analysis section "self-heals" each load.
    """
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    store = _get_supabase_store()
    if not store.is_configured():
        return jsonify({
            'configured': False,
            'message': 'Supabase not configured. Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in Render env.',
            'stats': None,
        })

    sender_id = request.args.get('sender_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    sender_int = None
    if sender_id and sender_id != 'all':
        try:
            sender_int = int(sender_id)
        except (ValueError, TypeError):
            pass

    # Auto-evaluate unevaluated rows on each load (matches original dashboard behavior)
    auto_eval = request.args.get('auto_eval', 'true').lower() != 'false'
    eval_limit = max(1, min(int(request.args.get('auto_eval_limit', 5)), 10))
    eval_summary = {'attempted': 0, 'updated': 0, 'failed': 0}

    if auto_eval and gemini_evaluator.is_configured():
        try:
            unevaluated = store.get_unevaluated(limit=eval_limit)
            eval_summary['attempted'] = len(unevaluated)
            for row in unevaluated:
                thread = row.get('conversation_thread') or []
                text = gemini_evaluator.conversation_to_text(thread)
                if not text:
                    eval_summary['failed'] += 1
                    continue
                evaluation = gemini_evaluator.evaluate_conversation(text)
                if evaluation.get('ok'):
                    ok = store.update_evaluation(
                        conversation_id=row['conversation_id'],
                        is_open=evaluation['is_open_conversation'],
                        is_interested=evaluation['is_interested'],
                        is_meeting_booked=evaluation.get('is_meeting_booked', False),
                        confidence=evaluation['confidence'],
                        reasoning=evaluation['reasoning'],
                        model=evaluation['model'],
                    )
                    if ok:
                        eval_summary['updated'] += 1
                    else:
                        eval_summary['failed'] += 1
                else:
                    eval_summary['failed'] += 1
        except Exception as e:
            logger.warning(f"Auto-evaluation pass failed: {e}")

    stats = store.get_stats(sender_id=sender_int, start_date=start_date, end_date=end_date)
    # meetings_booked is now AI-detected from conversation threads (single source of truth)
    stats['meetings_booked_source'] = 'ai'
    pipedrive_used = False

    return jsonify({
        'configured': True,
        'gemini_ready': gemini_evaluator.is_configured(),
        'pipedrive_ready': pipedrive_used,
        'stats': stats,
        'auto_eval': eval_summary,
    })


@app.route('/api/messages', methods=['GET'])
def api_messages_list():
    """Recent conversation rows (capped). Used for inspecting raw threads."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    store = _get_supabase_store()
    if not store.is_configured():
        return jsonify({'configured': False, 'messages': []})
    sender_id = request.args.get('sender_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    sender_int = None
    if sender_id and sender_id != 'all':
        try:
            sender_int = int(sender_id)
        except (ValueError, TypeError):
            pass
    rows = store.get_messages(sender_id=sender_int, start_date=start_date, end_date=end_date, limit=200)
    # Strip the bulky conversation_thread for list view
    light = [{k: v for k, v in r.items() if k != 'conversation_thread'} for r in rows]
    return jsonify({'configured': True, 'count': len(light), 'messages': light})


@app.route('/api/messages/evaluate', methods=['POST'])
def api_messages_evaluate():
    """Trigger AI evaluation on unevaluated conversations. Batch up to N at a time."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    store = _get_supabase_store()
    if not store.is_configured():
        return jsonify({'error': 'Supabase not configured'}), 503
    if not gemini_evaluator.is_configured():
        return jsonify({'error': 'No AI key set. Add OPENROUTER_API_KEY (or GEMINI_API_KEY) to Render env.'}), 503

    payload = request.get_json(silent=True) or {}
    limit = int(payload.get('limit', 20))
    rows = store.get_unevaluated(limit=limit)
    results = {'attempted': len(rows), 'updated': 0, 'failed': 0, 'errors': []}
    for row in rows:
        thread = row.get('conversation_thread') or []
        text = gemini_evaluator.conversation_to_text(thread)
        if not text:
            results['failed'] += 1
            continue
        evaluation = gemini_evaluator.evaluate_conversation(text)
        if not evaluation.get('ok'):
            results['failed'] += 1
            err = evaluation.get('error', 'unknown')
            if len(results['errors']) < 5:
                results['errors'].append(err[:160])
            continue
        ok = store.update_evaluation(
            conversation_id=row['conversation_id'],
            is_open=evaluation['is_open_conversation'],
            is_interested=evaluation['is_interested'],
            is_meeting_booked=evaluation.get('is_meeting_booked', False),
            confidence=evaluation['confidence'],
            reasoning=evaluation['reasoning'],
            model=evaluation['model'],
        )
        if ok:
            results['updated'] += 1
        else:
            results['failed'] += 1
    return jsonify(results)


# --- API: Leads board (V1.3) ---

@app.route('/api/leads/board', methods=['GET'])
def api_leads_board():
    """Kanban board: leads grouped into Open / Interested / Meeting Booked."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    store = _get_supabase_store()
    if not store.is_configured():
        return jsonify({'configured': False,
                        'columns': {'open': [], 'interested': [], 'meeting_booked': []},
                        'counts': {'open': 0, 'interested': 0, 'meeting_booked': 0}})
    sender_id = request.args.get('sender_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    sender_int = None
    if sender_id and sender_id != 'all':
        try:
            sender_int = int(sender_id)
        except (ValueError, TypeError):
            pass
    board = store.get_leads_board(sender_id=sender_int, start_date=start_date, end_date=end_date)
    return jsonify({'configured': True, **board})


@app.route('/api/leads/<path:conversation_id>', methods=['GET'])
def api_leads_detail(conversation_id):
    """Full conversation row for the slide-over drawer."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    store = _get_supabase_store()
    if not store.is_configured():
        return jsonify({'error': 'Supabase not configured'}), 503
    row = store.get_conversation(conversation_id)
    if not row:
        return jsonify({'error': 'Conversation not found'}), 404
    return jsonify({'conversation': row})


# --- API: Instantly (V1.2) ---

@app.route('/api/instantly/status', methods=['GET'])
def api_instantly_status():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    client = InstantlyClient()
    if not client.is_configured():
        return jsonify({'configured': False, 'message': 'INSTANTLY_API_KEY not set in Render env.'})
    accounts = client.get_accounts(limit=100)
    return jsonify({
        'configured': True,
        'account_count': len(accounts),
        'sample_account': accounts[0].get('email') if accounts else None,
    })


@app.route('/api/instantly/senders', methods=['GET'])
def api_instantly_senders():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    client = InstantlyClient()
    if not client.is_configured():
        return jsonify({'senders': [{'id': 'all', 'name': 'All'}], 'configured': False})
    accounts = client.get_accounts(limit=200)
    senders = [{'id': 'all', 'name': 'All'}]
    for a in accounts:
        name_parts = [a.get('first_name', ''), a.get('last_name', '')]
        full_name = ' '.join(p for p in name_parts if p).strip() or a.get('email')
        senders.append({'id': a.get('email'), 'name': full_name, 'email': a.get('email')})
    return jsonify({'senders': senders, 'configured': True})


@app.route('/api/instantly/dashboard', methods=['GET'])
def api_instantly_dashboard():
    """Single endpoint for the Email Outreach tab: daily perf + campaign breakdown + summary."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    client = InstantlyClient()
    if not client.is_configured():
        return jsonify({'configured': False, 'message': 'INSTANTLY_API_KEY not set in Render env.'})

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    if not start_date or not end_date:
        end_d = datetime.now()
        start_d = end_d - timedelta(days=28)
        start_date = start_d.strftime('%Y-%m-%d')
        end_date = end_d.strftime('%Y-%m-%d')

    # Cap at 90 days
    sd = datetime.strptime(start_date, '%Y-%m-%d')
    ed = datetime.strptime(end_date, '%Y-%m-%d')
    if (ed - sd).days > 90:
        start_date = (ed - timedelta(days=90)).strftime('%Y-%m-%d')

    dash = client.get_dashboard_data(start_date=start_date, end_date=end_date)
    campaigns = client.get_campaigns_enriched(start_date=start_date, end_date=end_date)

    return jsonify({
        'configured': True,
        'date_range': {'start': start_date, 'end': end_date},
        'daily': dash['daily'],
        'summary': dash['summary'],
        'campaigns': campaigns,
    })


# --- API: Pipedrive (V1.2) ---

@app.route('/api/pipedrive/status', methods=['GET'])
def api_pipedrive_status():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    client = PipedriveClient()
    return jsonify({'configured': client.is_configured(), **client.ping()})


@app.route('/api/pipedrive/funnel', methods=['GET'])
def api_pipedrive_funnel():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    client = PipedriveClient()
    if not client.is_configured():
        return jsonify({'configured': False, 'stages': []})
    return jsonify({'configured': True, 'stages': client.get_funnel()})


@app.route('/api/pipedrive/meetings', methods=['GET'])
def api_pipedrive_meetings():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401
    client = PipedriveClient()
    if not client.is_configured():
        return jsonify({'configured': False, 'meetings_booked': 0})
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    summary = client.count_meetings_booked(start_date=start_date, end_date=end_date)
    deals = client.get_meeting_deals(start_date=start_date, end_date=end_date, limit=20)
    light = [{
        'id': d.get('id'),
        'title': d.get('title'),
        'value': d.get('value'),
        'currency': d.get('currency'),
        'stage_id': d.get('stage_id'),
        'add_time': d.get('add_time'),
        'update_time': d.get('update_time'),
        'person_name': d.get('person_name'),
        'org_name': d.get('org_name'),
    } for d in deals]
    return jsonify({'configured': True, **summary, 'deals': light})


# --- Health ---

@app.route('/api/health', methods=['GET'])
def health_check():
    config = load_config()
    return jsonify({
        'status': 'healthy',
        'heyreach_configured': bool(config.get('heyreach', {}).get('api_key')),
        'instantly_configured': bool(config.get('instantly', {}).get('api_key')),
    })


# --- Validation: multi-step health/integration check ---

@app.route('/validate', methods=['GET'])
def validate_page():
    if not is_authenticated():
        return redirect('/login')
    return render_template('validate.html')


@app.route('/api/validate', methods=['GET'])
def api_validate():
    """Run a battery of checks and return per-step pass/fail with detail."""
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 401

    checks = []
    config = load_config()
    hr = config.get('heyreach', {})
    api_key = hr.get('api_key', '')

    # 1. Env config
    configured = bool(api_key)
    checks.append({
        'id': 'env_config',
        'label': 'Environment configuration',
        'status': 'pass' if configured else 'fail',
        'detail': 'HEYREACH_API_KEY is set' if configured else 'HEYREACH_API_KEY missing — set it in Render env vars',
        'metric': '',
    })

    if not configured:
        return jsonify({'checks': checks, 'overall': 'fail'})

    sender_ids = hr.get('sender_ids', [])
    sender_names = hr.get('sender_names', {})
    checks.append({
        'id': 'env_senders',
        'label': 'Sender allowlist',
        'status': 'pass' if sender_ids else 'warn',
        'detail': f'{len(sender_ids)} sender IDs configured, {len(sender_names)} with name overrides'
                  if sender_ids else 'No HEYREACH_SENDER_IDS set — dashboard will fall back to all live API senders',
        'metric': str(len(sender_ids)),
    })

    client = get_heyreach_client()
    if not client:
        checks.append({
            'id': 'client_init',
            'label': 'HeyReach client initialization',
            'status': 'fail',
            'detail': 'Could not construct HeyReach client',
            'metric': '',
        })
        return jsonify({'checks': checks, 'overall': 'fail'})

    # 2. Live API connectivity
    try:
        live_accounts = client.get_linkedin_accounts(force_api=True) or []
        checks.append({
            'id': 'api_auth',
            'label': 'HeyReach API authentication',
            'status': 'pass',
            'detail': f'Connected to api.heyreach.io and fetched {len(live_accounts)} LinkedIn account(s)',
            'metric': str(len(live_accounts)),
        })
    except Exception as e:
        checks.append({
            'id': 'api_auth',
            'label': 'HeyReach API authentication',
            'status': 'fail',
            'detail': f'API call failed: {str(e)[:140]}',
            'metric': '',
        })
        return jsonify({'checks': checks, 'overall': 'fail'})

    # 3. Sender ID overlap (env vs live)
    live_ids = set()
    for acc in live_accounts:
        try:
            live_ids.add(int(acc.get('id')))
        except (ValueError, TypeError):
            pass
    env_ids = set()
    for sid in sender_ids:
        try:
            env_ids.add(int(sid))
        except (ValueError, TypeError):
            pass
    overlap = live_ids & env_ids
    only_env = env_ids - live_ids
    only_live = live_ids - env_ids

    if not env_ids:
        status = 'warn'
        detail = f'No env sender IDs to validate. Live API has {len(live_ids)} sender(s).'
    elif overlap == env_ids and not only_live:
        status = 'pass'
        detail = f'All {len(env_ids)} configured sender IDs found in live API.'
    elif overlap:
        status = 'warn'
        detail_bits = [f'{len(overlap)}/{len(env_ids)} configured senders match live API']
        if only_env:
            detail_bits.append(f'configured but missing in HeyReach: {sorted(only_env)}')
        if only_live:
            detail_bits.append(f'in HeyReach but not in env: {sorted(only_live)}')
        detail = '. '.join(detail_bits) + '.'
    else:
        status = 'fail'
        detail = f'No overlap between env IDs ({sorted(env_ids)}) and live API IDs ({sorted(live_ids)})'

    checks.append({
        'id': 'sender_overlap',
        'label': 'Sender ID consistency',
        'status': status,
        'detail': detail,
        'metric': f'{len(overlap)}/{len(env_ids) if env_ids else len(live_ids)}',
    })

    # 4. Performance fetch (last 7 days)
    end_d = datetime.now()
    start_d = end_d - timedelta(days=7)
    try:
        perf = client.get_sender_weekly_performance(
            sender_id=None,
            start_date=start_d.strftime('%Y-%m-%d'),
            end_date=end_d.strftime('%Y-%m-%d'),
        )
        sender_count = len(perf.get('senders', {}) or {})
        total_sent = 0
        for weeks in (perf.get('senders') or {}).values():
            for w in weeks:
                total_sent += int(w.get('connections_sent') or 0)
        checks.append({
            'id': 'performance_fetch',
            'label': 'Weekly performance fetch (last 7 days)',
            'status': 'pass' if sender_count > 0 else 'warn',
            'detail': f'Fetched data for {sender_count} sender(s); {total_sent} connections sent in window',
            'metric': str(total_sent),
        })
    except Exception as e:
        checks.append({
            'id': 'performance_fetch',
            'label': 'Weekly performance fetch (last 7 days)',
            'status': 'fail',
            'detail': f'Performance fetch failed: {str(e)[:140]}',
            'metric': '',
        })

    # 5. Campaigns list
    try:
        campaigns = client.get_campaigns() or []
        statuses = {}
        for c in campaigns:
            s = (c.get('status') or 'unknown').lower()
            statuses[s] = statuses.get(s, 0) + 1
        breakdown = ', '.join(f'{k}: {v}' for k, v in sorted(statuses.items()))
        checks.append({
            'id': 'campaigns_list',
            'label': 'Campaign list fetch',
            'status': 'pass' if campaigns else 'warn',
            'detail': f'{len(campaigns)} campaigns found ({breakdown})' if campaigns else 'No campaigns found',
            'metric': str(len(campaigns)),
        })
    except Exception as e:
        checks.append({
            'id': 'campaigns_list',
            'label': 'Campaign list fetch',
            'status': 'fail',
            'detail': f'Campaign list failed: {str(e)[:140]}',
            'metric': '',
        })

    # 6. Auth + Instantly
    auth_ok = bool(AUTH_USERNAME and AUTH_PASSWORD)
    checks.append({
        'id': 'dashboard_auth',
        'label': 'Dashboard authentication',
        'status': 'pass' if auth_ok else 'fail',
        'detail': 'DASHBOARD_USERNAME and DASHBOARD_PASSWORD are set'
                  if auth_ok else 'Missing dashboard credentials — set in env',
        'metric': '',
    })

    # Supabase (V1.1 — message analysis)
    store = SupabaseMessageStore()
    if not store.is_configured():
        checks.append({
            'id': 'supabase',
            'label': 'Supabase message analysis (V1.1)',
            'status': 'warn',
            'detail': 'Optional — set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in Render env to enable Reply Classification + Meetings Booked.',
            'metric': '',
        })
    else:
        ping = store.ping()
        if not ping['ok']:
            checks.append({
                'id': 'supabase',
                'label': 'Supabase connectivity',
                'status': 'fail',
                'detail': ping.get('error') or 'Connection failed',
                'metric': '',
            })
        elif not ping['table_exists']:
            checks.append({
                'id': 'supabase',
                'label': 'Supabase schema',
                'status': 'fail',
                'detail': 'heyreach_messages table missing. Run supabase_schema.sql in the Supabase SQL editor.',
                'metric': '',
            })
        else:
            total = store.count()
            evaluated = store.count(ai_evaluated='eq.true')
            coverage = round((evaluated / total * 100), 1) if total > 0 else 0
            if total == 0:
                detail = 'Schema deployed but no replies captured yet. Set up the n8n workflow + register HeyReach webhook to start populating.'
                status = 'warn'
            else:
                detail = f'{total} conversations stored, {evaluated} AI-evaluated ({coverage}% coverage).'
                status = 'pass'
            checks.append({
                'id': 'supabase',
                'label': 'Supabase message analysis',
                'status': status,
                'detail': detail,
                'metric': str(total),
            })

    # Instantly (V1.2 — email outreach)
    inst_client = InstantlyClient()
    if not inst_client.is_configured():
        checks.append({
            'id': 'instantly',
            'label': 'Instantly email outreach (V1.2)',
            'status': 'warn',
            'detail': 'Optional — set INSTANTLY_API_KEY in Render env to enable the Email Outreach tab.',
            'metric': '',
        })
    else:
        try:
            accounts = inst_client.get_accounts(limit=10)
            if accounts:
                checks.append({
                    'id': 'instantly',
                    'label': 'Instantly email outreach',
                    'status': 'pass',
                    'detail': f'{len(accounts)} email account(s) connected.',
                    'metric': str(len(accounts)),
                })
            else:
                checks.append({
                    'id': 'instantly',
                    'label': 'Instantly email outreach',
                    'status': 'warn',
                    'detail': 'Connected but no email accounts found in this workspace.',
                    'metric': '0',
                })
        except Exception as e:
            checks.append({
                'id': 'instantly',
                'label': 'Instantly email outreach',
                'status': 'fail',
                'detail': f'Instantly API call failed: {str(e)[:140]}',
                'metric': '',
            })

    # Pipedrive (V1.2 — meetings booked)
    pd_client = PipedriveClient()
    if not pd_client.is_configured():
        missing = []
        if not pd_client.api_token:
            missing.append('PIPEDRIVE_API_TOKEN')
        if not pd_client.company_domain:
            missing.append('PIPEDRIVE_COMPANY_DOMAIN')
        checks.append({
            'id': 'pipedrive',
            'label': 'Pipedrive meetings booked (V1.2)',
            'status': 'warn',
            'detail': f'Optional — set {", ".join(missing) or "PIPEDRIVE_API_TOKEN and PIPEDRIVE_COMPANY_DOMAIN"} to wire real meetings booked.',
            'metric': '',
        })
    else:
        ping = pd_client.ping()
        if ping['ok']:
            try:
                meeting_summary = pd_client.count_meetings_booked()
                count = meeting_summary['meetings_booked']
                checks.append({
                    'id': 'pipedrive',
                    'label': 'Pipedrive meetings booked',
                    'status': 'pass',
                    'detail': f'Connected to {ping.get("company")} ({ping.get("company_domain")}.pipedrive.com). {count} deals currently in meeting stages {meeting_summary.get("meeting_stage_ids")}.',
                    'metric': str(count),
                })
            except Exception as e:
                checks.append({
                    'id': 'pipedrive',
                    'label': 'Pipedrive meetings booked',
                    'status': 'warn',
                    'detail': f'Connected but funnel query failed: {str(e)[:140]}',
                    'metric': '',
                })
        else:
            checks.append({
                'id': 'pipedrive',
                'label': 'Pipedrive meetings booked',
                'status': 'fail',
                'detail': ping.get('error') or 'Pipedrive auth failed',
                'metric': '',
            })

    # AI evaluator (OpenRouter -> Gemini 2.5 Flash by default)
    if gemini_evaluator.is_configured():
        checks.append({
            'id': 'ai',
            'label': 'AI sentiment classification',
            'status': 'pass',
            'detail': 'AI key is set. Replies will be auto-classified using ' + gemini_evaluator.get_model() + '.',
            'metric': gemini_evaluator.get_model(),
        })
    else:
        checks.append({
            'id': 'ai',
            'label': 'AI sentiment classification (V1.1)',
            'status': 'warn',
            'detail': 'Optional — set OPENROUTER_API_KEY (or GEMINI_API_KEY) in Render env to auto-classify replies.',
            'metric': '',
        })

    overall = 'pass'
    if any(c['status'] == 'fail' for c in checks):
        overall = 'fail'
    elif any(c['status'] == 'warn' for c in checks):
        overall = 'warn'

    return jsonify({'checks': checks, 'overall': overall, 'timestamp': datetime.now().isoformat()})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
