-- Database initialization script for Docker PostgreSQL
-- This script ensures the database and user are properly configured

-- Create database if it doesn't exist (this is usually handled by POSTGRES_DB env var)
-- But we can add any additional setup here

-- Enable extensions if needed
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Add any other initialization SQL here
-- For example, creating additional schemas, users, or initial data

-- Note: The main database schema will be created by the application
-- using SQLAlchemy's create_all() or Alembic migrations
