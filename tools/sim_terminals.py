#!/usr/bin/env python3
"""32台模拟终端 — Modbus RTU 从机协议测试工具

原理
----
通过 asyncio TCP 回环创建虚拟 RS485 总线：

  [主机侧]  发送 FC3 读保持寄存器请求（模拟 GW 轮询器）
       ↕  TCP loopback 127.0.0.1
  [从机侧]  32台虚拟 Modbus RTU 从机（地址 1–32），响应 FC3 请求

不需要：数据库、Redis、真实串口、DTU。

寄存器布局（每台从机 8 个保持寄存器，地址 0–7）
-----------------------------------------------
  0: 电压   (raw / 10 = V)      范围 330.0–380.0 V
  1: 电流   (raw / 10 = A)      范围 0.0–100.0 A
  2: 功率   (raw / 10 = kW)     范围 0.0–5000.0 kW
  3: 频率   (raw / 10 = Hz)     范围 49.8–50.2 Hz
  4: 温度   (raw / 10 = °C)     范围 0.0–80.0 °C
  5: 湿度   (raw / 10 = %)      范围 20.0–90.0 %
  6: 状态   (0=正常 1=告警 2=故障)
  7: 运行秒  (0–65535, 每轮 +1)

用法
----
  uv run python tools/sim_terminals.py
  uv run python tools/sim_terminals.py --slaves 32 --polls 20 --verbose
  uv run python tools/sim_terminals.py --slaves 32 --polls 100 --inject-errors 5

参数
----
  --slaves N       从机数量（默认 32，最多 247）
  --polls  N       每台从机轮询次数（默认 10）
  --regs   N       每次读取寄存器数（默认 8）
  --timeout F      单次响应超时秒数（默认 0.5）
  --inject-errors N  每 N 次请求注入一次 CRC 错误（0=不注入）
  --verbose        打印每帧寄存器值
  --no-color       不使用 ANSI 颜色
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── sys.path fallback（uv run 已注入 workspace；直接 python 时需手动加）─────────
_GW_SRC = Path(__file__).resolve().parent.parent / "ruisheng-gw" / "src"
if str(_GW_SRC) not in sys.path:
    sys.path.insert(0, str(_GW_SRC))

from ruisheng_gw.protocol.exceptions import CRCMismatchError, ProtocolError  # noqa: E402
from ruisheng_gw.protocol.framer import Framer  # noqa: E402
from ruisheng_gw.protocol.frames import (  # noqa: E402
    ReadHoldingRequest,
    decode_read_holding_response,
    encode_read_holding_request,
)
from ruisheng_gw.protocol.modbus_codec import append_crc_to_frame, verify_crc16  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 寄存器元数据
# ─────────────────────────────────────────────────────────────────────────────

# (名称, 原始最小值, 原始最大值, 单位, 缩放因子)
# 工程值 = raw / scale
REG_META: list[tuple[str, int, int, str, int]] = [
    ("电压", 3300, 3800, "V", 10),
    ("电流", 0, 1000, "A", 10),
    ("功率", 0, 50000, "kW", 10),
    ("频率", 498, 502, "Hz", 10),
    ("温度", 0, 800, "°C", 10),
    ("湿度", 200, 900, "%", 10),
    ("状态", 0, 2, "", 1),
    ("运行秒", 0, 65535, "s", 1),
]

N_REGS_DEFAULT = len(REG_META)  # 8

# Modbus RTU 标准：单总线从机地址 1–247（0=广播，248–255=保留）
MAX_ADDR_PER_BUS = 247
MAX_REGS_PER_REQUEST = 125  # Modbus FC3 单次最多读 125 个寄存器

# FC3 固定请求长度: slave(1)+FC(1)+start(2)+count(2)+CRC(2)
FC3_REQUEST_LEN = 8
FC_HEARTBEAT_LEN = 4
FC_READ_HOLDING = 0x03
FC_HEARTBEAT = 0x19

# 寄存器索引
REG_IDX_STATUS = 6
REG_IDX_RUNTIME = 7

# 告警概率（每次 tick 有 5% 概率触发状态告警）
ALARM_PROB = 0.05

# 成功率阈值（用于颜色区分）
RATE_GREEN = 95.0
RATE_YELLOW = 80.0

# ANSI 颜色
_RESET = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"

_use_color = True


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _use_color else text


# ─────────────────────────────────────────────────────────────────────────────
# 从机侧
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SimSlave:
    """单台 Modbus RTU 从机（保持寄存器 + FC3 响应）.

    addr  — Modbus 本地地址（1–247），写入响应帧 byte[0]
    seed  — 随机数种子（多总线时传全局编号保证数据多样性；默认 = addr）
    """

    addr: int
    seed: int = -1  # -1 = 使用 addr 作为种子
    n_regs: int = N_REGS_DEFAULT
    _regs: list[int] = field(init=False, repr=False)
    _tick_count: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        rng = random.Random((self.seed if self.seed >= 0 else self.addr) * 0xDEAD)
        self._regs = [rng.randint(lo, hi) for _, lo, hi, *_ in REG_META[: self.n_regs]]
        # 确保 status=0（正常）、runtime=0
        if self.n_regs > REG_IDX_STATUS:
            self._regs[REG_IDX_STATUS] = 0
        if self.n_regs > REG_IDX_RUNTIME:
            self._regs[REG_IDX_RUNTIME] = 0

    def tick(self) -> None:
        """随机游走寄存器值（模拟传感器噪声）."""
        self._tick_count += 1
        for i, (_, lo, hi, _, _) in enumerate(REG_META[: max(self.n_regs - 2, 0)]):
            self._regs[i] = max(lo, min(hi, self._regs[i] + random.randint(-3, 3)))
        # status: ALARM_PROB 概率告警
        if self.n_regs > REG_IDX_STATUS:
            self._regs[REG_IDX_STATUS] = 1 if random.random() < ALARM_PROB else 0
        # runtime: 每 tick +1，溢出回绕
        if self.n_regs > REG_IDX_RUNTIME:
            self._regs[REG_IDX_RUNTIME] = (self._regs[REG_IDX_RUNTIME] + 1) & 0xFFFF

    def fc3_response(self, start_addr: int, count: int) -> bytes:
        """构造合法 FC3 读保持寄存器响应帧."""
        regs = [
            self._regs[start_addr + i] if (start_addr + i) < len(self._regs) else 0
            for i in range(count)
        ]
        byte_count = count * 2
        body = bytearray([self.addr & 0xFF, FC_READ_HOLDING, byte_count & 0xFF])
        for r in regs:
            body += bytes([(r >> 8) & 0xFF, r & 0xFF])
        return append_crc_to_frame(bytes(body))


class SlaveBus:
    """模拟 RS485 总线服务端：解析主机 FC3 请求 → 路由到正确从机 → 返回响应."""

    def __init__(
        self,
        slaves: dict[int, SimSlave],
        *,
        inject_error_every: int = 0,
    ) -> None:
        self.slaves = slaves
        self.inject_error_every = inject_error_every
        self.rx = 0  # 收到的合法请求帧数
        self.tx = 0  # 发送的响应帧数
        self.crc_err = 0  # CRC 校验错误次数
        self.unknown = 0  # 未知从机地址次数
        self._req_count = 0

    async def run(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        buf = bytearray()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(256), timeout=5.0)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                buf.extend(chunk)
                consumed = self._drain(bytes(buf), writer)
                buf = buf[consumed:]
                await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def _drain(self, buf: bytes, writer: asyncio.StreamWriter) -> int:
        """处理 buf 中所有完整的 FC3 请求，写响应；返回消耗的字节数."""
        pos = 0
        while len(buf) - pos >= FC3_REQUEST_LEN:
            fc = buf[pos + 1] if pos + 1 < len(buf) else 0
            if fc == FC_READ_HOLDING:
                frame = buf[pos : pos + FC3_REQUEST_LEN]
                try:
                    verify_crc16(frame)
                except Exception:
                    self.crc_err += 1
                    pos += 1  # 重同步：前进 1 字节
                    continue
                slave_addr = frame[0]
                start = (frame[2] << 8) | frame[3]
                count = (frame[4] << 8) | frame[5]
                self.rx += 1
                self._req_count += 1
                slave = self.slaves.get(slave_addr)
                if slave is None:
                    self.unknown += 1
                    pos += FC3_REQUEST_LEN
                    continue
                slave.tick()
                resp = slave.fc3_response(start, count)
                # 注入 CRC 错误（用于测试主机错误处理）
                if self.inject_error_every > 0 and self._req_count % self.inject_error_every == 0:
                    resp = resp[:-1] + bytes([resp[-1] ^ 0xFF])  # 损坏最后一字节
                writer.write(resp)
                self.tx += 1
                pos += FC3_REQUEST_LEN
            elif fc == FC_HEARTBEAT:
                # FC 0x19 心跳：从机不应答，吞掉 FC_HEARTBEAT_LEN 字节
                if len(buf) - pos < FC_HEARTBEAT_LEN:
                    break
                pos += FC_HEARTBEAT_LEN
            else:
                pos += 1  # 未知 FC，前进 1 字节重同步
        return pos


# ─────────────────────────────────────────────────────────────────────────────
# 主机侧
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PollResult:
    addr: int
    poll_no: int
    success: bool
    latency_ms: float = 0.0
    registers: tuple[int, ...] = ()
    error: str = ""


class GwMasterSim:
    """模拟 GW 主机轮询器：顺序轮询所有从机，记录统计结果."""

    def __init__(
        self,
        n_slaves: int,
        n_polls: int,
        n_regs: int,
        *,
        addr_offset: int = 0,
        verbose: bool = False,
        poll_timeout: float = 0.5,
    ) -> None:
        self.n_slaves = n_slaves
        self.n_polls = n_polls
        self.n_regs = n_regs
        self.addr_offset = addr_offset  # local_addr + offset = 全局从机编号（仅用于 verbose 显示）
        self.verbose = verbose
        self.poll_timeout = poll_timeout
        self.results: list[PollResult] = []

    async def run(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        resp_queue: asyncio.Queue[bytes] = asyncio.Queue()
        recv_task = asyncio.create_task(self._recv_loop(reader, resp_queue))
        try:
            for poll_no in range(self.n_polls):
                if self.verbose:
                    print(f"\n{_c(_CYAN + _BOLD, f'── 第 {poll_no + 1}/{self.n_polls} 轮 ──')}")
                for addr in range(1, self.n_slaves + 1):
                    result = await self._poll_one(addr, poll_no, writer, resp_queue)
                    self.results.append(result)
                    if self.verbose:
                        self._print_verbose(result)
        finally:
            recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await recv_task

    async def _poll_one(
        self,
        addr: int,
        poll_no: int,
        writer: asyncio.StreamWriter,
        resp_queue: asyncio.Queue[bytes],
    ) -> PollResult:
        req = ReadHoldingRequest(slave=addr, start_addr=0, register_count=self.n_regs)
        frame = encode_read_holding_request(req)
        t0 = time.monotonic()
        writer.write(frame)
        await writer.drain()

        try:
            resp_bytes = await asyncio.wait_for(resp_queue.get(), timeout=self.poll_timeout)
            latency = (time.monotonic() - t0) * 1000.0
            resp = decode_read_holding_response(resp_bytes)
            if resp.slave != addr:
                return PollResult(
                    addr=addr,
                    poll_no=poll_no,
                    success=False,
                    latency_ms=latency,
                    error=f"slave mismatch: got {resp.slave}",
                )
            return PollResult(
                addr=addr,
                poll_no=poll_no,
                success=True,
                latency_ms=latency,
                registers=resp.registers,
            )
        except TimeoutError:
            latency = (time.monotonic() - t0) * 1000.0
            return PollResult(
                addr=addr, poll_no=poll_no, success=False, latency_ms=latency, error="timeout"
            )
        except CRCMismatchError as e:
            latency = (time.monotonic() - t0) * 1000.0
            return PollResult(
                addr=addr, poll_no=poll_no, success=False, latency_ms=latency, error=f"CRC: {e}"
            )
        except ProtocolError as e:
            latency = (time.monotonic() - t0) * 1000.0
            return PollResult(
                addr=addr,
                poll_no=poll_no,
                success=False,
                latency_ms=latency,
                error=f"protocol: {e}",
            )

    async def _recv_loop(
        self,
        reader: asyncio.StreamReader,
        queue: asyncio.Queue[bytes],
    ) -> None:
        framer = Framer()
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                except TimeoutError:
                    continue
                if not data:
                    break
                framer.feed(data, now_ms=int(time.monotonic() * 1000))
                for frame_bytes in framer.pop_frames():
                    await queue.put(frame_bytes)
        except asyncio.CancelledError:
            pass

    def _print_verbose(self, r: PollResult) -> None:
        disp = r.addr + self.addr_offset  # 全局编号
        if not r.success:
            print(f"  {_c(_RED, f'从机 {disp:3d}: 错误 — {r.error} ({r.latency_ms:.1f}ms)')}")
            return
        parts = []
        for i, val in enumerate(r.registers):
            if i < len(REG_META):
                name, _, _, unit, scale = REG_META[i]
                eng = val / scale
                parts.append(f"{name}={eng:.1f}{unit}")
            else:
                parts.append(f"r{i}={val}")
        color = (
            _YELLOW
            if (r.registers[REG_IDX_STATUS] != 0 if len(r.registers) > REG_IDX_STATUS else False)
            else _GREEN
        )
        print(f"  {_c(color, f'从机 {disp:3d}: {chr(32).join(parts)} ({r.latency_ms:.1f}ms)')}")


# ─────────────────────────────────────────────────────────────────────────────
# 统计报告
# ─────────────────────────────────────────────────────────────────────────────

# 寄存器索引（用于报告摘要列）
_REG_IDX_VOLTAGE = 0
_REG_IDX_CURRENT = 1
_REG_IDX_TEMPERATURE = 4
_P95_FRAC = 0.95


def _rate_color(rate: float) -> str:
    if rate >= RATE_GREEN:
        return _GREEN
    return _YELLOW if rate >= RATE_YELLOW else _RED


def _last_val_str(rows: list[PollResult]) -> tuple[str, int]:
    """返回 (最后一次成功的寄存器摘要字符串, 是否告警)."""
    last_ok = next((r for r in reversed(rows) if r.success), None)
    if not last_ok or not last_ok.registers:
        return "", 0
    regs = last_ok.registers
    parts: list[str] = []
    if len(regs) > _REG_IDX_VOLTAGE:
        parts.append(f"{regs[_REG_IDX_VOLTAGE] / 10:.1f}V")
    if len(regs) > _REG_IDX_CURRENT:
        parts.append(f"{regs[_REG_IDX_CURRENT] / 10:.1f}A")
    if len(regs) > _REG_IDX_TEMPERATURE:
        parts.append(f"{regs[_REG_IDX_TEMPERATURE] / 10:.1f}°C")
    alarmed = 0
    if len(regs) > REG_IDX_STATUS and regs[REG_IDX_STATUS] != 0:
        parts.append(_c(_YELLOW, f"状态={regs[REG_IDX_STATUS]}"))
        alarmed = 1
    return " | ".join(parts), alarmed


def _print_device_table(
    by_addr: dict[int, list[PollResult]],
    n_slaves: int,
) -> tuple[int, int, int]:
    """打印每台从机统计行，返回 (total_ok, total_fail, alarm_count)."""
    total_ok = total_fail = alarm_count = 0
    for addr in range(1, n_slaves + 1):
        rows = by_addr.get(addr, [])
        ok = [r for r in rows if r.success]
        fail = len(rows) - len(ok)
        total_ok += len(ok)
        total_fail += fail
        rate = len(ok) / len(rows) * 100 if rows else 0.0
        lats = [r.latency_ms for r in ok]
        avg = statistics.mean(lats) if lats else 0.0
        p95 = sorted(lats)[int(len(lats) * _P95_FRAC)] if lats else 0.0
        mx = max(lats) if lats else 0.0
        val_str, alarmed = _last_val_str(rows)
        alarm_count += alarmed
        print(
            f"{addr:4d}  {len(ok):5d}  {fail:5d}  "
            f"{_c(_rate_color(rate), f'{rate:6.1f}%')}  "
            f"{avg:7.2f}ms  {p95:7.2f}ms  {mx:7.2f}ms  {val_str}"
        )
    return total_ok, total_fail, alarm_count


def _print_summary(
    results: list[PollResult],
    buses: list[SlaveBus],
    elapsed: float,
    total_ok: int,
    total_fail: int,
    alarm_count: int,
) -> None:
    total = total_ok + total_fail
    succ_rate = total_ok / total * 100 if total else 0.0
    all_lats = [r.latency_ms for r in results if r.success]
    print("─" * 78)
    print(
        _c(
            _BOLD,
            f"\n总请求: {total}  成功: {total_ok}  失败: {total_fail}  "
            f"成功率: {_c(_rate_color(succ_rate), f'{succ_rate:.1f}%')}",
        )
    )
    if all_lats:
        p50 = sorted(all_lats)[len(all_lats) // 2]
        p95 = sorted(all_lats)[int(len(all_lats) * _P95_FRAC)]
        print(
            f"延迟: 均={statistics.mean(all_lats):.2f}ms | "
            f"P50={p50:.2f}ms | P95={p95:.2f}ms | 最大={max(all_lats):.2f}ms"
        )
    throughput = total / elapsed if elapsed > 0 else 0
    print(f"吞吐: {throughput:.1f} 请求/秒 | 总耗时: {elapsed:.2f}s")
    total_rx = sum(b.rx for b in buses)
    total_tx = sum(b.tx for b in buses)
    total_crc = sum(b.crc_err for b in buses)
    total_unk = sum(b.unknown for b in buses)
    print(f"从机端: 收{total_rx}请求 发{total_tx}响应 CRC错{total_crc} 未知地址{total_unk}")
    if alarm_count > 0:
        print(_c(_YELLOW, f"告警次数（最后一轮）: {alarm_count} 台状态异常"))


def print_report(
    results: list[PollResult],
    buses: list[SlaveBus],
    elapsed: float,
    n_slaves: int,
) -> None:
    sep = "═" * 78
    n_buses = len(buses)
    print(f"\n{_c(_BOLD, sep)}")
    print(
        _c(
            _BOLD + _CYAN,
            f"  {n_slaves}台模拟终端测试报告（{n_buses}条总线）— Modbus RTU 协议层验证",
        )
    )
    print(_c(_BOLD, sep))
    by_addr: dict[int, list[PollResult]] = {}
    for r in results:
        by_addr.setdefault(r.addr, []).append(r)
    header = (
        f"{'从机':>4}  {'成功':>5}  {'失败':>5}  {'成功率':>7}  "
        f"{'均延迟':>8}  {'P95':>8}  {'最大':>8}  最后一次值"
    )
    print(f"\n{_c(_BOLD, header)}")
    print("─" * 78)
    total_ok, total_fail, alarm_count = _print_device_table(by_addr, n_slaves)
    _print_summary(results, buses, elapsed, total_ok, total_fail, alarm_count)
    print(_c(_BOLD, sep))


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


async def _run_one_bus(
    local_slaves: dict[int, SimSlave],
    n_polls: int,
    n_regs: int,
    *,
    addr_offset: int,
    verbose: bool,
    poll_timeout: float,
    inject_error_every: int,
) -> tuple[list[PollResult], SlaveBus]:
    """运行一条虚拟 RS485 总线：启动从机 TCP 服务器，连接主机，轮询，返回结果。"""
    bus = SlaveBus(local_slaves, inject_error_every=inject_error_every)
    slave_connected = asyncio.Event()

    async def _accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        slave_connected.set()
        await bus.run(reader, writer)

    server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    master_reader, master_writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.wait_for(slave_connected.wait(), timeout=2.0)

    master = GwMasterSim(
        len(local_slaves),
        n_polls,
        n_regs,
        addr_offset=addr_offset,
        verbose=verbose,
        poll_timeout=poll_timeout,
    )
    await master.run(master_reader, master_writer)

    master_writer.close()
    with contextlib.suppress(Exception):
        await master_writer.wait_closed()
    server.close()
    with contextlib.suppress(Exception):
        await server.wait_closed()

    return master.results, bus


async def run_simulation(
    n_slaves: int,
    n_polls: int,
    n_regs: int,
    *,
    verbose: bool,
    poll_timeout: float,
    inject_error_every: int,
) -> None:
    # 自动分配总线：超过 MAX_ADDR_PER_BUS 时启用多总线
    n_buses = math.ceil(n_slaves / MAX_ADDR_PER_BUS)
    bus_size = math.ceil(n_slaves / n_buses)  # 均分，最后一条可能少一些

    print(_c(_BOLD, "模拟终端测试工具"))
    print(f"  从机总数:    {n_slaves} 台")
    print(f"  总线数量:    {n_buses} 条（每条 ≤{bus_size} 台，地址 1–{bus_size}）")
    print(f"  轮询次数:    {n_polls} 次/台")
    print(f"  每次读寄存器: {n_regs} 个（地址 0–{n_regs - 1}）")
    print(f"  超时设置:    {poll_timeout:.1f}s")
    if inject_error_every > 0:
        print(_c(_YELLOW, f"  注入错误:    每 {inject_error_every} 次请求损坏一帧 CRC"))
    print()

    all_results: list[PollResult] = []
    all_buses: list[SlaveBus] = []
    t_start = time.monotonic()

    for bus_idx in range(n_buses):
        g_start = bus_idx * bus_size + 1  # 全局起始编号
        g_end = min(g_start + bus_size - 1, n_slaves)  # 全局结束编号
        local_count = g_end - g_start + 1
        addr_offset = g_start - 1  # local_addr + offset = 全局编号

        label = f"[总线 {bus_idx + 1}/{n_buses}]"
        range_str = f"从机 {g_start:3d}–{g_end:3d}  本地地址 1–{local_count}"
        print(f"{_c(_CYAN, label)} {range_str} ... ", end="", flush=True)

        local_slaves = {
            local_addr: SimSlave(
                addr=local_addr,  # Modbus 本地地址
                seed=g_start + local_addr - 1,  # 全局编号作随机种子
                n_regs=n_regs,
            )
            for local_addr in range(1, local_count + 1)
        }
        if verbose:
            print()  # 换行，后续 verbose 输出缩进显示

        results, bus = await _run_one_bus(
            local_slaves,
            n_polls,
            n_regs,
            addr_offset=addr_offset,
            verbose=verbose,
            poll_timeout=poll_timeout,
            inject_error_every=inject_error_every,
        )

        # 将本地地址映射回全局编号
        for r in results:
            r.addr = r.addr + addr_offset

        ok_cnt = sum(1 for r in results if r.success)
        total_cnt = len(results)
        rate = ok_cnt / total_cnt * 100 if total_cnt else 0.0
        status = _c(_rate_color(rate), f"成功 {ok_cnt}/{total_cnt} ({rate:.0f}%)")
        print(status)

        all_results.extend(results)
        all_buses.append(bus)

    elapsed = time.monotonic() - t_start
    print()
    print_report(all_results, all_buses, elapsed, n_slaves)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="模拟终端 — Modbus RTU 从机协议测试（自动多总线，无上限）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--slaves", type=int, default=32, help="从机数量（默认 32；>247 自动启用多总线）"
    )
    parser.add_argument("--polls", type=int, default=10, help="每台轮询次数（默认 10）")
    parser.add_argument(
        "--regs", type=int, default=N_REGS_DEFAULT, help=f"每次读寄存器数（默认 {N_REGS_DEFAULT}）"
    )
    parser.add_argument("--timeout", type=float, default=0.5, help="单次响应超时秒数（默认 0.5）")
    parser.add_argument(
        "--inject-errors",
        type=int,
        default=0,
        metavar="N",
        help="每 N 次请求注入一次 CRC 错误（0=不注入）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印每帧寄存器值")
    parser.add_argument("--no-color", action="store_true", help="不使用 ANSI 颜色")
    args = parser.parse_args()

    global _use_color  # noqa: PLW0603
    if args.no_color or not sys.stdout.isatty():
        _use_color = False

    if args.slaves < 1:
        parser.error("--slaves 至少为 1")
    if args.polls < 1:
        parser.error("--polls 至少为 1")
    if not 1 <= args.regs <= MAX_REGS_PER_REQUEST:
        parser.error(f"--regs 范围: 1–{MAX_REGS_PER_REQUEST}")

    asyncio.run(
        run_simulation(
            args.slaves,
            args.polls,
            args.regs,
            verbose=args.verbose,
            poll_timeout=args.timeout,
            inject_error_every=args.inject_errors,
        )
    )


if __name__ == "__main__":
    main()
