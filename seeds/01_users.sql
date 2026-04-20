-- authority ∈ {'Administrators','GroupCompany','Company','User'}（ck_users_authority）
-- user_name 匹配 ^1[3-9][0-9]{9}$（手机号）或 ^[a-zA-Z][a-zA-Z0-9_]{3,29}$（用户名）
-- password_hash: bcrypt hash of 'Admin@2026!' (rounds=12), generated 2026-04-20
INSERT INTO users (user_name, password_hash, authority, control_authority, usr_group)
VALUES
  ('13800138000', '$2b$12$6zv1m80/no3wYWqTukgRB.HKofQJFQA.c0A03Bnjj3tIDZfNyB9bi', 'Administrators', 3, 'demo'),
  ('13800138001', '$2b$12$6zv1m80/no3wYWqTukgRB.HKofQJFQA.c0A03Bnjj3tIDZfNyB9bi', 'Company', 1, 'demo')
ON CONFLICT (user_name) DO NOTHING;
