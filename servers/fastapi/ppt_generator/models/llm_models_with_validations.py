from typing import List, Mapping
from pydantic import Field

from graph_processor.models import GraphModel
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


class LLMHeadingModelWithValidation(LLMHeadingModel):
    heading: str = Field(
        description="List item heading to show in slide body in less than 5 words.",
    )
    description: str = Field(
        description="Description of list item in less than 20 words.",
    )


class LLMHeadingModelWithImagePromptWithValidation(LLMHeadingModelWithImagePrompt):
    image_prompt: str = Field(
        description="Prompt used to generate image for this item in less than 6 words.",
    )


class LLMHeadingModelWithIconQueryWithValidation(LLMHeadingModelWithIconQuery):
    icon_query: str = Field(
        description="Icon query to generate icon for this item in less than 4 words.",
    )


class LLMType1ContentWithValidation(LLMType1Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: str = Field(
        description="Slide content summary in less than 30 words.",
    )
    image_prompt: str = Field(
        description="Prompt used to generate image for this slide in less than 6 words.",
    )

    @classmethod
    def get_notes(cls):
        return ""


class LLMType2ContentWithValidation(LLMType2Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: List[LLMHeadingModelWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=1,
        max_length=4,
    )

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **1 to 4 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


class LLMType3ContentWithValidation(LLMType3Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: List[LLMHeadingModelWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=3,
        max_length=3,
    )
    image_prompt: str = Field(
        description="Prompt used to generate image for this slide in less than 6 words.",
    )

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **3 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


class LLMType4ContentWithValidation(LLMType4Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: List[LLMHeadingModelWithImagePromptWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=1,
        max_length=3,
    )

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **1 to 3 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


class LLMType5ContentWithValidation(LLMType5Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: str = Field(
        description="Slide content summary in less than 30 words.",
    )
    graph: GraphModel = Field(description="Graph to show in slide")

    @classmethod
    def get_notes(self):
        return ""


class LLMType6ContentWithValidation(LLMType6Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    description: str = Field(
        description="Slide content summary in less than 20 words.",
    )
    body: List[LLMHeadingModelWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=1,
        max_length=3,
    )

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **1 to 3 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


class LLMType7ContentWithValidation(LLMType7Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: List[LLMHeadingModelWithIconQueryWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=1,
        max_length=4,
    )

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **1 to 4 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


class LLMType8ContentWithValidation(LLMType8Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    description: str = Field(
        description="Slide content summary in less than 20 words.",
    )
    body: List[LLMHeadingModelWithImagePromptWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=1,
        max_length=3,
    )

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **1 to 3 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


class LLMType9ContentWithValidation(LLMType9Content):
    title: str = Field(
        description="Title of the slide in less than 6 words.",
    )
    body: List[LLMHeadingModelWithValidation] = Field(
        description="List items to show in slide's body",
        min_length=1,
        max_length=3,
    )
    graph: GraphModel = Field(description="Graph to show in slide")

    @classmethod
    def get_notes(cls):
        return """
        - The **Body** should include **1 to 3 HeadingModels**.  
        - Each **Heading** must consist of **1 to 3 words**.  
        - Each item **Description** can be upto 10 words.
        """


LLM_CONTENT_TYPE_WITH_VALIDATION_MAPPING: Mapping[int, LLMSlideContentModel] = {
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
    content: (
        LLMType1ContentWithValidation
        | LLMType2ContentWithValidation
        | LLMType4ContentWithValidation
        | LLMType5ContentWithValidation
        | LLMType6ContentWithValidation
        | LLMType7ContentWithValidation
        | LLMType8ContentWithValidation
        | LLMType9ContentWithValidation
    )


class LLMPresentationModelWithValidation(LLMPresentationModel):
    slides: list[LLMSlideModelWithValidation]
