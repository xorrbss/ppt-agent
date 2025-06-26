from typing import List, Literal, Mapping
from pydantic import BaseModel, Field

from ppt_generator.models.content_type_models import (
    HeadingModel,
    SlideContentModel,
    TableDataModel,
    TableModel,
    Type1Content,
    Type2Content,
    Type3Content,
    Type4Content,
    Type5Content,
    Type6Content,
    Type7Content,
    Type8Content,
    Type9Content,
)
from ppt_generator.models.other_models import (
    TYPE1,
    TYPE2,
    TYPE3,
    TYPE4,
    TYPE5,
    TYPE6,
    TYPE7,
    TYPE8,
    TYPE9,
)


class LLMTableDataModel(TableDataModel):
    x_labels: List[str] = Field(description="X labels of the table")
    y_labels: List[str] = Field(description="Y labels of the table")
    data: List[List[float]] = Field(description="Data of the table")


class LLMTableModel(TableModel):
    name: str = Field(description="Name of the table")
    data: LLMTableDataModel


class LLMHeadingModel(BaseModel):
    heading: str = Field(
        description="Item heading in less than 6 words",
    )
    description: str = Field(
        description="Item description in less than 15 words.",
    )

    def to_content(self) -> HeadingModel:
        return HeadingModel(
            heading=self.heading,
            description=self.description,
        )


class LLMHeadingModelWithImagePrompt(LLMHeadingModel):
    image_prompt: str = Field(
        description="Item image prompt in less than 5 words",
    )

    def to_content(self) -> HeadingModel:
        return HeadingModel(
            heading=self.heading,
            description=self.description,
        )


class LLMHeadingModelWithIconQuery(LLMHeadingModel):
    icon_query: str = Field(
        description="Item icon query in less than 5 words",
    )

    def to_content(self) -> HeadingModel:
        return HeadingModel(
            heading=self.heading,
            description=self.description,
        )


class LLMSlideContentModel(BaseModel):
    # title: str = Field(
    #     description="Slide title in less than 8 words",
    # )

    def to_content(self) -> SlideContentModel:
        raise NotImplementedError("to_content method not implemented")


class LLMType1Content(LLMSlideContentModel):
    type: Literal["1"] = "1"
    body: str = Field(
        description="Slide content summary in less than 30 words.",
    )
    image_prompt: str = Field(
        description="Slide image prompt in less than 5 words",
    )

    def to_content(self) -> Type1Content:
        return Type1Content(
            title=self.title,
            body=self.body,
            image_prompts=[self.image_prompt],
        )


class LLMType2Content(LLMSlideContentModel):
    type: Literal["2"] = "2"
    body: List[LLMHeadingModel] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=4,
    )

    def to_content(self) -> Type2Content:
        return Type2Content(
            title=self.title,
            body=[each.to_content() for each in self.body],
        )


class LLMType3Content(LLMSlideContentModel):
    type: Literal["3"] = "3"
    body: List[LLMHeadingModel] = Field(
        description="Items to show in slide",
        min_length=3,
        max_length=3,
    )
    image_prompt: str = Field(
        description="Slide image prompt in less than 5 words",
    )

    def to_content(self) -> Type3Content:
        return Type3Content(
            title=self.title,
            body=[each.to_content() for each in self.body],
            image_prompts=[self.image_prompt],
        )


class LLMType4Content(LLMSlideContentModel):
    type: Literal["4"] = "4"
    body: List[LLMHeadingModelWithImagePrompt] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )

    def to_content(self) -> Type4Content:
        return Type4Content(
            title=self.title,
            body=[each.to_content() for each in self.body],
            image_prompts=[each.image_prompt for each in self.body],
        )


class LLMType5Content(LLMSlideContentModel):
    type: Literal["5"] = "5"
    body: str = Field(
        description="Slide content summary in less than 30 words.",
    )
    table: LLMTableModel = Field(description="Table to show in slide")

    def to_content(self) -> Type5Content:
        return Type5Content(
            title=self.title,
            body=self.body,
            table=self.table,
        )


class LLMType6Content(LLMSlideContentModel):
    type: Literal["6"] = "6"
    description: str = Field(
        description="Slide content summary in less than 20 words.",
    )
    body: List[LLMHeadingModel] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )

    def to_content(self) -> Type6Content:
        return Type6Content(
            title=self.title,
            description=self.description,
            body=[each.to_content() for each in self.body],
        )


class LLMType7Content(LLMSlideContentModel):
    type: Literal["7"] = "7"
    body: List[LLMHeadingModelWithIconQuery] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=4,
    )

    def to_content(self) -> Type7Content:
        return Type7Content(
            title=self.title,
            body=[each.to_content() for each in self.body],
            icon_queries=[each.icon_query for each in self.body],
        )


class LLMType8Content(LLMSlideContentModel):
    type: Literal["8"] = "8"
    description: str = Field(
        description="Slide content summary in less than 20 words.",
    )
    body: List[LLMHeadingModelWithImagePrompt] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )

    def to_content(self) -> Type8Content:
        return Type8Content(
            title=self.title,
            description=self.description,
            body=[each.to_content() for each in self.body],
            icon_queries=[each.image_prompt for each in self.body],
        )


class LLMType9Content(LLMSlideContentModel):
    type: Literal["9"] = "9"
    body: List[LLMHeadingModel] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )
    table: LLMTableModel = Field(description="Table to show in slide")

    def to_content(self) -> Type9Content:
        return Type9Content(
            title=self.title,
            body=[each.to_content() for each in self.body],
            table=self.table,
        )


LLM_CONTENT_TYPE_MAPPING: Mapping[int, LLMSlideContentModel] = {
    TYPE1: LLMType1Content,
    TYPE2: LLMType2Content,
    TYPE3: LLMType3Content,
    TYPE4: LLMType4Content,
    TYPE5: LLMType5Content,
    TYPE6: LLMType6Content,
    TYPE7: LLMType7Content,
    TYPE8: LLMType8Content,
    TYPE9: LLMType9Content,
}


class LLMSlideModel(BaseModel):
    type: int
    content: (
        LLMType1Content
        | LLMType2Content
        | LLMType4Content
        | LLMType5Content
        | LLMType6Content
        # | LLMType7Content
        # | LLMType8Content
        # | LLMType9Content
    )


class LLMPresentationModel(BaseModel):
    slides: list[LLMSlideModel]
