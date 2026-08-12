-- Run in the Supabase SQL editor once.
-- Existing rows remain compatible: source defaults to consensus and
-- settlement fields remain NULL until a leg is settled/notified.
ALTER TABLE public.user_bets
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'consensus';

ALTER TABLE public.user_bets
    ADD COLUMN IF NOT EXISTS final_score text;

ALTER TABLE public.user_bets
    ADD COLUMN IF NOT EXISTS settlement_notified_at timestamptz;

CREATE INDEX IF NOT EXISTS user_bets_slip_id_idx
    ON public.user_bets (slip_id);
