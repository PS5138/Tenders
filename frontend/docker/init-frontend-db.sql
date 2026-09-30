-- Local development only. Keep identity data in a separate database.
create database ten_frontend;
\connect ten_frontend
\i /opt/ten-bootstrap.sql
