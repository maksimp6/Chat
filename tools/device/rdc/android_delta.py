"""Change-only screenshot detection for local Android live server."""

import base64
from io import BytesIO
from PIL import Image, ImageChops


class DeltaDetector:
    """Track the last observed frame; return only changed region."""

    def __init__(self):
        self.previous = None
        self.sequence = 0

    def compare(self, filename):
        with Image.open(filename) as source:
            frame = source.convert("RGB")
        self.sequence += 1
        if self.previous is None or self.previous.size != frame.size:
            box = (0, 0, *frame.size)
            reason = "keyframe"
        else:
            # Ignore small compression noise; find bounding box of changed pixels.
            delta = ImageChops.difference(frame, self.previous).convert("L")
            mask = delta.point(lambda v: 255 if v >= 24 else 0)
            box = mask.getbbox()
            reason = "delta"
        self.previous = frame
        if box is None:
            return {"changed": False, "sequence": self.sequence, "image": None}
        left, top, right, bottom = box
        crop = frame.crop(box)
        out = BytesIO()
        crop.save(out, format="JPEG", quality=68, optimize=True)
        return {
            "changed": True,
            "sequence": self.sequence,
            "type": reason,
            "bbox": [left, top, right - left, bottom - top],
            "screen_size": list(frame.size),
            "encoding": "jpeg_base64",
            "image": base64.b64encode(out.getvalue()).decode("ascii"),
        }
