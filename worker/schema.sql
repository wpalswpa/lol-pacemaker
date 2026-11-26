-- AGENTS §14: anonymous opt-in match numbers. No IP, no names, date only.
CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  install_id TEXT NOT NULL,
  app_version TEXT NOT NULL,
  lang TEXT NOT NULL,
  tier TEXT NOT NULL,
  blue_win INTEGER NOT NULL CHECK (blue_win IN (0, 1)),
  snapshots_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_install_day ON games (install_id, day);

-- AGENTS §16: in-site contact. ip_hash = SHA-256(IP + day + salt), only for the hourly limit.
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  hour TEXT NOT NULL,
  ip_hash TEXT NOT NULL,
  category TEXT NOT NULL,
  lang TEXT NOT NULL,
  message TEXT NOT NULL,
  contact TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tickets_ip_hour ON tickets (ip_hash, hour);

-- AGENTS §16: chatbot hourly limit only (no messages stored).
CREATE TABLE IF NOT EXISTS chat_usage (
  ip_hash TEXT NOT NULL,
  hour TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (ip_hash, hour)
);

-- AGENTS §15: Riot account ownership verification (temporary profile-icon flow until RSO approval)
CREATE TABLE IF NOT EXISTS auth_challenges (
  challenge_id TEXT PRIMARY KEY,
  puuid TEXT NOT NULL,
  puuid_hash TEXT NOT NULL,
  riot_id TEXT NOT NULL,
  region TEXT NOT NULL,
  target_icon INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_auth_puuid_created ON auth_challenges (puuid_hash, created_at);

CREATE TABLE IF NOT EXISTS linked_accounts (
  puuid_hash TEXT PRIMARY KEY,
  first_linked_day TEXT NOT NULL,
  last_verified_day TEXT NOT NULL
);
