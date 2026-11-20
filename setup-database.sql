-- Run once as the postgres superuser:  psql -U postgres -f scripts_setup.sql
CREATE USER cip WITH PASSWORD 'cip_password';
CREATE DATABASE cip_db OWNER cip;
CREATE DATABASE cip_test OWNER cip;
\c cip_db
CREATE EXTENSION IF NOT EXISTS pgcrypto;
\c cip_test
CREATE EXTENSION IF NOT EXISTS pgcrypto;
