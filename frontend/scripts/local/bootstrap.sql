-- Local-only bootstrap that recreates the parts of a hosted Supabase database
-- that our migrations and Supabase Auth expect. Never run this against a
-- hosted Supabase project: these roles and schemas already exist there.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'supabase_auth_admin') then
    create role supabase_auth_admin login noinherit createrole password 'local-auth-admin';
  end if;
end
$$;

create schema if not exists auth authorization supabase_auth_admin;
grant usage on schema auth to anon, authenticated, service_role;
alter role supabase_auth_admin set search_path = auth;
grant anon, authenticated, service_role to postgres;
create extension if not exists pgcrypto;
