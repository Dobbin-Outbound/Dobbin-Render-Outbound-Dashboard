# Leads Tab (Kanban + Reporting) — Design Spec

**Date:** 2026-05-17
**Status:** Approved
**Project:** outbound-dashboard (neo-vibe/render-outbound-dashboard)

## Goal

Add a third top-level tab, **"Leads"**, that gives the client a Kanban view of
generated leads grouped by AI-detected conversation state (Open / Interested /
Meeting Booked), plus a reporting strip of outreach KPIs. Clicking a lead card
opens a right slide-over drawer with full contact info, the AI's reasoning, and
the complete conversation transcript.

## Key Decisions

1. **Single source of truth:** Supabase `heyreach_messages`. No cross-source
   fuzzy matching. Pipedrive stays wired in code but is removed from all UI
   surfaces.
2. **Meetings Booked is AI-detected** from the conversation thread. The AI only
   counts a meeting as booked if the prospect genuinely confirmed one (specific
   time accepted / booking confirmed / explicit "booked"/"confirmed"), NOT when
   our side merely offered a call or sent a link with no confirmation.
3. **All "meetings booked" surfaces** (existing Message Analysis KPI + new Leads
   tab) use the AI-detected value. The Pipedrive override in
   `/api/messages/stats` is removed.
4. **Detail view:** right slide-over drawer (CRM pattern).
5. **Re-evaluation strategy:** add `is_meeting_booked` column, set all existing
   rows `ai_evaluated=false` to force re-eval; auto-eval drains them over a few
   loads; run one-shot `/api/messages/evaluate?limit=200` after deploy for
   immediate correctness.

## Architecture

```
Supabase heyreach_messages (single source)
  conversation_thread + AI eval: is_open_conversation, is_interested,
  is_meeting_booked (NEW), ai_confidence, ai_reasoning
        |
        +-- GET /api/leads/board?start&end&sender   -> 3 buckets, light cards
        +-- GET /api/leads/<conversation_id>         -> full thread + contact + AI
        +-- reporting strip reuses /api/heyreach + /api/messages/stats
        |
   Frontend "Leads" tab
        +-- Reporting strip (8 KPIs)
        +-- Kanban board (Open / Interested / Meeting Booked)
        +-- Right slide-over drawer (contact + transcript + AI reasoning)
```

## Schema Change

```sql
ALTER TABLE heyreach_messages ADD COLUMN IF NOT EXISTS is_meeting_booked BOOLEAN;
CREATE INDEX IF NOT EXISTS idx_hr_msgs_is_meeting_booked
  ON heyreach_messages(is_meeting_booked) WHERE is_meeting_booked = TRUE;
```

## AI Prompt Change (gemini_evaluator.py)

Returned JSON gains `is_meeting_booked`:

```json
{ "is_open_conversation": bool, "is_interested": bool,
  "is_meeting_booked": bool, "confidence": 0-1, "reasoning": "..." }
```

Rule added to the prompt:

> is_meeting_booked = true ONLY if the thread shows a meeting was actually
> agreed/confirmed by the prospect — a specific time accepted, a calendar/
> booking link confirmed as booked, or the prospect explicitly says
> "booked"/"confirmed"/"see you then". NOT true if our side merely offered a
> call, sent a booking link with no confirmation, or the prospect said
> "maybe"/"let me check". If a meeting is booked, is_interested is also true.

## Reporting Strip (8 KPIs)

| KPI | Source |
|---|---|
| Connection Requests | HeyReach summary |
| Accepted | HeyReach summary |
| Acceptance Rate | computed |
| Replies | HeyReach summary |
| Reply Rate | computed |
| Open Conversations | Supabase is_open_conversation |
| Interested | Supabase is_interested (label exactly "Interested") |
| Meetings Booked | Supabase is_meeting_booked |

## Kanban

Three mutually-exclusive columns (highest state wins), count badge per header,
view-only (no drag-drop — AI sets state):

- **Open**: is_open_conversation AND NOT is_interested AND NOT is_meeting_booked
- **Interested**: is_interested AND NOT is_meeting_booked
- **Meeting Booked**: is_meeting_booked

Card: lead full name, company, title, last-activity date, 1-line latest-message
snippet, confidence dot. Sorted most-recent-activity first. Respects sender +
date-range filters.

## Slide-over Drawer

Click card -> right slide-over:
- Header: name, title @ company, email, LinkedIn link (from lead jsonb)
- State badge + AI confidence %
- AI reasoning text
- Full conversation_thread rendered as timestamped chat (prospect vs sender,
  attachments/voice noted)
- Close: X, click-outside, Esc

Backed by `GET /api/leads/<conversation_id>`.

## Endpoints

| Endpoint | Returns |
|---|---|
| GET /api/leads/board?start_date&end_date&sender_id | {open:[], interested:[], meeting_booked:[], counts:{}} light cards |
| GET /api/leads/<conversation_id> | full conversation row for drawer |
| (reporting strip) | reuses /api/heyreach + /api/messages/stats |

## Logo

- **Login page:** OSPRI logo (`static/images/ospri-logo.png`, gray as-is)
  centered on the white login card.
- **Header (dark):** same asset with CSS `filter: brightness(0) invert(1)` ->
  white on the dark gradient, replacing the generic purple "O" mark. Small
  product label stays beside it.

## Edge Cases

- Conversation not yet AI-evaluated -> appears in Open with a "pending AI" dot;
  auto-eval drains it.
- Empty column -> "No leads here yet" placeholder.
- Supabase not configured -> not-configured empty state (same pattern as
  Message Analysis).
- Empty thread in drawer -> show contact + "No message history captured".
- `/validate` gains no new check (reuses Supabase check). Pipedrive check stays
  but is not surfaced in UI.

## Out of Scope

- Drag-and-drop between columns (AI owns state)
- Cross-source identity matching with Pipedrive
- Editing lead data from the dashboard (read-only)
