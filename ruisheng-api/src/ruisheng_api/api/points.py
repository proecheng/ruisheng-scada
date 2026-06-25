"""Points API：嵌套在 /api/devices/{dev_number}/points/*。"""

from __future__ import annotations

import csv
import io
from typing import cast

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from ruisheng_shared.errors.codes import BizError, ErrCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import CurrentUser, check_ca, check_role
from ..core.response import ApiResponse, ok
from ..core.tenant import apply_tenant_context
from ..db.repositories import devices as devices_repo
from ..db.repositories import points as points_repo
from ..deps import get_current_user, get_session
from .schemas.points import (
    PointCreateRequest,
    PointOut,
    PointUpdateRequest,
    validate_point_contract,
)

router = APIRouter(prefix="/api/devices", tags=["points"])

CSV_FIELDS = [
    "point_name",
    "user_point_name",
    "point_number",
    "fun_code",
    "dev_addr",
    "r_bit",
    "value_type",
    "point_unit",
    "point_ratio",
    "point_offset",
    "user_ratio",
    "user_point_offset",
    "min_value",
    "max_value",
    "show",
]


async def _require_dev(session: AsyncSession, dev_number: str, user: CurrentUser) -> object:
    await apply_tenant_context(session, usr_group=user.usr_group, role=user.role)
    d = await devices_repo.get_by_dev_number(session, dev_number)
    if d is None:
        raise BizError(ErrCode.BAD_PARAM, "device not found")
    return d


async def _bump_flag(session: AsyncSession, dev_number: str) -> None:
    await session.execute(
        text("UPDATE devices SET update_flag = 1 WHERE dev_number = :d"),
        {"d": dev_number},
    )


@router.get("/{dev_number}/points", response_model=ApiResponse)
async def list_points(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    async with session.begin():
        await _require_dev(session, dev_number, user)
        rows = await points_repo.list_points(session, dev_number)
    return ok(data={"items": [PointOut.model_validate(p).model_dump() for p in rows]})


@router.post("/{dev_number}/points", response_model=ApiResponse)
async def create_point(
    dev_number: str,
    body: PointCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    async with session.begin():
        await _require_dev(session, dev_number, user)
        p = await points_repo.create_point(
            session, dev_number=dev_number, **body.model_dump(exclude_none=True)
        )
        await _bump_flag(session, dev_number)
    return ok(data=PointOut.model_validate(p).model_dump())


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _point_to_csv_row(point: PointOut) -> dict[str, str]:
    d = point.model_dump()
    return {field: _csv_value(d.get(field)) for field in CSV_FIELDS}


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


def _parse_optional_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def _parse_csv_row(row: dict[str, str | None]) -> PointCreateRequest:
    return PointCreateRequest(
        point_name=(row.get("point_name") or row.get("user_point_name") or "").strip(),
        user_point_name=(row.get("user_point_name") or "").strip() or None,
        point_number=int(row.get("point_number") or "0"),
        fun_code=int(row.get("fun_code") or "3"),
        dev_addr=int(row.get("dev_addr") or "1"),
        r_bit=_parse_optional_int(row.get("r_bit")),
        value_type=row.get("value_type") or "字",
        point_unit=(row.get("point_unit") or "").strip() or None,
        point_ratio=float(row.get("point_ratio") or "1"),
        point_offset=float(row.get("point_offset") or "0"),
        user_ratio=float(row.get("user_ratio") or "1"),
        user_point_offset=float(row.get("user_point_offset") or "0"),
        min_value=_parse_optional_float(row.get("min_value")),
        max_value=_parse_optional_float(row.get("max_value")),
        show=int(row.get("show") or "1"),
    )


@router.get("/{dev_number}/points/export")
async def export_points(
    dev_number: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    async with session.begin():
        await _require_dev(session, dev_number, user)
        rows = await points_repo.list_points(session, dev_number)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for point in rows:
        writer.writerow(_point_to_csv_row(PointOut.model_validate(point)))
    payload = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dev_number}-points.csv"'},
    )


@router.post("/{dev_number}/points/import", response_model=ApiResponse)
async def import_points(
    dev_number: str,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    raw = await file.read()
    try:
        text_payload = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text_payload))
        if reader.fieldnames is None:
            raise ValueError("missing csv header")
        requests = [_parse_csv_row(row) for row in reader]
    except Exception as exc:
        raise BizError(ErrCode.BAD_PARAM, f"invalid points csv: {exc}") from exc
    if not requests:
        raise BizError(ErrCode.BAD_PARAM, "csv has no point rows")
    async with session.begin():
        await _require_dev(session, dev_number, user)
        points = await points_repo.create_points(
            session,
            dev_number=dev_number,
            rows=[p.model_dump(exclude_none=True) for p in requests],
        )
        await _bump_flag(session, dev_number)
    return ok(
        data={
            "imported": len(points),
            "items": [PointOut.model_validate(p).model_dump() for p in points],
        }
    )


@router.put("/{dev_number}/points/{point_id}", response_model=ApiResponse)
async def update_point(
    dev_number: str,
    point_id: int,
    body: PointUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    check_ca(user, bit=0x02)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise BizError(ErrCode.BAD_PARAM, "no fields to update")
    async with session.begin():
        await _require_dev(session, dev_number, user)
        p = await points_repo.get_point(session, point_id)
        if p is None or p.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "point not found")
        try:
            validate_point_contract(
                fun_code=cast(int, updates.get("fun_code", p.fun_code)),
                value_type=str(updates.get("value_type", p.value_type)),
                r_bit=cast(int | None, updates.get("r_bit", p.r_bit)),
                min_value=cast(float | None, updates.get("min_value", p.min_value)),
                max_value=cast(float | None, updates.get("max_value", p.max_value)),
            )
        except ValueError as exc:
            raise BizError(ErrCode.BAD_PARAM, str(exc)) from exc
        await points_repo.update_point(session, p, updates)
        await _bump_flag(session, dev_number)
    return ok(data=PointOut.model_validate(p).model_dump())


@router.delete("/{dev_number}/points/{point_id}", response_model=ApiResponse)
async def delete_point(
    dev_number: str,
    point_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    check_role(user, allowed=("Company", "GroupCompany", "Administrators"))
    async with session.begin():
        await _require_dev(session, dev_number, user)
        p = await points_repo.get_point(session, point_id)
        if p is None or p.dev_number != dev_number:
            raise BizError(ErrCode.BAD_PARAM, "point not found")
        await points_repo.delete_point(session, p)
        await _bump_flag(session, dev_number)
    return ok(data={"deleted": point_id})
