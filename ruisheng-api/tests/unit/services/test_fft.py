import math

from ruisheng_api.services.analytics.fft import compute_fft


def test_compute_fft_empty():
    r = compute_fft([], sample_rate=1000.0)
    assert r == {"freqs": [], "magnitudes": []}


def test_compute_fft_single_tone():
    # 100Hz tone at 1000Hz sample rate, 1024 samples
    n = 1024
    sr = 1000.0
    freq = 100.0
    samples = [math.sin(2 * math.pi * freq * i / sr) for i in range(n)]
    r = compute_fft(samples, sample_rate=sr)
    # The dominant frequency should be near 100Hz
    freqs = r["freqs"]
    mags = r["magnitudes"]
    peak_idx = mags.index(max(mags))
    assert abs(freqs[peak_idx] - freq) < 2.0  # within 2Hz of 100Hz
