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
