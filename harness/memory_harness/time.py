import time as _time


def now_ms() -> int:
    'Current wall-clock time as integer UTC milliseconds.'
    return int(_time.time() * 1000)


def monotonic_ms() -> int:
    'Monotonic clock in integer milliseconds (durations only, never stored).'
    return int(_time.monotonic() * 1000)
