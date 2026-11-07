-- Fresh dedicated database only. Run as the cloud DB administrator using psql.
-- Passwords come from the caller's environment, not command-line arguments.
\set ON_ERROR_STOP on
\getenv migrator_password MIGRATOR_DB_PASSWORD
\getenv java_password JAVA_DB_PASSWORD
\getenv python_password PYTHON_DB_PASSWORD
\getenv monitor_password MONITOR_DB_PASSWORD
BEGIN;
-- Existing roles or application tables abort the transaction; upgrades use the migration job.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public') THEN
    RAISE EXCEPTION 'Fresh dedicated database required';
  END IF;
END $$;
CREATE TEMP TABLE service_passwords(value text NOT NULL);
INSERT INTO service_passwords VALUES (:'migrator_password'), (:'java_password'), (:'python_password'), (:'monitor_password');
DO $$ BEGIN
  IF (SELECT min(length(value)) < 16 OR count(DISTINCT value) <> 4 FROM service_passwords) THEN
    RAISE EXCEPTION 'Four distinct passwords of at least 16 characters required';
  END IF;
END $$;
CREATE ROLE dr_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'migrator_password';
CREATE ROLE dr_java LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'java_password';
CREATE ROLE dr_python LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'python_password';
CREATE ROLE dr_monitor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'monitor_password';
-- Managed providers generally do not expose PostgreSQL SUPERUSER. Schema ownership suffices.
GRANT dr_migrator TO CURRENT_USER;
ALTER SCHEMA public OWNER TO dr_migrator;
REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"DBNAME" TO dr_migrator;
GRANT CONNECT ON DATABASE :"DBNAME" TO dr_java, dr_python, dr_monitor;
GRANT USAGE ON SCHEMA public TO dr_java, dr_python, dr_monitor;
DROP TABLE service_passwords;
COMMIT;
