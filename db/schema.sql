-- Bet Tracker schema
-- Auto-applied on first run by utils/db_utils.py::Database.initialize()

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    telegram_id INTEGER UNIQUE,
    display_name TEXT,
    is_public BOOLEAN DEFAULT TRUE,
    bankroll REAL DEFAULT 1000,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bets (
    bet_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id),
    sport TEXT NOT NULL,
    game TEXT NOT NULL,
    game_date DATE NOT NULL,
    player TEXT,
    prop_type TEXT,
    line REAL,
    direction TEXT,
    book TEXT NOT NULL,
    odds INTEGER NOT NULL,
    implied_prob REAL,
    your_prob REAL NOT NULL,
    edge REAL,
    stake REAL NOT NULL,
    closing_odds INTEGER,
    closing_implied REAL,
    clv REAL,
    actual_value REAL,
    result TEXT DEFAULT 'pending',
    pnl REAL,
    niche TEXT,
    injury_context TEXT,
    confidence TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weekly_summaries (
    summary_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id),
    week_start DATE,
    week_end DATE,
    total_bets INTEGER,
    wins INTEGER,
    losses INTEGER,
    pushes INTEGER,
    win_rate REAL,
    total_staked REAL,
    total_pnl REAL,
    roi REAL,
    avg_clv REAL,
    best_niche TEXT,
    worst_niche TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS achievements (
    achievement_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id),
    achievement_type TEXT,
    description TEXT,
    icon TEXT,
    earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    group_name TEXT NOT NULL,
    created_by TEXT REFERENCES users(user_id),
    telegram_chat_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT REFERENCES groups(group_id),
    user_id TEXT REFERENCES users(user_id),
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, user_id)
);

-- Indexes for common query paths
CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(user_id);
CREATE INDEX IF NOT EXISTS idx_bets_user_date ON bets(user_id, game_date);
CREATE INDEX IF NOT EXISTS idx_bets_user_result ON bets(user_id, result);
CREATE INDEX IF NOT EXISTS idx_bets_created ON bets(created_at);
CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id);
CREATE INDEX IF NOT EXISTS idx_weekly_user ON weekly_summaries(user_id, week_start);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_user_achievement
    ON achievements(user_id, achievement_type);
