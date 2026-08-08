DEFAULT_TEMPLATES = ["adaptive", "korean-biz", "financial-chart", "comparison-table", "roadmap", "org-chart"]
MAX_NUMBER_OF_SLIDES = 100

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
