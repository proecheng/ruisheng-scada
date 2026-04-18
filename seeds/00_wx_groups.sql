INSERT INTO wx_groups (usr_group, appid, sys_title, company_name)
VALUES
  ('demo', 'wxDEMOappid', '润盛监控 Demo', '润盛集团 Demo')
ON CONFLICT (usr_group) DO NOTHING;
