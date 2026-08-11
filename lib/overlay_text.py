"""Text normalization shared by HTML overlay generators."""


def normalize_overlay_text(value: object) -> str:
    """Return UTF-8 encodable text even when logs contain surrogate bytes."""
    text = str(value or "")
    return text.encode("utf-8", errors="replace").decode("utf-8").replace("\x00", "\ufffd")
