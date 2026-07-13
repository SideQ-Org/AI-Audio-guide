-- Friendships for the profile "friends" feature.
-- The anon/publishable key can only CRUD existing tables — it cannot run DDL — so this
-- must be executed once in the Supabase SQL editor (or via the service role). After it
-- exists, the app (and seeding) can read/write friendships as the signed-in owner.
--
-- A friendship is stored as TWO rows (A→B and B→A) so each side can list their friends
-- with a simple `where user_id = auth.uid()`.

create table if not exists public.friendships (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.users(id) on delete cascade,
  friend_id  uuid not null references public.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (user_id, friend_id)
);

alter table public.friendships enable row level security;

-- Owner can see and manage only their own friendship rows.
create policy "friendships_select_own"
  on public.friendships for select
  using (auth.uid() = user_id);

create policy "friendships_write_own"
  on public.friendships for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Seed: make the two test accounts friends (both directions).
--   Kolbasenko  = ff3a7698-6042-4a25-b59d-83e81c6c7a7e
--   Sosiskin    = f54e565c-c84d-4932-a60c-595087382f33
insert into public.friendships (user_id, friend_id) values
  ('ff3a7698-6042-4a25-b59d-83e81c6c7a7e', 'f54e565c-c84d-4932-a60c-595087382f33'),
  ('f54e565c-c84d-4932-a60c-595087382f33', 'ff3a7698-6042-4a25-b59d-83e81c6c7a7e')
on conflict (user_id, friend_id) do nothing;

-- OPTIONAL: if you'd rather store the birthday as a real column instead of Supabase
-- user_metadata (the app currently uses user_metadata, no schema change needed):
--   alter table public.users add column if not exists birthday date;
