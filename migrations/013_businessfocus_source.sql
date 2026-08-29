-- 013: BusinessFocus (businessfocus.io) source + first author (龔成/shing).
-- Reference copy of what docker/postgres/init.sql seeds; `kb db migrate`
-- replays init.sql, so this file is documentation/manual-apply only.

INSERT INTO source(code, name, url, kind) VALUES
  ('businessfocus', 'BusinessFocus', 'https://businessfocus.io/', 'newspaper')
ON CONFLICT (code) DO NOTHING;

INSERT INTO channel(source_id, handle, name, url)
SELECT id, 'shing', '龔成', 'https://businessfocus.io/author/shing'
FROM source WHERE code = 'businessfocus'
ON CONFLICT (source_id, handle) DO NOTHING;
