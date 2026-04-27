from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AnalysisRequest(_CamelModel):
    request_id: str
    game_id: str
    s3_uri: str


Classification = Literal[
    "best",
    "good",
    "inaccuracy",
    "mistake",
    "blunder",
]


class MoveAnalysis(_CamelModel):
    ply: int
    san: str
    uci: str
    cp: int | None = None
    mate: int | None = None
    classification: Classification


class SideSummary(_CamelModel):
    accuracy: float
    acpl: int


class Summary(_CamelModel):
    white: SideSummary
    black: SideSummary


class AnalysisError(_CamelModel):
    code: str
    message: str


class AnalysisResponse(_CamelModel):
    request_id: str
    game_id: str
    status: Literal["success", "failed"]
    analysis_s3_uri: str | None = None
    moves: list[MoveAnalysis] | None = None
    summary: Summary | None = None
    error: AnalysisError | None = None
