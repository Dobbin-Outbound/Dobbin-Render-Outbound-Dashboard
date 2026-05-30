"""
Reply sentiment classifier via OpenRouter (default: Gemini 2.5 Flash).

Uses OpenRouter's OpenAI-compatible API. Set AI_MODEL env var to switch models
without code changes (e.g., 'anthropic/claude-haiku-4.5', 'openai/gpt-4o-mini').

Falls back to direct Gemini API if only GEMINI_API_KEY is set.
File name kept for backwards compatibility with existing imports.
"""

import os
import json
import logging
import requests
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get('AI_MODEL', 'google/gemini-2.5-flash')
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
GEMINI_DIRECT_URL = 'https://generativelanguage.googleapis.com/v1beta/models'
APP_REFERRER = 'https://outbound-dashboard-wai9.onrender.com'
APP_TITLE = 'Outbound Dashboard'

EVALUATION_PROMPT = """You are analyzing a LinkedIn outreach conversation between an SDR
(the SENDER, who works for our client and is pitching a specific product or service) and a
PROSPECT. Classify the prospect's state. Read the WHOLE thread before deciding.

CONVERSATION:
{conversation}

These three flags are mutually reinforcing in this order:
meeting_booked  >  interested  >  open_conversation
A conversation that does not qualify for ANY of the three is simply "not engaged"
(set all three false) and will be hidden from the board. Do NOT inflate.

--------------------------------------------------------------------
is_open_conversation = true ONLY if BOTH:
  (a) the prospect has actually REPLIED with a real human message, AND
  (b) the prospect is genuinely engaging / open to a two-way dialogue —
      asking questions, responding substantively, willing to keep talking,
      or otherwise actively in conversation.

is_open_conversation = false if ANY of:
  - The prospect never replied (only the sender messaged)
  - An automated / out-of-office / "I'm away" auto-reply with no real engagement
  - A pure brush-off or disengagement: "no thanks", "not interested",
    "not a fit", "we already have a solution", "remove me", "stop messaging"
  - A dead-end pleasantry that closes the loop with no openness to continue
    ("thanks, take care", "all good, cheers") and nothing else
  - Connection accepted but zero substantive reply

--------------------------------------------------------------------
is_interested = true ONLY if the prospect shows real interest in THE SENDER'S
specific product/service (the thing the sender pitched in this thread):
  - Asks about the offering, how it works, pricing, a proposal, or next steps ON IT
  - Says they want to learn more about that specific product/service
  - Agrees to a call/demo to discuss that offering
  - Asks the sender to send materials about the offering / shares their email
    expressly to receive info about it
  - Loops in a colleague to evaluate the offering

is_interested = false if:
  - Generic friendliness with no interest in the offering ("thanks for reaching out")
  - Interest is only in networking / the sender personally, not the product
  - Vague non-commitment ("maybe later", "sounds interesting") with no concrete ask
  - They asked who the sender is but never engaged with the actual offer
If is_interested is true, is_open_conversation must also be true.

--------------------------------------------------------------------
is_meeting_booked = true ONLY if the thread shows a meeting/call was actually
agreed or confirmed BY THE PROSPECT:
  - The prospect accepted a specific proposed time ("Tuesday 2pm works", "yes that works")
  - The prospect confirmed they booked / used a scheduling link ("just booked",
    "added to my calendar", "see you then")
  - The prospect explicitly says the meeting/call is set ("confirmed", "booked",
    "looking forward to the call")

is_meeting_booked = false if:
  - The sender only OFFERED a call or sent a booking link with no prospect confirmation
  - The prospect said "maybe", "let me check", "send me times" (no concrete confirmation)
  - Only interest was expressed but no meeting was actually locked in
If is_meeting_booked is true, is_interested AND is_open_conversation must also be true.

--------------------------------------------------------------------
Respond with ONLY a valid JSON object, no other text:
{{
  "is_open_conversation": true or false,
  "is_interested": true or false,
  "is_meeting_booked": true or false,
  "confidence": 0.0 to 1.0,
  "reasoning": "1-2 sentences citing what in the thread drove the decision"
}}"""


def _get_keys():
    """Returns (provider, api_key) tuple. provider is 'openrouter' or 'gemini' or None."""
    or_key = os.environ.get('OPENROUTER_API_KEY', '').strip().strip('<>')
    if or_key:
        return ('openrouter', or_key)
    gem_key = os.environ.get('GEMINI_API_KEY', '').strip().strip('<>')
    if gem_key:
        return ('gemini', gem_key)
    return (None, None)


def is_configured() -> bool:
    provider, _ = _get_keys()
    return provider is not None


def get_model() -> str:
    return DEFAULT_MODEL


def _parse_json_response(text: str) -> Dict:
    """Robustly extract JSON from LLM response (handles markdown fences)."""
    text = text.strip()
    if text.startswith('```'):
        # ```json ... ```  or  ``` ... ```
        text = text.split('```', 2)[1] if '```' in text[3:] else text
        if text.startswith('json'):
            text = text[4:]
        text = text.strip()
        if text.endswith('```'):
            text = text[:-3].strip()
    return json.loads(text)


def _evaluate_via_openrouter(prompt: str, model: str, key: str) -> Dict:
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': APP_REFERRER,
        'X-Title': APP_TITLE,
    }
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.1,
        'max_tokens': 500,
        'response_format': {'type': 'json_object'},
    }
    r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=45)
    if r.status_code != 200:
        return {'_http_error': f'OpenRouter HTTP {r.status_code}: {r.text[:200]}'}
    data = r.json()
    if 'choices' not in data or not data['choices']:
        return {'_http_error': f'No choices in response: {json.dumps(data)[:200]}'}
    text = data['choices'][0].get('message', {}).get('content', '')
    if not text:
        return {'_http_error': 'Empty response content'}
    try:
        return _parse_json_response(text)
    except json.JSONDecodeError as e:
        return {'_http_error': f'JSON parse error: {e}; raw: {text[:200]}'}


