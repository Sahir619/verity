"""Mirror of the request types in frontend/src/lib/contracts/view-types.ts that cross the wire."""

from typing import Annotated, Any, Literal, Union

from pydantic import Field, TypeAdapter

from .base import WireModel


class PinSteer(WireModel):
    kind: Literal["pin"] = "pin"
    block_id: str
    pin: str


class ToggleSteer(WireModel):
    kind: Literal["toggle"] = "toggle"
    block_id: str
    variant_id: str
    value: str


class GateSteer(WireModel):
    kind: Literal["gate"] = "gate"
    action: Literal["build_now", "hold", "resume"]


class AutopilotSteer(WireModel):
    kind: Literal["autopilot"] = "autopilot"
    on: bool


class WinnerOverrideSteer(WireModel):
    kind: Literal["winner_override"] = "winner_override"
    candidate_id: str


SteerRequest = Annotated[
    Union[PinSteer, ToggleSteer, GateSteer, AutopilotSteer, WinnerOverrideSteer],
    Field(discriminator="kind"),
]

STEER_ADAPTER: TypeAdapter[Any] = TypeAdapter(SteerRequest)


def parse_steer(data: Any) -> Any:
    return STEER_ADAPTER.validate_python(data)
