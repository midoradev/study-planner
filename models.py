from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import List, Optional


class Subject(BaseModel):
    id: str
    name: str
    deadline: date
    difficulty: int = Field(ge=1, le=5)
    est_hours: float = Field(gt=0)
    notes: str = ""


class Task(BaseModel):
    id: str
    subject_id: str
    subject_name: str
    day: date
    minutes: int = Field(gt=0)
    done: bool = False
    notes: str = ""


class Event(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime


class Settings(BaseModel):
    minutes_per_day: int = Field(ge=15, le=600, default=90)
    rest_days: List[int] = Field(default_factory=list)  # 0=Mon ... 6=Sun
    chunk_minutes: int = Field(default=25)
    daily_buffer_minutes: int = Field(default=15, ge=0, le=180)
    preferred_start_hour: int = Field(default=18, ge=0, le=23)
    preferred_end_hour: int = Field(default=22, ge=0, le=23)


class AppState(BaseModel):
    subjects: List[Subject] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)
    events: List[Event] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    last_generated_on: Optional[date] = None
    profile: str = "default"
