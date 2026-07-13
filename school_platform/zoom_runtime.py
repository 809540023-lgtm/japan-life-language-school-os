from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from school_platform.config import SchoolPlatformSettings, load_settings


def is_zoom_meeting_url(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return "zoom.us/" in normalized or normalized.startswith("zoommtg://")


def is_zoom_delivery(location_label: str | None, meeting_url: str | None = None) -> bool:
    if is_zoom_meeting_url(meeting_url):
        return True
    if not location_label:
        return False
    normalized = location_label.strip().lower()
    return "zoom" in normalized


@dataclass(slots=True)
class SchoolPlatformZoomRuntime:
    settings: SchoolPlatformSettings | None = None

    def __post_init__(self) -> None:
        if self.settings is None:
            self.settings = load_settings()

    def status(self) -> dict[str, Any]:
        account_id_present = bool(self.settings.zoom_account_id)
        client_id_present = bool(self.settings.zoom_client_id)
        client_secret_present = bool(self.settings.zoom_client_secret)
        user_id_present = bool(self.settings.zoom_user_id)
        ready = account_id_present and client_id_present and client_secret_present and user_id_present
        return {
            "provider": "zoom",
            "ready": ready,
            "mode": "api" if ready else "manual_link",
            "account_id_present": account_id_present,
            "client_id_present": client_id_present,
            "client_secret_present": client_secret_present,
            "user_id_present": user_id_present,
            "api_base_url": self.settings.zoom_api_base_url,
            "oauth_base_url": self.settings.zoom_oauth_base_url,
            "timezone": self.settings.zoom_timezone,
            "auto_create_available": ready,
            "message": (
                "Zoom Server-to-Server OAuth 已可自動建立會議。"
                if ready
                else "尚未補齊 Zoom API 憑證；目前可使用手動貼入 Zoom meeting_url 的模式。"
            ),
        }

    def _require_ready(self) -> None:
        status = self.status()
        if not status["ready"]:
            raise RuntimeError(status["message"])

    @staticmethod
    def _request_user_agent() -> str:
        return "JapanLifeLanguageSchoolOS/1.0"

    def _access_token(self) -> str:
        self._require_ready()
        response = httpx.post(
            f"{self.settings.zoom_oauth_base_url}/oauth/token",
            params={
                "grant_type": "account_credentials",
                "account_id": self.settings.zoom_account_id,
            },
            auth=(self.settings.zoom_client_id or "", self.settings.zoom_client_secret or ""),
            headers={"User-Agent": self._request_user_agent()},
            timeout=30.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Zoom OAuth 取 token 失敗：{exc}") from exc
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Zoom OAuth 回應缺少 access_token。")
        return str(token)

    def create_meeting(
        self,
        *,
        topic: str,
        start_at: datetime,
        duration_minutes: int,
        agenda: str | None = None,
    ) -> dict[str, Any]:
        token = self._access_token()
        start_utc = start_at.astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "topic": topic[:200],
            "type": 2,
            "start_time": start_utc,
            "duration": max(int(duration_minutes), 30),
            "timezone": self.settings.zoom_timezone,
            "agenda": (agenda or topic)[:2000],
            "settings": {
                "host_video": True,
                "participant_video": True,
                "waiting_room": True,
                "join_before_host": False,
                "mute_upon_entry": True,
                "auto_recording": "none",
            },
        }
        try:
            response = httpx.post(
                f"{self.settings.zoom_api_base_url}/users/{self.settings.zoom_user_id}/meetings",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": self._request_user_agent(),
                },
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Zoom 建立會議失敗：{exc}") from exc
        data = response.json()
        join_url = data.get("join_url")
        if not join_url:
            raise RuntimeError("Zoom 建立會議成功，但回應缺少 join_url。")
        return {
            "provider": "zoom",
            "meeting_id": data.get("id"),
            "join_url": str(join_url),
            "start_url": data.get("start_url"),
            "password": data.get("password"),
            "raw": data,
        }
