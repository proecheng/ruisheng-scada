"""业务校验器。"""

from .rs485 import min_poll_interval_decisec, validate_bus_feasibility

__all__ = ["min_poll_interval_decisec", "validate_bus_feasibility"]
