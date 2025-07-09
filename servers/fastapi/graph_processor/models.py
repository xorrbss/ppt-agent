from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class PointModel(BaseModel):
    x: float
    y: float

    def to_list(self) -> List[float]:
        return [self.x, self.y]


class PointWithRadius(PointModel):
    radius: Optional[float] = None


class BarSeriesModel(BaseModel):
    name: str
    data: List[float] = Field(
        description="Only numbers should be given out in data. Don't include text/string in data."
    )


class ScatterSeriesModel(BaseModel):
    name: str
    points: List[PointModel]


class BubbleSeriesModel(BaseModel):
    name: str
    points: List[PointWithRadius]


class LineSeriesModel(BaseModel):
    name: str
    data: List[float] = Field(
        description="Only numbers should be given out in data. Don't include text/string in data."
    )


class PieChartSeriesModel(BaseModel):
    data: List[float]


class BarGraphDataModel(BaseModel):
    categories: List[str]
    series: List[BarSeriesModel] = Field(
        description="There should be no more than 3 series"
    )


class ScatterChartDataModel(BaseModel):
    series: List[ScatterSeriesModel]


class BubbleChartDataModel(BaseModel):
    series: List[BubbleSeriesModel]


class LineChartDataModel(BaseModel):
    categories: List[str]
    series: List[LineSeriesModel] = Field(
        description="There should be no more than 3 series"
    )


class PieChartDataModel(BaseModel):
    categories: List[str]
    series: List[PieChartSeriesModel] = Field(
        description="One series model with list of data",
        min_length=1,
    )

    @model_validator(mode="after")
    def limit_series(self):
        self.series = self.series[:1]
        return self


class GraphTypeEnum(Enum):
    pie = "pie"
    bar = "bar"
    line = "line"


class LLMGraphModel(BaseModel):
    name: str
    type: GraphTypeEnum
    unit: Optional[str] = Field(
        description="Unit of the data in the graph. Example: %, kg, million USD, tonnes, etc."
    )
    data: PieChartDataModel | LineChartDataModel | BarGraphDataModel


class GraphModel(LLMGraphModel):
    style: Optional[dict] = {}

    @classmethod
    def from_llm_graph_model(
        cls, llm_graph_model: LLMGraphModel, style: Optional[dict] = {}
    ):
        return cls(
            name=llm_graph_model.name,
            type=llm_graph_model.type,
            unit=llm_graph_model.unit,
            data=llm_graph_model.data,
            style=style,
        )


GRAPH_TYPE_MAPPING = {
    GraphTypeEnum.pie: PieChartDataModel,
    GraphTypeEnum.bar: BarGraphDataModel,
    GraphTypeEnum.line: LineChartDataModel,
}
