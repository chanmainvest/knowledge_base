-- Add Yahoo Finance Hong Kong source for existing databases.
INSERT INTO source(code, name, url, kind) VALUES
  ('yahoohk', 'Yahoo Finance Hong Kong', 'https://hk.finance.yahoo.com/', 'newspaper')
ON CONFLICT (code) DO NOTHING;
