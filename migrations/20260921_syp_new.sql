-- SYP denomination migration: legacy bot storage -> canonical new SYP.
-- Conversion is 100 old SYP = 1 new SYP. Run once during a maintenance window.
-- The marker makes this migration idempotent and prevents double division.
BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(120) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = '20260921_syp_new') THEN
        -- User-facing/cash balances. game_balance is intentionally excluded:
        -- it is an Ichancy balance and remains in the old denomination.
        UPDATE users SET
            bot_balance = FLOOR(COALESCE(bot_balance, 0) / 100),
            bonus_balance = FLOOR(COALESCE(bonus_balance, 0) / 100),
            bonus_base_balance = FLOOR(COALESCE(bonus_base_balance, 0) / 100),
            game_bonus_amount = FLOOR(COALESCE(game_bonus_amount, 0) / 100),
            affiliate_balance = FLOOR(COALESCE(affiliate_balance, 0) / 100),
            cashback_pending_balance = FLOOR(COALESCE(cashback_pending_balance, 0) / 100),
            checkin_pending_balance = FLOOR(COALESCE(checkin_pending_balance, 0) / 100),
            total_deposits = FLOOR(COALESCE(total_deposits, 0) / 100);

        -- Cash transaction amounts become new SYP. Game balance snapshots remain old.
        UPDATE transactions SET
            amount = FLOOR(COALESCE(amount, 0) / 100),
            converted_amount_syp = CASE WHEN converted_amount_syp IS NULL THEN NULL ELSE FLOOR(converted_amount_syp / 100) END,
            original_amount = CASE WHEN LOWER(COALESCE(original_currency, '')) = 'syp' THEN FLOOR(original_amount / 100) ELSE original_amount END
        WHERE type IN ('deposit_bot', 'withdraw_bot', 'withdraw_from_game', 'deposit_to_game');

        UPDATE bot_settings SET
            agent_balance = FLOOR(COALESCE(agent_balance, 0) / 100),
            agent_balance_alert_threshold = FLOOR(COALESCE(agent_balance_alert_threshold, 0) / 100),
            game_min_deposit_syp = FLOOR(COALESCE(game_min_deposit_syp, 0) / 100),
            min_deposit_syp = FLOOR(COALESCE(min_deposit_syp, 0) / 100),
            min_withdraw_syp = FLOOR(COALESCE(min_withdraw_syp, 0) / 100),
            syp_version = 'new';

        UPDATE user_features_settings SET
            bonus_min_transfer = FLOOR(COALESCE(bonus_min_transfer, 0) / 100),
            bonus_deposit_threshold = FLOOR(COALESCE(bonus_deposit_threshold, 0) / 100),
            checkin_min_deposit = FLOOR(COALESCE(checkin_min_deposit, 0) / 100),
            checkin_completion_reward = FLOOR(COALESCE(checkin_completion_reward, 0) / 100),
            wheel_min_deposit = FLOOR(COALESCE(wheel_min_deposit, 0) / 100),
            wheel_max_reward = FLOOR(COALESCE(wheel_max_reward, 0) / 100),
            cashback_min_loss = FLOOR(COALESCE(cashback_min_loss, 0) / 100);

        INSERT INTO schema_migrations(version) VALUES ('20260921_syp_new');
    END IF;
END $$;

COMMIT;
