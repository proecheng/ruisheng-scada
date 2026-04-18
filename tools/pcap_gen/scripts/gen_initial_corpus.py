"""首批 15 个 pcap：5 种设备类型 × 3 种工况。

Plan bug #15 fix (v1.2): 原脚本用中文 DEVICE_TYPES 做 ASCII encode errors="ignore"，
5 种中文类型全部被剥成空，f"DEMO-{dtype}-{j}" 变成 "DEMO--0/1/2"，
`or fallback` 不触发（"DEMO--0" truthy），5 种 type 全坍缩为 3 个唯一文件名，
产物 6 文件（3 pcap + 3 expected），不是期望的 30。修复：直接用 ASCII TYPE{i} 编码。

Plan bug #20 fix (v1.6): 原方案 subprocess.check_call(["uv", "run", "pcap-gen", ...])
走 console script，在 Windows CJK 路径 D:\\江苏润盛\\... 下会被 uv editable .pth mbcs
解码问题炸（与 F2 pytest 绕路的同根问题；F4 typer CLI 走 pip entry-point 也是基于
site-packages，无 pytest pythonpath 兜底）。改为直接 from pcap_gen.scenarios import
gen_normal_session，调用 Python API；CJK 路径下 gen_normal_session 本身 OK（scapy
wrpcap/rdpcap CJK 路径实测 roundtrip 通过），只需在 entry 处把 tools/pcap_gen/src 加到
sys.path 即可绕过 .pth 问题（与 root pyproject.toml [tool.pytest.ini_options] pythonpath
同策略）。F4 的 CLI 仍然保留（Linux/ASCII 路径下可用，也给 Plan 1 之后的交互式测试用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# CJK 路径绕路：把 tools/pcap_gen/src 与 ruisheng-shared/src 加 sys.path
# （同 root pyproject.toml [tool.pytest.ini_options] pythonpath 策略；.pth mbcs 问题下
# uv editable 的两个 pkg 都不可信，所以两个都要手动 prepend）
_REPO_ROOT = Path(__file__).resolve().parents[3]  # scripts/ → pcap_gen/ → tools/ → repo
for _src in (
    _REPO_ROOT / "tools" / "pcap_gen" / "src",
    _REPO_ROOT / "ruisheng-shared" / "src",
):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from pcap_gen.scenarios import gen_normal_session  # noqa: E402

# 5 种设备类型（注释仅说明，实际 dev_ser 走 TYPE{i} ASCII）
# TYPE0=采油机 / TYPE1=保温 / TYPE2=电气 / TYPE3=液位 / TYPE4=温湿度
DEVICE_TYPE_COUNT = 5
SEEDS = [100, 200, 300]  # 3 种工况
FRAMES_PER_PCAP = 100
SLAVE_DEFAULT = 1


def main() -> None:
    out_dir = _REPO_ROOT / "corpus" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(DEVICE_TYPE_COUNT):
        for j, seed in enumerate(SEEDS):
            dev_ser = f"DEMO-TYPE{i}-{j}"
            name = f"normal_{dev_ser}_{FRAMES_PER_PCAP}_seed{seed}"
            gen_normal_session(
                dev_ser=dev_ser,
                slave=SLAVE_DEFAULT,
                frames_count=FRAMES_PER_PCAP,
                out_pcap=out_dir / f"{name}.pcap",
                out_expected=out_dir / f"{name}.expected.json",
                seed=seed,
            )
            print(f"wrote: {name}.pcap and .expected.json")


if __name__ == "__main__":
    main()
