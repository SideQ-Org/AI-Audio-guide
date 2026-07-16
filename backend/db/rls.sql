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

-- ─────────────────────────────────────────────────────────────────────────────
-- Self-improvement corpus (Block 4 §D2). The quality worker reads these under the
-- service-role key (bypasses RLS); the backend writes them under service-role too.
-- These select-own policies are defence-in-depth for any direct client access; no
-- insert/update policies (writes stay backend-only). A walk deletion cascades to its
-- narration_samples via the FK, honouring the right to be forgotten.

alter table narration_samples enable row level security;
alter table interest_signals enable row level security;

drop policy if exists narration_samples_select_own on narration_samples;
create policy narration_samples_select_own on narration_samples
  for select using (user_id = auth.uid());

drop policy if exists interest_signals_select_own on interest_signals;
create policy interest_signals_select_own on interest_signals
  for select using (user_id = auth.uid());

alter table walk_quality enable row level security;
drop policy if exists walk_quality_select_own on walk_quality;
create policy walk_quality_select_own on walk_quality
  for select using (user_id = auth.uid());

-- ─────────────────────────────────────────────────────────────────────────────
-- Community (design/COMMUNITY.md §3.3). Reads in v1 all go through the backend
-- (service role bypasses RLS); these policies are defence-in-depth for any direct
-- client access. Writes stay backend-only — no insert/update/delete policies.

alter table friendships enable row level security;
alter table activity_events enable row level security;
alter table challenges enable row level security;
alter table challenge_participants enable row level security;

-- A user sees friendships they are part of (their requests + requests to them).
drop policy if exists friendships_select_own on friendships;
create policy friendships_select_own on friendships
  for select using (requester_id = auth.uid() or addressee_id = auth.uid());

-- Feed: your own events + events of your accepted friends.
drop policy if exists activity_events_select_visible on activity_events;
create policy activity_events_select_visible on activity_events
  for select using (
    user_id = auth.uid()
    or exists (
      select 1 from friendships f
      where f.status = 'accepted'
        and ( (f.requester_id = auth.uid() and f.addressee_id = activity_events.user_id)
           or (f.addressee_id = auth.uid() and f.requester_id = activity_events.user_id) )
    )
  );

-- Challenges: global ones, ones you created, or ones you've joined.
drop policy if exists challenges_select_visible on challenges;
create policy challenges_select_visible on challenges
  for select using (
    scope = 'global'
    or creator_id = auth.uid()
    or exists (
      select 1 from challenge_participants p
      where p.challenge_id = challenges.id and p.user_id = auth.uid()
    )
  );

-- Participants: your own rows, or co-participants of a challenge you're in.
drop policy if exists challenge_participants_select_visible on challenge_participants;
create policy challenge_participants_select_visible on challenge_participants
  for select using (
    user_id = auth.uid()
    or exists (
      select 1 from challenge_participants mine
      where mine.challenge_id = challenge_participants.challenge_id
        and mine.user_id = auth.uid()
    )
  );

-- Group streaks (design/COMMUNITY.md). Reads go through the backend (service role);
-- these are defence-in-depth. Writes stay backend-only.
alter table group_streaks enable row level security;
alter table group_streak_members enable row level security;

drop policy if exists group_streaks_select_member on group_streaks;
create policy group_streaks_select_member on group_streaks
  for select using (
    creator_id = auth.uid()
    or exists (select 1 from group_streak_members m
               where m.streak_id = group_streaks.id and m.user_id = auth.uid())
  );

drop policy if exists group_streak_members_select_member on group_streak_members;
create policy group_streak_members_select_member on group_streak_members
  for select using (
    user_id = auth.uid()
    or exists (select 1 from group_streak_members mine
               where mine.streak_id = group_streak_members.streak_id and mine.user_id = auth.uid())
  );
