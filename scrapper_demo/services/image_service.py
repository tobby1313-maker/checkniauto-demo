"""Compatibility facade for the adaptive V2 image pipeline."""
from . import adaptive_image_service as _impl
from .adaptive_image_service import *  # noqa: F401,F403


def _sync_runtime_overrides() -> None:
    # Existing tests and deployments patch these module globals at runtime.
    for name in (
        "AI_MAX_VISION_ATTACHMENTS",
        "MAX_ANALYSIS_COLLAGES",
        "LLM_IMAGE_END_POSITION",
        "OVERVIEW_TARGET_ITEMS_PER_SHEET",
        "DETAIL_ONLY_MAX_IMAGES",
        "MAX_DETAIL_COLLAGES_WITH_OVERVIEW",
    ):
        if name in globals():
            setattr(_impl, name, globals()[name])


def prepare_llm_images(slug_dir, *, log=None):
    _sync_runtime_overrides()
    return _impl.prepare_llm_images(slug_dir, log=log)
