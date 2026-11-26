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
