from ruisheng_api.db.repositories.timeseries import pick_sample_interval


def test_pick_sample_interval_under_1d():
    assert pick_sample_interval(3600) == 1


def test_pick_sample_interval_7d():
    assert pick_sample_interval(86400 * 7) == 300


def test_pick_sample_interval_30d():
    assert pick_sample_interval(86400 * 30) == 3600


def test_pick_sample_interval_beyond_30d():
    assert pick_sample_interval(86400 * 365) == 86400
