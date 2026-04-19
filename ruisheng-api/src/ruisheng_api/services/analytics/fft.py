"""FFT 分析服务（numpy 实现）。"""

from __future__ import annotations

import numpy as np


def compute_fft(samples: list[float], *, sample_rate: float) -> dict[str, list[float]]:
    """
    计算 FFT。
    Returns: {freqs: [...], magnitudes: [...]}
    Only positive frequencies (DC + one-sided).
    """
    n = len(samples)
    if n == 0:
        return {"freqs": [], "magnitudes": []}
    arr = np.array(samples, dtype=np.float64)
    fft_vals = np.fft.rfft(arr)
    magnitudes = (np.abs(fft_vals) / n * 2).tolist()
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate).tolist()
    return {"freqs": freqs, "magnitudes": magnitudes}
