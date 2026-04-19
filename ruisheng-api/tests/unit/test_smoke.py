def test_package_importable() -> None:
    import ruisheng_api

    assert ruisheng_api.__version__ == "0.1.0"
