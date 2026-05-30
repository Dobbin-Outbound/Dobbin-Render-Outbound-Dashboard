-- ============================================================
-- Fix A : refresh lead/campaign/sender on existing conversations
-- so HeyReach tag updates (e.g. "Emailed") propagate.
--
-- HOW TO RUN:
--   1. Open this file.
--   2. Select ALL (Ctrl+A) and Copy (Ctrl+C).
--   3. Supabase -> SQL Editor -> New query -> Paste -> Run.
--
-- Safe to run multiple times (CREATE OR REPLACE). Existing rows
-- are not modified; only the function logic changes.
-- ============================================================

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
