-- migrate:up
ALTER TABLE rooms ALTER COLUMN code DROP NOT NULL;

-- migrate:down
-- Backfill any NULL codes with a placeholder before re-adding the constraint.
-- (In practice this rollback is for dev only — production should not invoke it.)
UPDATE rooms SET code = substr(replace(id::text, '-', ''), 1, 8) WHERE code IS NULL;
ALTER TABLE rooms ALTER COLUMN code SET NOT NULL;
