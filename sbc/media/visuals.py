"""Mutable visual surfaces for Python-published BBB screen shares.

The classes in this module are rendering primitives.  They do not contain bot,
chat-command, or meeting-policy logic: applications decide *when* to update a
surface and pass it to :meth:`client.screenshare.start`.
"""
from __future__ import annotations

import textwrap
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


Color = str | tuple[int, int, int] | tuple[int, int, int, int]
Renderer = Callable[["VisualSurface"], Any]
TextDirection = str


def _contains_rtl(value: str) -> bool:
    """Return whether a string contains Arabic, Persian, or Hebrew script."""
    return any(
        "\u0590" <= character <= "\u08ff"
        or "\ufb1d" <= character <= "\ufdff"
        or "\ufe70" <= character <= "\ufeff"
        for character in value
    )


def _font_candidates(*, rtl: bool, bold: bool) -> tuple[str, ...]:
    """Return platform font candidates, preferring Persian-capable fonts."""
    windows = Path("C:/Windows/Fonts")
    if rtl:
        names = (
            "Vazirmatn-Bold.ttf" if bold else "Vazirmatn-Regular.ttf",
            "Vazir-Bold.ttf" if bold else "Vazir.ttf",
            "BYekan.ttf",
            "IYekan.ttf",
            "Tahoma.ttf",
            "NotoNaskhArabic-Bold.ttf" if bold else "NotoNaskhArabic-Regular.ttf",
            "NotoSansArabic-Bold.ttf" if bold else "NotoSansArabic-Regular.ttf",
            "arialbd.ttf" if bold else "arial.ttf",
        )
    else:
        names = ("arialbd.ttf" if bold else "arial.ttf", "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf")
    paths = [str(windows / name) for name in names if (windows / name).exists()]
    paths.extend(names)
    return tuple(paths)


