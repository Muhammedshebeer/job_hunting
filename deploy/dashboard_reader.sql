-- TEMPLATE ONLY. Run with psql as the hosted database owner AFTER approval.
-- The owner role is the role that restores the dump and creates future tables.
\set ON_ERROR_STOP on
\prompt 'Hosted database owner role: ' owner_role
CREATE ROLE dashboard_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\password dashboard_reader
GRANT CONNECT ON DATABASE :"DBNAME" TO dashboard_reader;
GRANT USAGE ON SCHEMA public TO dashboard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT SELECT ON TABLES TO dashboard_reader;
ALTER ROLE dashboard_reader SET default_transaction_read_only = on;
-- No INSERT/UPDATE/DELETE, sequence, ownership or schema-creation grants.
-- Do not grant this role membership in an owner/writer role.
