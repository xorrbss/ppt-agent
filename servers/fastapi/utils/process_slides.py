from typing import List, Tuple
from models.presentation_layout import SlideLayoutModel
from models.sql.asset import ImageAsset
from models.sql.slide import SlideModel


def process_slide_and_fetch_assets(
    slide: SlideModel, layout: SlideLayoutModel
) -> Tuple[SlideModel, List[ImageAsset]]:
    pass