def _load_font(size: int, *, rtl: bool, bold: bool, font: str | Path | None) -> Any:
    from PIL import ImageFont

    candidates = ((str(font),) if font is not None else ()) + _font_candidates(rtl=rtl, bold=bold)
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _shape_rtl(value: str) -> str:
    """Shape Arabic-family text when Pillow was built without libraqm."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(value))
    except Exception:
        # The caller still receives readable characters on minimal Python
        # environments; declaring the packages as SBC dependencies provides
        # connected glyphs and correct bidirectional ordering in normal use.
        return value


class VisualSurface:
    """A thread-safe, mutable RGBA canvas rendered into a live screen share.

    A surface can be changed at any time.  Changes become visible in the next
    video frame; no WebRTC renegotiation is required.

    ``render`` callbacks may return a Pillow image or an ``(H, W, 3|4)``
    NumPy-compatible array.  For imperative drawings, use :meth:`paint`.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        *,
        frame_rate: int = 15,
        background: Color = "#101827",
    ) -> None:
        if width < 16 or height < 16:
            raise ValueError("visual surfaces must be at least 16x16 pixels")
        if not 1 <= frame_rate <= 60:
            raise ValueError("frame_rate must be between 1 and 60")
        self.width = int(width)
        self.height = int(height)
        self.frame_rate = int(frame_rate)
        self._background = background
        self._lock = threading.RLock()
        self._renderer: Renderer | None = None
        self._image: Any | None = None
        self._revision = 0

    @property
    def revision(self) -> int:
        """Monotonically increasing value changed by every visual update."""
        with self._lock:
            return self._revision

    @property
    def background(self) -> Color:
        with self._lock:
            return self._background

    def set_background(self, color: Color) -> "VisualSurface":
        """Set the fallback background colour and return this surface."""
        with self._lock:
            self._background = color
            self._revision += 1
        return self

    def set_renderer(self, renderer: Renderer | None) -> "VisualSurface":
        """Install a declarative renderer called for each outgoing frame."""
        if renderer is not None and not callable(renderer):
            raise TypeError("renderer must be callable or None")
        with self._lock:
            self._renderer = renderer
            self._revision += 1
        return self

    def set_image(self, image: Any) -> "VisualSurface":
        """Replace the canvas with a Pillow image or RGB/RGBA array."""
        with self._lock:
            self._image = image.copy() if hasattr(image, "copy") else image
            self._renderer = None
            self._revision += 1
        return self

    def load_image(self, path: str | Path) -> "VisualSurface":
        """Load an image file as the current canvas."""
        from PIL import Image

        with Image.open(path) as image:
            return self.set_image(image.convert("RGBA"))

    def paint(self, painter: Callable[[Any, Any], None], *, clear: bool = True) -> "VisualSurface":
        """Draw onto a Pillow canvas with ``painter(image, draw)``.

        ``clear=False`` starts from the current rendered image, allowing callers
        to build a persistent canvas incrementally.
        """
        if not callable(painter):
            raise TypeError("painter must be callable")
        from PIL import Image, ImageDraw

        with self._lock:
            if clear or self._image is None:
                image = Image.new("RGBA", (self.width, self.height), self._background)
            else:
                image = self._coerce_image(self._image)
            painter(image, ImageDraw.Draw(image))
            self._image = image
            self._renderer = None
            self._revision += 1
        return self

    def render(self) -> Any:
        """Return the current frame as a Pillow RGBA image.

        This method is intentionally public so an application may preview or
        save the exact frame it is sending to BBB.
        """
        from PIL import Image

        with self._lock:
            renderer = self._renderer
            image = self._image
            background = self._background
        if renderer is not None:
            image = renderer(self)
        if image is None:
            return Image.new("RGBA", (self.width, self.height), background)
        return self._coerce_image(image)

    def _coerce_image(self, image: Any) -> Any:
        from PIL import Image

        if isinstance(image, Image.Image):
            result = image.convert("RGBA")
        else:
            import numpy as np

            array = np.asarray(image)
            if array.ndim != 3 or array.shape[2] not in (3, 4):
                raise TypeError("visual renderers must return a Pillow image or an RGB/RGBA array")
            result = Image.fromarray(array.astype("uint8"), "RGBA" if array.shape[2] == 4 else "RGB").convert("RGBA")
        if result.size != (self.width, self.height):
            result = result.resize((self.width, self.height))
        return result