def _evaluate_via_gemini_direct(prompt: str, model: str, key: str) -> Dict:
    # model is in OpenRouter format ('google/gemini-2.5-flash'); strip provider prefix
    direct_model = model.split('/')[-1] if '/' in model else model
    url = f'{GEMINI_DIRECT_URL}/{direct_model}:generateContent'
    payload = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {
            'temperature': 0.1,
            'maxOutputTokens': 500,
            'responseMimeType': 'application/json',
        },
    }
    r = requests.post(url, params={'key': key}, json=payload, timeout=45)
    if r.status_code != 200:
        return {'_http_error': f'Gemini HTTP {r.status_code}: {r.text[:200]}'}
    data = r.json()
    candidates = data.get('candidates', [])
    if not candidates:
        return {'_http_error': 'No candidates in response'}
    text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
    try:
        return _parse_json_response(text)
    except json.JSONDecodeError as e:
        return {'_http_error': f'JSON parse error: {e}; raw: {text[:200]}'}


def evaluate_conversation(conversation_text: str, model: Optional[str] = None) -> Dict:
    """
    Classify a conversation thread.

    Returns:
        {
            'ok': bool,
            'is_open_conversation': bool|None,
            'is_interested': bool|None,
            'confidence': float|None,
            'reasoning': str|None,
            'model': str,
            'provider': str,
            'error': str|None,
        }
    """
    provider, key = _get_keys()
    use_model = model or DEFAULT_MODEL

    if not provider:
        return {
            'ok': False, 'is_open_conversation': None, 'is_interested': None, 'is_meeting_booked': None,
            'confidence': None, 'reasoning': None, 'model': use_model, 'provider': 'none',
            'error': 'No AI key set (OPENROUTER_API_KEY or GEMINI_API_KEY)',
        }

    if not conversation_text or not conversation_text.strip():
        return {
            'ok': False, 'is_open_conversation': None, 'is_interested': None, 'is_meeting_booked': None,
            'confidence': None, 'reasoning': None, 'model': use_model, 'provider': provider,
            'error': 'Empty conversation text',
        }

    prompt = EVALUATION_PROMPT.format(conversation=conversation_text)

    try:
        if provider == 'openrouter':
            result = _evaluate_via_openrouter(prompt, use_model, key)
        else:
            result = _evaluate_via_gemini_direct(prompt, use_model, key)

        if '_http_error' in result:
            return {
                'ok': False, 'is_open_conversation': None, 'is_interested': None, 'is_meeting_booked': None,
                'confidence': None, 'reasoning': None, 'model': use_model, 'provider': provider,
                'error': result['_http_error'],
            }

        is_booked = bool(result.get('is_meeting_booked'))
        is_interested = bool(result.get('is_interested'))
        is_open = bool(result.get('is_open_conversation'))
        # Enforce the hierarchy: booked -> interested -> open
        if is_booked:
            is_interested = True
        if is_interested:
            is_open = True
        return {
            'ok': True,
            'is_open_conversation': is_open,
            'is_interested': is_interested,
            'is_meeting_booked': is_booked,
            'confidence': float(result.get('confidence', 0.5)),
            'reasoning': str(result.get('reasoning', '')),
            'model': use_model,
            'provider': provider,
            'error': None,
        }
    except Exception as e:
        return {
            'ok': False, 'is_open_conversation': None, 'is_interested': None, 'is_meeting_booked': None,
            'confidence': None, 'reasoning': None, 'model': use_model, 'provider': provider,
            'error': str(e),
        }


def conversation_to_text(thread: list) -> str:
    """Convert a JSONB conversation_thread array into a readable transcript.

    HeyReach payload structure (real, observed):
      thread[i].sender = { full_name, ... }     # the SDR / LinkedIn account
      thread[i].lead   = { full_name, ... }     # the prospect
      thread[i].recent_messages = [
        { message: "...", is_reply: true|false, message_type: "Text|Attachment|Voice", creation_time }
      ]
    is_reply=true means it's FROM the prospect (an inbound reply),
    is_reply=false means it's FROM the SDR (outbound).
    """
    if not thread:
        return ''
    lines = []
    seen = set()  # dedupe across multiple webhook events that share recent_messages
    for evt in thread:
        if not isinstance(evt, dict):
            continue
        sender = evt.get('sender') or {}
        lead = evt.get('lead') or {}
        sdr_name = sender.get('full_name') or 'SDR'
        lead_name = lead.get('full_name') or 'Prospect'
        recent = evt.get('recent_messages') or []
        if not isinstance(recent, list):
            continue
        for m in recent:
            if not isinstance(m, dict):
                continue
            text = (m.get('message') or m.get('body') or m.get('text') or '').strip()
            mtype = (m.get('message_type') or 'Text').strip()
            if not text:
                # Non-text content (attachment, voice). Note it but skip body.
                if mtype and mtype.lower() != 'text':
                    text = f'[{mtype}]'
                else:
                    continue
            ts = m.get('creation_time') or evt.get('timestamp') or ''
            is_reply = m.get('is_reply', False)
            speaker = lead_name if is_reply else sdr_name
            key = (ts, speaker, text)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f'[{ts}] {speaker}: {text}')
    return '\n'.join(lines).strip()
