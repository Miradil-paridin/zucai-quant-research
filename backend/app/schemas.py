from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProbabilityDistribution(BaseModel):
    home: float = Field(ge=0, le=1)
    draw: float = Field(ge=0, le=1)
    away: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def probabilities_sum_to_one(self) -> "ProbabilityDistribution":
        if abs(self.home + self.draw + self.away - 1) > 0.0001:
            raise ValueError("1X2 probabilities must sum to one")
        return self


class MatchSummary(BaseModel):
    data_status: Literal["demo_not_for_decision"] = "demo_not_for_decision"
    match_id: str
    competition: str
    kickoff_utc: datetime
    home_team: str
    away_team: str
    status: Literal["scheduled", "live", "finished", "postponed", "cancelled"]
    model_probability: ProbabilityDistribution
    market_probability: ProbabilityDistribution
    edge: float
    data_completeness: float = Field(ge=0, le=1)
    lineup_confidence: float = Field(ge=0, le=1)
    calibration_status: str


class WatchlistRequest(BaseModel):
    match_id: str
    market: str = "1X2.home"
    requested_stake_units: float = Field(default=1.0, gt=0, le=1.0)


class WatchlistResponse(BaseModel):
    audit_id: str
    match_id: str
    status: Literal["observing"] = "observing"
    created_at_utc: datetime
    message: str
