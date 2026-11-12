from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class LoginRequest(BaseModel):
    login: str
    password: Optional[str] = None
    pin: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    login: str
    rola: str


class DeviceResponse(BaseModel):
    id: str
    nazwa: str
    typ: str
    online: bool
    mqtt_topic: Optional[str] = None
    ieee_address: Optional[str] = None
    last_state: Dict[str, Any] = {}
    updated_at: Optional[str] = None


class DeviceCommandRequest(BaseModel):
    payload: Optional[Dict[str, Any]] = None
    state: Optional[str] = None
    state_l1: Optional[str] = None
    state_l2: Optional[str] = None
    state_l3: Optional[str] = None
    brightness: Optional[int] = Field(default=None, ge=0, le=254)
    color_temp: Optional[int] = Field(default=None, ge=150, le=500)
    color: Optional[Dict[str, Any]] = None
    effect: Optional[str] = None
    sensitivity: Optional[str] = None
    silent_mode: Optional[str] = None
    dismiss_alarm: Optional[str] = None

    def to_mqtt_payload(self) -> Dict[str, Any]:
        if self.payload:
            return self.payload
        return self.model_dump(exclude_none=True, exclude={"payload"})


class MeasurementResponse(BaseModel):
    id: Optional[int] = None
    device_id: str
    metric: str
    value: float
    unit: str
    ts: str


class StatPoint(BaseModel):
    period: str
    min: float
    max: float
    avg: float
    sum: float
    daily_consumption: Optional[float] = None
    count: int


class StatsResponse(BaseModel):
    device_id: str
    metric: str
    unit: str
    period: str
    overall_min: Optional[float] = None
    overall_max: Optional[float] = None
    overall_avg: Optional[float] = None
    overall_sum: Optional[float] = None
    series: List[StatPoint]


class EventResponse(BaseModel):
    id: int
    device_id: str
    typ: str
    payload: Dict[str, Any]
    ts: str


class UserCreateRequest(BaseModel):
    login: str = Field(..., min_length=2, max_length=64)
    pin: str = Field(..., pattern=r"^\d{4}$")
    password: Optional[str] = Field(default="iot123", min_length=4)
    rola: str = Field(default="user")
    aktywny: bool = True


class UserUpdateRequest(BaseModel):
    pin: Optional[str] = Field(default=None, pattern=r"^\d{4}$")
    rola: Optional[str] = None
    aktywny: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    login: str
    rola: str
    aktywny: bool
    created_at: str

    class Config:
        from_attributes = True


class AccessAttemptResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_login: Optional[str] = None
    device_id: str
    wynik: str
    kod_decyzji: int
    ts: str


class UserAccessReportResponse(BaseModel):
    user: UserResponse
    total_attempts: int
    granted_count: int
    denied_wrong_pin_count: int
    denied_forbidden_count: int
    last_attempt_ts: Optional[str] = None
    attempts: List[AccessAttemptResponse]


class AutomationCreateRequest(BaseModel):
    nazwa: str
    trigger: Dict[str, Any]
    warunek: Optional[Dict[str, Any]] = None
    akcja: Dict[str, Any]
    aktywna: bool = True


class AutomationEnableRequest(BaseModel):
    aktywna: bool


class AutomationResponse(BaseModel):
    id: int
    nazwa: str
    trigger: Dict[str, Any]
    warunek: Optional[Dict[str, Any]] = None
    akcja: Dict[str, Any]
    aktywna: bool