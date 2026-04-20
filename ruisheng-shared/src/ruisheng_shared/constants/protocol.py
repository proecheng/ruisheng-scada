"""ModBus / TCP / RS485 协议常量。对应 spec §A + §B + §D.1。"""

from __future__ import annotations

# CRC16 ModBus 标准（§A.3）
CRC16_POLYNOMIAL: int = 0xA001  # 反向多项式
CRC16_INIT: int = 0xFFFF

# TCP 端口分工（§A.7）
DEVICE_REGISTER_TCP_PORT: int = 6000  # FC 21/22/20/100/0x19
DEVICE_TELEMETRY_TCP_PORT: int = 6020  # FC 3/5/6/16

# 帧界（§A.2.1）
FRAME_MAX_LENGTH: int = 4096
FRAME_SILENCE_MS: int = 200

# 心跳（§A.5 + §D.1）
HEARTBEAT_INTERVAL_S: int = 30
HEARTBEAT_TIMEOUT_MULTIPLE: int = 3  # 连续 3 次无响应 → LossCnt++

# 离线 / 清除阈值（§D.1）
OFFLINE_THRESHOLD_MIN: int = 15
PURGE_AFTER_REGISTER_S: int = 120

# 轮询（§D.1 / D6）
POLL_INTERVAL_MIN_DECISEC: int = 10
POLL_INTERVAL_MAX_DECISEC: int = 1000
DEFAULT_POLL_INTERVAL_DECISEC: int = 100  # 10 秒

# ModBus 地址范围
MODBUS_BROADCAST_ADDR: int = 0  # 新系统拒收（§3.8.13）
MODBUS_MIN_SLAVE_ADDR: int = 1
MODBUS_MAX_SLAVE_ADDR: int = 247

# 控制命令 TTL（§3.2 + §3.8.5）
OFFLINE_COMMAND_TTL_S: int = 600  # 10 min
COMMAND_ACK_TIMEOUT_S: int = 5
COMMAND_RETRY_MAX: int = 3
