-- v1.1 补全 NOT NULL 列：update_interval_decisec(100)/loss_count(0)/is_online(false)/update_flag(0)
-- 原生 SQL INSERT 路径不走 ORM Python default，必须显式给值（D9 Plan bug #8 / E5 Plan bug #11 教训）
-- CHECK: modbus_addr ∈ [1,247] / baud_rate ∈ {9600,19200,38400,57600,115200} / update_interval_decisec ∈ [10,1000]
INSERT INTO devices (
    dev_number, dev_ser_number, modbus_addr, baud_rate,
    usr_group, administrators,
    update_interval_decisec, loss_count, is_online, update_flag
)
VALUES
  ('60270012', 'DEMO-SN-0001', 1, 9600, 'demo', '13800138000', 100, 0, FALSE, 0)
ON CONFLICT (dev_number) DO NOTHING;
