DEFAULT_TEMPLATES = ["adaptive", "korean-biz", "financial-chart", "comparison-table", "roadmap", "org-chart"]
MAX_NUMBER_OF_SLIDES = 100
DEFAULT_ATTACHMENT_GENERATION_PROMPT = (
    "Generate a presentation from the attached files."
)

# Authored slides are model-written HTML with no layout schema behind them.
# The schema-driven chat tools can delete such a slide, but `save_slide` has no
# layout to rebuild an equivalent one from - so an "improve this page" turn
# destroys the page and leaves a blank in its place.  The tools refuse the
# destructive half and name the flow that can regenerate an authored page.
AUTHORED_SLIDE_CHAT_EDIT_REFUSAL = (
    "This page is model-authored HTML with no editable layout behind it. The "
    "slide tools cannot rebuild it once changed, so they will not modify it. "
    "Tell the user to regenerate this page with the page remake action "
    "(\"페이지 다시 만들기\"); do not attempt to delete or replace it."
)

# The machine-readable half of the refusal above.  The prose reaches the model,
# which then only tells the user which flow to use; this code reaches the
# workspace client, which can offer that flow directly.  A refusal that names a
# target page carries it under `validation_errors` beside that page's `index`,
# which is what makes the offer addressable.
AUTHORED_SLIDE_CHAT_EDIT_REFUSAL_CODE = "authored_slide_requires_page_remake"
