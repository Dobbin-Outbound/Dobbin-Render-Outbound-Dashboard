-- =====================================================================
-- HeyReach Messages Schema for Outreach Dashboard V1.1
-- Run this once in your Supabase SQL editor.
-- One table: heyreach_messages (one row per conversation thread)
-- =====================================================================

CREATE TABLE IF NOT EXISTS heyreach_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Unique key
    conversation_id TEXT UNIQUE NOT NULL,

    -- JSONB payloads (flexible, queryable with -> operator)
    campaign JSONB NOT NULL,
    sender JSONB NOT NULL,
    lead JSONB NOT NULL,

    -- Generated columns for fast filtering
    sender_id INTEGER GENERATED ALWAYS AS ((sender->>'id')::INTEGER) STORED,
    sender_full_name TEXT GENERATED ALWAYS AS (sender->>'full_name') STORED,
    lead_id TEXT GENERATED ALWAYS AS (lead->>'id') STORED,
    lead_full_name TEXT GENERATED ALWAYS AS (lead->>'full_name') STORED,
    lead_company_name TEXT GENERATED ALWAYS AS (lead->>'company_name') STORED,
    campaign_id INTEGER GENERATED ALWAYS AS ((campaign->>'id')::INTEGER) STORED,
    campaign_name TEXT GENERATED ALWAYS AS (campaign->>'name') STORED,

    -- Full conversation thread (chronologically ordered messages)
    conversation_thread JSONB NOT NULL DEFAULT '[]'::JSONB,

    -- Latest message metadata
    latest_correlation_id TEXT,
    latest_event_type TEXT,
    latest_timestamp TIMESTAMPTZ,
    is_inmail BOOLEAN DEFAULT FALSE,

    -- AI evaluation fields (populated by /api/evaluate-conversation)
    ai_evaluated BOOLEAN DEFAULT FALSE,
    ai_evaluation_timestamp TIMESTAMPTZ,
    is_open_conversation BOOLEAN,
    is_interested BOOLEAN,
    is_meeting_booked BOOLEAN,
    ai_confidence DECIMAL(3,2),
    ai_reasoning TEXT,
    ai_model_version TEXT,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_hr_msgs_conversation_id ON heyreach_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_hr_msgs_sender_id ON heyreach_messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_hr_msgs_campaign_id ON heyreach_messages(campaign_id);
