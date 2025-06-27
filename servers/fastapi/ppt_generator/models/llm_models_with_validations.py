from typing import List, Mapping, Union
from pydantic import Field

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
from ppt_generator.models.llm_models import (
    LLMTableDataModel,
    LLMTableModel,
    LLMHeadingModel,
    LLMHeadingModelWithImagePrompt,
    LLMHeadingModelWithIconQuery,
    LLMSlideContentModel,
    LLMType1Content,
    LLMType2Content,
    LLMType3Content,
    LLMType4Content,
    LLMType5Content,
    LLMType6Content,
    LLMType7Content,
    LLMType8Content,
    LLMType9Content,
    LLMSlideModel,
    LLMPresentationModel,
)


class LLMTableDataModelWithValidation(LLMTableDataModel):
    x_labels: List[str] = Field(
        description="X labels of the table",
        min_length=1,
        max_length=5,
    )
    y_labels: List[str] = Field(
        description="Y labels of the table",
        min_length=1,
        max_length=3,
    )
    data: List[List[float]] = Field(
        description="Data of the table",
        min_length=1,
        max_length=5,
    )


class LLMTableModelWithValidation(LLMTableModel):
    name: str = Field(
        description="Name of the table in less than 8 words",
        min_length=10,
        max_length=50,
    )
    data: LLMTableDataModelWithValidation


class LLMHeadingModelWithValidation(LLMHeadingModel):
    heading: str = Field(
        description="Item heading in less than 6 words",
        min_length=10,
        max_length=40,
    )
    description: str = Field(
        description="Item description in less than 15 words.",
        min_length=50,
        max_length=150,
    )


class LLMHeadingModelWithImagePromptWithValidation(LLMHeadingModelWithImagePrompt):
    image_prompt: str = Field(
        description="Item image prompt in less than 10 words",
        min_length=10,
        max_length=100,
    )


class LLMHeadingModelWithIconQueryWithValidation(LLMHeadingModelWithIconQuery):
    icon_query: str = Field(
        description="Item icon query in less than 4 words",
        min_length=10,
        max_length=40,
    )


class LLMSlideContentModelWithValidation(LLMSlideContentModel):
    title: str = Field(
        description="Slide title in less than 8 words",
        min_length=10,
        max_length=80,
    )


class LLMType1ContentWithValidation(LLMType1Content):
    body: str = Field(
        description="Slide content summary in less than 30 words.",
        min_length=50,
        max_length=300,
    )
    image_prompt: str = Field(
        description="Slide image prompt in less than 5 words",
        min_length=10,
        max_length=30,
    )


class LLMType2ContentWithValidation(LLMType2Content):
    body: List[LLMHeadingModelWithValidation] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=4,
    )


class LLMType3ContentWithValidation(LLMType3Content):
    body: List[LLMHeadingModelWithValidation] = Field(
        description="Items to show in slide",
        min_length=3,
        max_length=3,
    )
    image_prompt: str = Field(
        description="Slide image prompt in less than 5 words",
        min_length=10,
        max_length=30,
    )


class LLMType4ContentWithValidation(LLMType4Content):
    body: List[LLMHeadingModelWithImagePromptWithValidation] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )


class LLMType5ContentWithValidation(LLMType5Content):
    body: str = Field(
        description="Slide content summary in less than 30 words.",
        min_length=50,
        max_length=300,
    )
    table: LLMTableModelWithValidation = Field(description="Table to show in slide")


class LLMType6ContentWithValidation(LLMType6Content):
    description: str = Field(
        description="Slide content summary in less than 20 words.",
        min_length=50,
        max_length=300,
    )
    body: List[LLMHeadingModelWithValidation] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )


class LLMType7ContentWithValidation(LLMType7Content):
    body: List[LLMHeadingModelWithIconQueryWithValidation] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=4,
    )


class LLMType8ContentWithValidation(LLMType8Content):
    description: str = Field(
        description="Slide content summary in less than 20 words.",
        min_length=50,
        max_length=300,
    )
    body: List[LLMHeadingModelWithImagePromptWithValidation] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )


class LLMType9ContentWithValidation(LLMType9Content):
    body: List[LLMHeadingModelWithValidation] = Field(
        description="Items to show in slide",
        min_length=1,
        max_length=3,
    )
    table: LLMTableModelWithValidation = Field(description="Table to show in slide")


LLMContentUnionWithValidation = Union[
    LLMType1ContentWithValidation,
    LLMType2ContentWithValidation,
    LLMType3ContentWithValidation,
    LLMType4ContentWithValidation,
    LLMType5ContentWithValidation,
    LLMType6ContentWithValidation,
    LLMType7ContentWithValidation,
    LLMType8ContentWithValidation,
    LLMType9ContentWithValidation,
]

LLM_CONTENT_TYPE_MAPPING_WITH_VALIDATION: Mapping[
    int, LLMContentUnionWithValidation
] = {
    TYPE1: LLMType1ContentWithValidation,
    TYPE2: LLMType2ContentWithValidation,
    TYPE3: LLMType3ContentWithValidation,
    TYPE4: LLMType4ContentWithValidation,
    TYPE5: LLMType5ContentWithValidation,
    TYPE6: LLMType6ContentWithValidation,
    TYPE7: LLMType7ContentWithValidation,
    TYPE8: LLMType8ContentWithValidation,
    TYPE9: LLMType9ContentWithValidation,
}


class LLMSlideModelWithValidation(LLMSlideModel):
    type: int
    content: LLMContentUnionWithValidation


class LLMPresentationModelWithValidation(LLMPresentationModel):
    slides: List[LLMSlideModelWithValidation]
