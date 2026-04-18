-- authority ∈ {'Administrators','GroupCompany','Company','User'}（ck_users_authority）
-- user_name 匹配 ^1[3-9][0-9]{9}$（手机号）或 ^[a-zA-Z][a-zA-Z0-9_]{3,29}$（用户名）
-- password_hash 当前仅 dev stub；生产由后端 bcrypt 计算
INSERT INTO users (user_name, password_hash, authority, control_authority, usr_group)
VALUES
  ('13800138000', '$2b$12$PLACEHOLDER_BCRYPT_HASH', 'Administrators', 3, 'demo'),
  ('13800138001', '$2b$12$PLACEHOLDER_BCRYPT_HASH', 'Company', 1, 'demo')
ON CONFLICT (user_name) DO NOTHING;
