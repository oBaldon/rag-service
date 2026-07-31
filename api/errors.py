from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    details: Any | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)
