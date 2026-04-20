"""Waveforms API：/api/waveforms/。

表：waveform_history（spec §4.2）
  dev_number, point_id, data_array BYTEA, tz_data_array BYTEA,
  sample_time_decisec SMALLINT, packet_count SMALLINT, recorded_at TIMESTAMPTZ

sample_time_decisec 为每包采样时长（单位：1/10 秒）。
sample_rate (Hz) = packet_count / (sample_time_decisec * 0.1)。
data_array 以小端 IEEE-754 float32 序列存储（每 4 字节一个采样值）。
"""

from __future__ import annotations

import struct
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..deps import get_current_user, get_session
from ..services.analytics.fft import compute_fft

router = APIRouter(prefix="/api/waveforms", tags=["waveforms"])


def _decode_data_array(data: bytes) -> list[float]:
    """将 BYTEA data_array（小端 float32 序列）解码为浮点列表。"""
    if not data:
        return []
    count = len(data) // 4
    return list(struct.unpack_from(f"<{count}f", data, 0))


@router.get("/{dev_number}/{point_id}", response_model=ApiResponse)
async def get_waveform_history(
    dev_number: str,
    point_id: int,
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """查询波形历史记录（最近 100 条）。"""
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        sql = text("""
            SELECT dev_number, point_id, sample_time_decisec, packet_count, recorded_at
            FROM waveform_history
            WHERE dev_number = :d AND point_id = :p
              AND recorded_at >= :f AND recorded_at < :t
            ORDER BY recorded_at DESC
            LIMIT 100
        """)
        result = await session.execute(
            sql,
            {
                "d": dev_number,
                "p": point_id,
                "f": from_ts,
                "t": to_ts,
            },
        )
        rows = [dict(r._mapping) for r in result]
    return ok(data={"dev_number": dev_number, "point_id": point_id, "waveforms": rows})


@router.post("/analyze", response_model=ApiResponse)
async def analyze_waveform(
    dev_number: str = Query(...),
    point_id: int = Query(...),
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """对最新一条波形记录执行 FFT 分析，返回频率/幅值数组。"""
    async with session.begin():
        await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
        sql = text("""
            SELECT data_array, sample_time_decisec, packet_count
            FROM waveform_history
            WHERE dev_number = :d AND point_id = :p
              AND recorded_at >= :f AND recorded_at < :t
            ORDER BY recorded_at DESC
            LIMIT 1
        """)
        result = await session.execute(
            sql,
            {
                "d": dev_number,
                "p": point_id,
                "f": from_ts,
                "t": to_ts,
            },
        )
        row = result.one_or_none()
    if row is None:
        return ok(data={"freqs": [], "magnitudes": []})
    # sample_time_decisec: 每包时长（单位 1/10 s）；packet_count: 采样点数
    sample_time_sec = float(row.sample_time_decisec) * 0.1
    packet_count = int(row.packet_count)
    if sample_time_sec > 0 and packet_count > 0:
        sample_rate = packet_count / sample_time_sec
    else:
        sample_rate = 1000.0  # 默认 1 kHz
    samples = _decode_data_array(bytes(row.data_array))
    return ok(data=compute_fft(samples, sample_rate=sample_rate))
