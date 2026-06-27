-- Add Patreon source for existing databases.
INSERT INTO source(code, name, url, kind) VALUES
  ('patreon', 'Patreon', 'https://www.patreon.com/', 'membership')
ON CONFLICT (code) DO NOTHING;