CREATE INDEX IF NOT EXISTS idx_hr_msgs_latest_timestamp ON heyreach_messages(latest_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_hr_msgs_ai_evaluated ON heyreach_messages(ai_evaluated) WHERE ai_evaluated = FALSE;
CREATE INDEX IF NOT EXISTS idx_hr_msgs_is_open ON heyreach_messages(is_open_conversation) WHERE is_open_conversation = TRUE;
CREATE INDEX IF NOT EXISTS idx_hr_msgs_is_interested ON heyreach_messages(is_interested) WHERE is_interested = TRUE;
CREATE INDEX IF NOT EXISTS idx_hr_msgs_is_meeting_booked ON heyreach_messages(is_meeting_booked) WHERE is_meeting_booked = TRUE;

-- Migration for existing deployments: add the column + force re-evaluation
ALTER TABLE heyreach_messages ADD COLUMN IF NOT EXISTS is_meeting_booked BOOLEAN;
UPDATE heyreach_messages SET ai_evaluated = FALSE WHERE is_meeting_booked IS NULL;

-- Auto-update updated_at on UPDATE
CREATE OR REPLACE FUNCTION hr_msgs_update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = public;

DROP TRIGGER IF EXISTS hr_msgs_set_updated_at ON heyreach_messages;
CREATE TRIGGER hr_msgs_set_updated_at
    BEFORE UPDATE ON heyreach_messages
    FOR EACH ROW
    EXECUTE FUNCTION hr_msgs_update_updated_at();

-- Upsert function called by n8n webhook
CREATE OR REPLACE FUNCTION upsert_heyreach_conversation(
    p_conversation_id TEXT,
    p_campaign JSONB,
    p_sender JSONB,
    p_lead JSONB,
    p_correlation_id TEXT,
    p_event_type TEXT,
    p_timestamp TIMESTAMPTZ,
    p_is_inmail BOOLEAN,
    p_recent_messages JSONB
)
RETURNS UUID AS $$
DECLARE
    v_id UUID;
    v_existing_thread JSONB;
    v_new_message JSONB;
    v_updated_thread JSONB;
BEGIN
    v_new_message := jsonb_build_object(
        'correlation_id', p_correlation_id,
        'event_type', p_event_type,
        'timestamp', p_timestamp,
        'is_inmail', p_is_inmail,
        'sender', p_sender,
        'lead', p_lead,
        'campaign', p_campaign,
        'recent_messages', p_recent_messages
    );

    SELECT id, conversation_thread INTO v_id, v_existing_thread
    FROM heyreach_messages
    WHERE conversation_id = p_conversation_id
    FOR UPDATE;

    IF v_id IS NULL THEN
        INSERT INTO heyreach_messages (
            conversation_id, campaign, sender, lead,
            conversation_thread, latest_correlation_id,
            latest_event_type, latest_timestamp, is_inmail
        ) VALUES (
            p_conversation_id, p_campaign, p_sender, p_lead,
            jsonb_build_array(v_new_message), p_correlation_id,
            p_event_type, p_timestamp, p_is_inmail
        )
        RETURNING id INTO v_id;
    ELSE
        v_updated_thread := v_existing_thread || v_new_message;
        SELECT jsonb_agg(msg ORDER BY (msg->>'timestamp')::TIMESTAMPTZ)
        INTO v_updated_thread
        FROM jsonb_array_elements(v_updated_thread) msg;

        UPDATE heyreach_messages
        SET conversation_thread = v_updated_thread,
            latest_correlation_id = p_correlation_id,
            latest_event_type = p_event_type,
            latest_timestamp = p_timestamp,
            is_inmail = p_is_inmail,
            -- Fix A: refresh lead/campaign/sender on every event so tag
            -- updates (e.g. "Emailed ✅") propagate. Guarded so an empty
            -- payload can't wipe previously-good data.
            lead = CASE WHEN p_lead IS NOT NULL AND p_lead <> '{}'::jsonb
                        THEN p_lead ELSE lead END,
            campaign = CASE WHEN p_campaign IS NOT NULL AND p_campaign <> '{}'::jsonb
                        THEN p_campaign ELSE campaign END,
            sender = CASE WHEN p_sender IS NOT NULL AND p_sender <> '{}'::jsonb
                        THEN p_sender ELSE sender END,
            ai_evaluated = FALSE,
            is_open_conversation = NULL,
            is_interested = NULL,
            is_meeting_booked = NULL,
            ai_confidence = NULL,
            ai_reasoning = NULL
        WHERE id = v_id;
    END IF;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql SET search_path = public;

-- Lightweight lead/tags refresh for the n8n tag-sync poller.
-- Updates ONLY the lead column (no thread append, no AI reset) so it's
-- safe to call on a schedule. The board re-buckets via the tag on next load.
CREATE OR REPLACE FUNCTION sync_lead_tags(
    p_conversation_id TEXT,
    p_lead JSONB
)
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER;
BEGIN
    UPDATE heyreach_messages
    SET lead = CASE
                 WHEN p_lead IS NOT NULL AND p_lead <> '{}'::jsonb
                 THEN p_lead ELSE lead
               END
    WHERE conversation_id = p_conversation_id;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql SET search_path = public;

-- Disable RLS for internal tool use (server-side service role key handles auth)
ALTER TABLE heyreach_messages DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE heyreach_messages IS
'Conversation threads from HeyReach webhooks (one row per conversation_id) + AI sentiment evaluation';
