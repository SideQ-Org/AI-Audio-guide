-- Row-Level Security for the accounts/history layer (Postgres / Supabase only).
--
-- Apply AFTER `alembic upgrade head`:
--     psql "$SUPABASE_DB_URL" -f db/rls.sql
-- (SQLite — used by the offline tests — has no RLS; ownership is also enforced in the
--  repository layer, so tests cover the same authz path.)
--
-- Model: our `walks.user_id` holds the Supabase auth uid (JWT `sub` == auth.uid()),
-- seeded via repository.get_or_create_user(user_id=...). So the policies below compare
-- directly against auth.uid(). Inserts are done by the backend under the service-role
-- key, which BYPASSES RLS — end users never write history directly.

alter table walks enable row level security;
alter table walk_events enable row level security;

-- A user may read only their own walks, and delete them (right to be forgotten).
drop policy if exists walks_select_own on walks;
create policy walks_select_own on walks
  for select using (user_id = auth.uid());

drop policy if exists walks_delete_own on walks;
create policy walks_delete_own on walks
  for delete using (user_id = auth.uid());

-- Events are reachable only through a walk the user owns.
drop policy if exists walk_events_select_own on walk_events;
create policy walk_events_select_own on walk_events
  for select using (
    exists (
      select 1 from walks w
      where w.id = walk_events.walk_id and w.user_id = auth.uid()
    )
  );

drop policy if exists walk_events_delete_own on walk_events;
create policy walk_events_delete_own on walk_events
  for delete using (
    exists (
      select 1 from walks w
      where w.id = walk_events.walk_id and w.user_id = auth.uid()
    )
  );
