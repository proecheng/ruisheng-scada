-- v1.1 补全 NOT NULL 列：point_ratio(1.0)/point_offset(0.0)/user_ratio(1.0)/user_point_offset(0.0)/show(1)
-- v1.2 幂等：device_points 无 UniqueConstraint on (dev_number, point_number)（id-only PK，ORM L82-88
-- 仅 2 CheckConstraint + 1 Index），ON CONFLICT DO NOTHING 在无匹配 UQ/PK 时等于 no-op 静默重复插入。
-- 改用 WHERE NOT EXISTS 子查询实现业务层幂等（Plan bug #12，类 option A；option B 加 UQ + 迁移超 E3-E6 范围）。
-- CHECK: point_number ∈ [0,65535] / fun_code ∈ {1,2,3,4}
INSERT INTO device_points (
    dev_number, point_name, point_number, fun_code, dev_addr, value_type,
    point_ratio, point_offset, user_ratio, user_point_offset, show
)
SELECT
    v.dev_number, v.point_name, v.point_number, v.fun_code, v.dev_addr, v.value_type,
    v.point_ratio, v.point_offset, v.user_ratio, v.user_point_offset, v.show
FROM (VALUES
    ('60270012', 'temperature', 0, 3::smallint, 1::smallint, '字', 1.0, 0.0, 1.0, 0.0, 1::smallint),
    ('60270012', 'pressure',    1, 3::smallint, 1::smallint, '字', 1.0, 0.0, 1.0, 0.0, 1::smallint)
) AS v(dev_number, point_name, point_number, fun_code, dev_addr, value_type,
       point_ratio, point_offset, user_ratio, user_point_offset, show)
WHERE NOT EXISTS (
    SELECT 1 FROM device_points dp
    WHERE dp.dev_number = v.dev_number AND dp.point_number = v.point_number
);
