-- Fixed DEV001, maximum 48 hours, no writes; excludes the newest 10 seconds for batch flush.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30000ms';
SET LOCAL lock_timeout = '1000ms';
SET LOCAL timezone = 'UTC';
WITH settings AS MATERIALIZED (
    SELECT now() AS observed_at, now() - interval '48 hours' AS starts,
           now() - interval '10 seconds' AS ends,
           update_interval_decisec / 10.0 AS period,
           (SELECT count(*) FROM device_points WHERE dev_number = 'DEV001') AS expected_points
    FROM devices WHERE dev_number = 'DEV001' AND deleted_at IS NULL
), rounds AS MATERIALIZED (
    SELECT h.recorded_at, count(*) AS points, count(DISTINCT h.point_id) AS unique_points,
           count(*) FILTER (WHERE h.org_value IS NULL OR h.rt_value IS NULL) AS null_values
    FROM point_data_history h CROSS JOIN settings s
    WHERE h.dev_number = 'DEV001' AND h.recorded_at >= s.starts AND h.recorded_at <= s.ends
    GROUP BY h.recorded_at
), windows AS (
    SELECT 'last_1h' AS label, greatest(starts, observed_at - interval '1 hour') AS starts, ends FROM settings
    UNION ALL SELECT 'last_24h', greatest(starts, observed_at - interval '24 hours'), ends FROM settings
    UNION ALL SELECT 'last_48h', starts, ends FROM settings
    UNION ALL SELECT 'current_release', greatest(starts, :'release_at'::timestamptz), ends FROM settings
), intervals AS MATERIALIZED (
    SELECT w.label, r.*, lag(r.recorded_at) OVER (PARTITION BY w.label ORDER BY r.recorded_at) AS previous
    FROM windows w JOIN rounds r ON r.recorded_at >= w.starts AND r.recorded_at <= w.ends
), metrics AS (
    SELECT w.label, w.starts, w.ends, count(i.recorded_at) AS rounds,
           min(i.recorded_at) AS first_sample, max(i.recorded_at) AS last_sample,
           count(i.recorded_at) FILTER (WHERE i.points = s.expected_points AND i.unique_points = s.expected_points) AS complete_rounds,
           coalesce(sum(i.null_values), 0) AS null_values,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM i.recorded_at - i.previous)) AS median_interval_seconds,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM i.recorded_at - i.previous)) AS p95_interval_seconds,
           max(extract(epoch FROM i.recorded_at - i.previous)) AS max_interval_seconds,
           count(*) FILTER (WHERE extract(epoch FROM i.recorded_at - i.previous) > s.period * 1.5) AS long_gap_count,
           coalesce(sum(greatest(0, floor(extract(epoch FROM i.recorded_at - i.previous) / s.period + 0.5) - 1)), 0) AS estimated_missing_between_samples,
           extract(epoch FROM w.ends - max(i.recorded_at)) AS trailing_age_seconds
    FROM windows w CROSS JOIN settings s LEFT JOIN intervals i ON i.label = w.label
    GROUP BY w.label, w.starts, w.ends
), hourly AS (
    SELECT date_trunc('hour', recorded_at) AS hour_utc, count(*) AS rounds,
           min(points) AS min_points, max(points) AS max_points,
           count(*) FILTER (WHERE points <> s.expected_points) AS incomplete_rounds
    FROM rounds CROSS JOIN settings s GROUP BY 1
)
SELECT json_build_object(
    'observed_at', now(), 'settings', (SELECT row_to_json(s) FROM settings s),
    'windows', (SELECT json_agg(m ORDER BY label) FROM metrics m),
    'hourly', (SELECT json_agg(h ORDER BY hour_utc) FROM hourly h),
    'longest_gaps', (SELECT json_agg(g ORDER BY seconds DESC) FROM (
        SELECT previous, recorded_at, extract(epoch FROM recorded_at - previous) AS seconds
        FROM intervals CROSS JOIN settings s WHERE label = 'last_48h'
        AND extract(epoch FROM recorded_at - previous) > s.period * 1.5
        ORDER BY seconds DESC LIMIT 100
    ) g),
    'devices', (SELECT json_agg(d ORDER BY dev_number) FROM (
        SELECT dev_number, dev_type, transport_type, modbus_addr, serial_port, read_profile,
               is_enabled, is_online, deleted_at, update_flag, update_interval_decisec,
               last_call_at, last_back_at, loss_count
        FROM devices ORDER BY dev_number LIMIT 129
    ) d),
    'point_config', (SELECT json_agg(p ORDER BY point_number) FROM (
        SELECT p.id, p.point_number, p.fun_code, p.dev_addr, p.value_type, p.r_bit,
               p.point_ratio, p.user_ratio, p.point_offset, p.user_point_offset, p.point_unit,
               r.org_value, r.rt_value, r.recorded_at
        FROM device_points p LEFT JOIN point_data_realtime r
        ON r.dev_number = p.dev_number AND r.point_id = p.id WHERE p.dev_number = 'DEV001'
    ) p),
    'limitations', json_build_array('Current point count/period may differ from historical configuration.',
        'Nearest-period missing count is an estimate between samples, not a physical request counter.',
        'Leading/trailing outages and maintenance must be assessed separately.',
        'Hourly entries cover observed samples; absent hours must not be treated as healthy.')
);
ROLLBACK;
