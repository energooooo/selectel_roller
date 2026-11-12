from __future__ import annotations

from dataclasses import dataclass


def _string_field(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


@dataclass
class FloatingIPRecord:
    id: str
    address: str
    region: str
    project_id: str = ""
    status: str = ""
    fixed_ip_address: str = ""
    port_id: str = ""
    source: str = "api"

    @classmethod
    def from_payload(cls, payload: dict[str, object], source: str = "api") -> "FloatingIPRecord":
        return cls(
            id=_string_field(payload.get("id")),
            address=_string_field(payload.get("floating_ip_address")),
            region=_string_field(payload.get("region")),
            project_id=_string_field(payload.get("project_id")),
            status=_string_field(payload.get("status")),
            fixed_ip_address=_string_field(payload.get("fixed_ip_address")),
            port_id=_string_field(payload.get("port_id")),
            source=source,
        )

    def resource_ref(self) -> str:
        short_id = self.id[:8] if self.id else "unknown"
        if self.address:
            return f"{self.address} (id={short_id})"
        return f"id={short_id}"
