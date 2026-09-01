-- =============================================================================
-- MySQL Initialization Script
-- Game Boosting Platform
-- This script runs automatically when the container is first created
-- =============================================================================

-- Create database if not exists (redundant but safe)
CREATE DATABASE IF NOT EXISTS game_boosting
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- Use the database
USE game_boosting;

-- Grant permissions
GRANT ALL PRIVILEGES ON game_boosting.* TO 'boosting_user'@'%';
FLUSH PRIVILEGES;

-- Log successful initialization
SELECT 'Database initialized successfully!' AS message;