class TextBoard(VisualSurface):
    """A reusable text-oriented visual surface.

    The board is generic mutable media, not a bot.  Update it from commands,
    timers, an API, or any other application code::

        board = client.screenshare.textboard("Waiting…")
        client.screenshare.start(board)
        board.set_text("Round 2 starts now")
    """

    def __init__(
        self,
        text: str = "",
        *,
        title: str | None = None,
        width: int = 1280,
        height: int = 720,
        frame_rate: int = 15,
        background: Color = "#101827",
        foreground: Color = "#f8fafc",
        accent: Color = "#38bdf8",
        font_size: int = 56,
        padding: int = 72,
        direction: TextDirection = "auto",
        language: str | None = None,
        font: str | Path | None = None,
    ) -> None:
        super().__init__(width, height, frame_rate=frame_rate, background=background)
        self._text = str(text)
        self._title = title
        self._foreground = foreground
        self._accent = accent
        self._font_size = max(12, int(font_size))
        self._padding = max(0, int(padding))
        self._direction = self._validate_direction(direction)
        self._language = language
        self._font = font

    @property
    def text(self) -> str:
        with self._lock:
            return self._text

    def set_text(self, text: str) -> "TextBoard":
        """Replace the body text shown by the board."""
        with self._lock:
            self._text = str(text)
            self._revision += 1
        return self

    def append(self, text: str, *, separator: str = "\n") -> "TextBoard":
        """Append body text without restarting the published screen stream."""
        with self._lock:
            self._text = f"{self._text}{separator if self._text else ''}{text}"
            self._revision += 1
        return self

    def clear(self) -> "TextBoard":
        """Clear the body text."""
        return self.set_text("")

    def set_title(self, title: str | None) -> "TextBoard":
        """Set or remove the title displayed above the body."""
        with self._lock:
            self._title = title
            self._revision += 1
        return self

    @property
    def direction(self) -> TextDirection:
        """Configured text direction: ``auto``, ``rtl``, or ``ltr``."""
        with self._lock:
            return self._direction

    def set_direction(self, direction: TextDirection, *, language: str | None = None) -> "TextBoard":
        """Set text direction; ``auto`` detects Persian/Arabic/Hebrew text."""
        with self._lock:
            self._direction = self._validate_direction(direction)
            if language is not None:
                self._language = language
            self._revision += 1
        return self

    def set_font(self, font: str | Path | None) -> "TextBoard":
        """Use a specific font file/name, or restore SBC's adaptive fallback."""
        with self._lock:
            self._font = font
            self._revision += 1
        return self

    def set_style(
        self,
        *,
        foreground: Color | None = None,
        accent: Color | None = None,
        font_size: int | None = None,
        padding: int | None = None,
    ) -> "TextBoard":
        """Change board typography without interrupting the screen share."""
        with self._lock:
            if foreground is not None:
                self._foreground = foreground
            if accent is not None:
                self._accent = accent
            if font_size is not None:
                self._font_size = max(12, int(font_size))
            if padding is not None:
                self._padding = max(0, int(padding))
            self._revision += 1
        return self

    def render(self) -> Any:
        from PIL import Image, ImageDraw, features

        with self._lock:
            text, title = self._text, self._title
            background, foreground, accent = self._background, self._foreground, self._accent
            size, padding = self._font_size, self._padding
            configured_direction, language, font = self._direction, self._language, self._font
        rtl = configured_direction == "rtl" or (configured_direction == "auto" and _contains_rtl(f"{title or ''}\n{text}"))
        raqm = features.check("raqm")
        body_font = _load_font(size, rtl=rtl, bold=False, font=font)
        title_font = _load_font(max(18, int(size * 0.52)), rtl=rtl, bold=True, font=font)
        image = Image.new("RGBA", (self.width, self.height), background)
        draw = ImageDraw.Draw(image)
        y = padding
        if title:
            self._draw_line(draw, title, title_font, accent, y, padding, rtl, raqm, language)
            y += int(size * 1.35)
        max_width = max(1, self.width - padding * 2)
        words_per_line = max(8, int(max_width / max(1, size * 0.58)))
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(paragraph, width=words_per_line, replace_whitespace=False) or [""])
        line_height = int(size * 1.3)
        for line in lines:
            if y + line_height > self.height - padding:
                break
            self._draw_line(draw, line, body_font, foreground, y, padding, rtl, raqm, language)
            y += line_height
        return image

    @staticmethod
    def _validate_direction(direction: TextDirection) -> TextDirection:
        value = str(direction).lower()
        if value not in {"auto", "rtl", "ltr"}:
            raise ValueError("direction must be 'auto', 'rtl', or 'ltr'")
        return value

    def _draw_line(
        self,
        draw: Any,
        value: str,
        font: Any,
        color: Color,
        y: int,
        padding: int,
        rtl: bool,
        raqm: bool,
        language: str | None,
    ) -> None:
        # Pillow's libraqm path shapes complex scripts natively.  Many Windows
        # Pillow wheels omit it, so reshape/reorder Persian and Arabic before
        # drawing in that case.  The right anchor also preserves RTL alignment
        # for mixed Persian/Latin content.
        text = value if raqm or not rtl else _shape_rtl(value)
        position = (self.width - padding, y) if rtl else (padding, y)
        kwargs: dict[str, Any] = {"font": font, "fill": color, "anchor": "ra" if rtl else "la"}
        if raqm:
            kwargs["direction"] = "rtl" if rtl else "ltr"
            if language:
                kwargs["language"] = language
        draw.text(position, text, **kwargs)


__all__ = ["Color", "Renderer", "TextBoard", "TextDirection", "VisualSurface"]
