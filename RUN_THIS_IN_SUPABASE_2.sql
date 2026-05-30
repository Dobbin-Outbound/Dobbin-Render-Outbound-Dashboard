-- ============================================================
-- sync_lead_tags : lightweight lead/tags refresh for the
-- n8n tag-poller. Updates ONLY the lead column for an existing
-- conversation -- no thread append, no AI reset. Safe to call
-- repeatedly on a schedule.
--
-- HOW TO RUN:
--   1. Open this file, Select All (Ctrl+A), Copy (Ctrl+C).
--   2. Supabase -> SQL Editor -> New query -> Paste -> Run.
--   Safe to re-run (CREATE OR REPLACE).
-- ============================================================

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
    RETURN v_count;  -- rows updated (0 if conversation not yet stored)
END;
$$ LANGUAGE plpgsql SET search_path = public;
