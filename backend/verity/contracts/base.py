"""Wire base model: snake_case in Python, camelCase on the wire, TS optional vs nullable kept exact.

Rules (match frontend/src/lib/contracts/schemas.ts):
- A TS field written `x?: T` is declared with `opt()` here. When it is None it is OMITTED from the JSON
  (zod `.optional()` rejects null).
- A TS field written `x: T | null` is declared `x: T | None` WITHOUT `opt()`. None is sent as null.
- Always serialize with `to_wire()` / `to_json()` (by_alias=True). Never call model_dump() without by_alias.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializationInfo, SerializerFunctionWrapHandler, model_serializer

OMIT_IF_NONE = "verity_omit_if_none"


def camel(name: str) -> str:
    """snake_case -> camelCase. A trailing underscore (keyword escape, e.g. `pass_`) is dropped."""
    head, *rest = name.rstrip("_").split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def opt(**kwargs: Any) -> Any:
    """Declare a TS-optional field (`x?: T`): default None, omitted from the wire when None."""
    return Field(default=None, json_schema_extra={OMIT_IF_NONE: True}, **kwargs)


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=camel, populate_by_name=True, extra="ignore")

    @model_serializer(mode="wrap")
    def _omit_optional_none(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo) -> Any:
        data = handler(self)
        if not isinstance(data, dict):
            return data
        for name, field in type(self).model_fields.items():
            extra = field.json_schema_extra
            if isinstance(extra, dict) and extra.get(OMIT_IF_NONE) and getattr(self, name, None) is None:
                data.pop(field.alias or name, None)
                data.pop(name, None)
        return data


def to_wire(model: BaseModel) -> dict[str, Any]:
    """The JSON-ready dict the web zod schemas accept."""
    return model.model_dump(mode="json", by_alias=True)


def to_json(model: BaseModel) -> str:
    return model.model_dump_json(by_alias=True)
