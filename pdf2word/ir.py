"""Intermediate representation (IR) — implementation-spec v1.4 §3.

All dataclasses and enums here mirror the specification exactly (field names,
order, types and defaults). The module also provides a generic, type-hint
driven JSON (de)serialisation used for IR dumps, the JSONL blocks file and
debug output.

Coordinates are PDF points with the origin at the page's top-left.
IDs follow ``"p{page}:{kind}{n}"`` and are unique per document (see make_id).
"""

from __future__ import annotations

import dataclasses
import json
import re
import types
import typing
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Union

BBox = tuple[float, float, float, float]
"""(x0, y0, x1, y1) in PDF points, origin top-left."""


def make_id(page_no: int, kind: str, n: int) -> str:
    """Return a document-unique element id, e.g. ``make_id(6, "t", 42) == "p6:t42"``."""
    if page_no < 1:
        raise ValueError(f"page numbers are 1-based, got {page_no}")
    if not kind or not kind.isalpha():
        raise ValueError(f"id kind must be letters only, got {kind!r}")
    if n < 0:
        raise ValueError(f"id counter must be >= 0, got {n}")
    return f"p{page_no}:{kind}{n}"


_ID = re.compile(r"^p(\d+):([a-z]+)(\d+)$")


def id_key(element_id: str) -> tuple:
    """Natural sort key for element ids ("p2:t10" after "p2:t9"). Analysis iterates id collections
    in this order so results never depend on set/hash order (PYTHONHASHSEED; repair release D-1)."""
    m = _ID.match(element_id)
    return (int(m.group(1)), m.group(2), int(m.group(3))) if m else (0, element_id, 0)


# ---------------------------------------------------------------- enums ----
class PathKind(Enum):
    GLYPH = "glyph"
    RULE = "rule"
    ART = "art"


class ZoneKind(Enum):
    INLINE_MATH = "inline_math"
    DISPLAY_MATH = "display_math"
    LINE_PRESERVE = "line_preserve"      # whole line kept as image
    BLOCK_PRESERVE = "block_preserve"    # group of lines kept as image
    FIGURE = "figure"
    ICON = "icon"
    TABLE_IMAGE = "table_image"
    PAGE_FALLBACK = "page_fallback"


class Escalation(Enum):
    TIGHT = 0
    EXPANDED = 1
    LINE = 2
    BLOCK = 3


class Confidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FateKind(Enum):
    EMITTED_TEXT = "emitted_text"
    IN_ZONE = "in_zone"
    IN_TABLE = "in_table"
    REMOVED = "removed"


class Role(Enum):
    CHAPTER_NUMBER = "chapter_number"
    CHAPTER_TITLE = "chapter_title"
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    BODY = "body"
    LIST_BULLET = "list_bullet"
    LIST_NUMBER = "list_number"
    CAPTION = "caption"
    EXAMPLE_Q = "example_q"
    EXAMPLE_A = "example_a"
    OTHER = "other"


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ----------------------------------------------------------- primitives ----
@dataclass(frozen=True)
class FontInfo:
    name: str
    size: float
    bold: bool
    italic: bool
    color: int


@dataclass(frozen=True)
class CharInfo:
    """Exact source character, never normalised (§4.2)."""

    c: str                          # Unicode exactly as extracted
    bbox: BBox
    origin: tuple[float, float]
    synthetic: bool                 # True if the backend inserted the char (e.g. an inferred space)
    glyph_id: int | None = None     # optional; only from a genuine backend field, never inferred


@dataclass
class Token:
    """Unit of fate for text (§4.2)."""

    id: str
    chars: list[CharInfo]
    raw_text: str                   # "".join(c.c for c in chars), unmodified
    ws_before: str                  # exact whitespace preceding the token in its source span
    ws_after_span_end: str | None   # trailing whitespace if last token of its span, else None
    bbox: BBox
    origin: tuple[float, float]
    font: FontInfo
    span_flags: int
    baseline_shift_pt: float
    size_ratio: float
    script: str                     # "none" | "super" | "sub"
    src_order: tuple[int, int, int]
    parent_span: str
    suspicious: bool = False
    protected: bool = False
    repairs: list[RepairRecord] = field(default_factory=list)
    line_id: str | None = None

    def __post_init__(self) -> None:
        joined = "".join(ch.c for ch in self.chars)
        if joined != self.raw_text:
            raise ValueError(
                f"Token {self.id}: raw_text {self.raw_text!r} != joined chars {joined!r} (§4.2 fidelity)"
            )
        if self.script not in ("none", "super", "sub"):
            raise ValueError(f"Token {self.id}: invalid script {self.script!r}")


@dataclass(frozen=True)
class RepairRecord:
    """One character-level change to a token (T3 or T4, §4.2)."""

    char_index: int
    source: str
    replacement: str                # "" for a removal (T3 soft hyphen)
    rule: str                       # "T3:soft_hyphen" | "T4:<font name>:U+XXXX"


@dataclass
class PathItem:
    id: str
    bbox: BBox
    kind: PathKind                  # descriptive only (§4.1)
    filled: bool
    stroke_width: float | None
    n_segments: int
    shape_key: str
    protected: bool = False
    protection_reason: str | None = None   # "glyph" | "band:<line_id>"
    released_by: str | None = None         # "table:<id>" | "figure:<id>" | "icon:<id>" | "underline:<token_id>"


@dataclass
class ImageItem:
    id: str
    bbox: BBox
    ref: int
    content_hash: str
    px_size: tuple[int, int]
    ext: str
    protected: bool = False
    released_by: str | None = None


@dataclass
class Fate:
    kind: FateKind
    target: str | None = None       # zone_id / table_id / token_id (underline)
    reason: str | None = None       # REMOVED: "slug"|"running_head"|"folio"|"crop_mark"|"icon"|"empty"
    stage: str = ""                 # module that assigned it (diagnostics)


# -------------------------------------------------------- page analysis ----
@dataclass
class Line:
    id: str
    bbox: BBox
    baseline: float
    x_height: float
    token_ids: list[str]
    path_ids: list[str]
    protection_band: BBox
    band: int = 0
    column: int = 0


@dataclass
class Asset:
    content_hash: str
    path: Path
    px_size: tuple[int, int]
    ext: str


@dataclass
class Zone:
    id: str
    kind: ZoneKind
    page_no: int
    bbox: BBox
    token_ids: list[str] = field(default_factory=list)
    path_ids: list[str] = field(default_factory=list)
    image_ids: list[str] = field(default_factory=list)
    line_ids: list[str] = field(default_factory=list)
    escalation: Escalation = Escalation.TIGHT
    page_clamped: bool = False
    merged_from: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    reasons: list[str] = field(default_factory=list)
    asset_hash: str | None = None
    replacement_text: str | None = None
    baseline_offset_pt: float = 0.0
    display_width_pt: float = 0.0
    caption_token_ids: list[str] = field(default_factory=list)
    consumed_caption_token_ids: list[str] = field(default_factory=list)
    preview_text: str = ""


@dataclass
class TableCell:
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    content: list[Inline] = field(default_factory=list)


@dataclass
class Table:
    id: str
    page_no: int
    bbox: BBox
    n_rows: int
    n_cols: int
    col_widths_pt: list[float]
    cells: list[TableCell]
    header_rows: int
    source: str
    confidence: Confidence
    replaced_zone_id: str


@dataclass
class PageIR:
    page_no: int
    size: tuple[float, float]
    tokens: dict[str, Token]
    paths: dict[str, PathItem]
    images: dict[str, ImageItem]
    lines: list[Line]
    zones: dict[str, Zone]
    tables: dict[str, Table]
    fates: dict[str, Fate]
    blocks: list[Block]
    issues: list[Issue]
    timings_ms: dict[str, float]
    fallback: bool = False
    extracted: bool = True


# ----------------------------------------------------- logical document ----
@dataclass
class TextRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    token_ids: list[str] = field(default_factory=list)


@dataclass
class InlineImage:
    zone_id: str                    # INLINE_MATH or LINE_PRESERVE


@dataclass
class InlineOperator:
    zone_id: str
    text: str


Inline = Union[TextRun, InlineImage, InlineOperator]


@dataclass
class Paragraph:
    id: str
    role: Role
    style_name: str
    inlines: list[Inline]
    pages: tuple[int, int]
    list_level: int = 0
    style_confidence: Confidence = Confidence.MEDIUM
    contains_inline_image: bool = False


@dataclass
class PictureBlock:
    zone_id: str
    centered: bool = True


@dataclass
class TableBlock:
    table_id: str


@dataclass
class IconCallout:
    """A small margin icon (REMEMBER, TIP, WARNING...) together with the paragraph it introduces,
    laid out side by side (icon | text) as the source book does, instead of a standalone centred
    picture with the paragraph above and below it."""

    icon_zone_id: str
    paragraph: Paragraph


@dataclass
class PageFallbackBlock:
    page_no: int
    zone_id: str | None             # None only if render failed
    error: str


Block = Union[Paragraph, PictureBlock, TableBlock, PageFallbackBlock, IconCallout]


@dataclass
class Issue:
    id: str
    page_no: int
    severity: Severity
    category: str
    message: str
    element_id: str | None
    bbox: BBox | None


@dataclass
class DocumentIR:
    source: Path
    source_sha256: str
    page_count: int
    profile_id: str
    template_used: str | None
    versions: dict[str, str]
    blocks_file: Path
    zones: dict[str, Zone]
    tables: dict[str, Table]
    assets: dict[str, Asset]
    issues: list[Issue]
    stats: dict[str, int]
    diagnostics: dict[str, float]
    timings_ms: dict[str, float]


IR_CLASSES: tuple[type, ...] = (
    FontInfo, CharInfo, Token, RepairRecord, PathItem, ImageItem, Fate, Line, Asset, Zone,
    TableCell, Table, PageIR, TextRun, InlineImage, InlineOperator, Paragraph, PictureBlock,
    TableBlock, PageFallbackBlock, IconCallout, Issue, DocumentIR,
)
IR_ENUMS: tuple[type[Enum], ...] = (PathKind, ZoneKind, Escalation, Confidence, FateKind, Role, Severity)


# ------------------------------------------------------- serialisation ----
TYPE_KEY = "_type"


class IRSerializationError(ValueError):
    """Raised when a value cannot be encoded or decoded according to its type hints."""


def to_jsonable(obj: Any) -> Any:
    """Convert an IR object (or any dataclass tree) into JSON-compatible data.

    Dataclasses become dicts tagged with ``"_type": ClassName`` so unions
    (Inline, Block) can be decoded unambiguously. Enums are stored by value,
    tuples as lists, Paths as strings.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {TYPE_KEY: type(obj).__name__}
        for f in dataclasses.fields(obj):
            out[f.name] = to_jsonable(getattr(obj, f.name))
        return out
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        for k in obj:
            if not isinstance(k, str):
                raise IRSerializationError(f"dict keys must be str, got {type(k).__name__}")
        return {k: to_jsonable(v) for k, v in obj.items()}
    raise IRSerializationError(f"cannot serialise value of type {type(obj).__name__}")


def to_json(obj: Any, *, indent: int | None = None) -> str:
    """Serialise to a JSON string (UTF-8 safe; non-ASCII kept verbatim)."""
    return json.dumps(to_jsonable(obj), ensure_ascii=False, indent=indent)


def from_jsonable(data: Any, hint: Any) -> Any:
    """Decode JSON-compatible ``data`` into an instance described by type ``hint``."""
    return _decode(data, hint, path="$")


def from_json(text: str, hint: Any) -> Any:
    return from_jsonable(json.loads(text), hint)


_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINT_CACHE:
        module = __import__(cls.__module__, fromlist=["_"])
        _HINT_CACHE[cls] = typing.get_type_hints(cls, vars(module))
    return _HINT_CACHE[cls]


def _decode(data: Any, hint: Any, path: str) -> Any:
    if hint is Any:
        return data
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)

    if origin in (Union, types.UnionType):
        if data is None:
            if type(None) in args:
                return None
            raise IRSerializationError(f"{path}: null not allowed for {hint}")
        members = [a for a in args if a is not type(None)]
        if len(members) == 1:
            return _decode(data, members[0], path)
        if isinstance(data, dict) and TYPE_KEY in data:
            for m in members:
                if isinstance(m, type) and m.__name__ == data[TYPE_KEY]:
                    return _decode(data, m, path)
            raise IRSerializationError(f"{path}: _type {data[TYPE_KEY]!r} not in {hint}")
        for m in members:  # primitive unions such as int | str
            try:
                return _decode(data, m, path)
            except IRSerializationError:
                continue
        raise IRSerializationError(f"{path}: value {data!r} matches no member of {hint}")

    if origin is list:
        if not isinstance(data, list):
            raise IRSerializationError(f"{path}: expected list, got {type(data).__name__}")
        (item,) = args or (Any,)
        return [_decode(v, item, f"{path}[{i}]") for i, v in enumerate(data)]
    if origin is tuple:
        if not isinstance(data, list):
            raise IRSerializationError(f"{path}: expected list for tuple, got {type(data).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(v, args[0], f"{path}[{i}]") for i, v in enumerate(data))
        if len(args) != len(data):
            raise IRSerializationError(f"{path}: expected {len(args)} items, got {len(data)}")
        return tuple(_decode(v, a, f"{path}[{i}]") for i, (v, a) in enumerate(zip(data, args)))
    if origin is dict:
        if not isinstance(data, dict):
            raise IRSerializationError(f"{path}: expected object, got {type(data).__name__}")
        _, val = args or (str, Any)
        return {k: _decode(v, val, f"{path}.{k}") for k, v in data.items()}

    if isinstance(hint, type):
        if issubclass(hint, Enum):
            try:
                return hint(data)
            except ValueError as exc:
                raise IRSerializationError(f"{path}: {exc}") from None
        if dataclasses.is_dataclass(hint):
            if not isinstance(data, dict):
                raise IRSerializationError(f"{path}: expected object for {hint.__name__}")
            tag = data.get(TYPE_KEY)
            if tag is not None and tag != hint.__name__:
                raise IRSerializationError(f"{path}: _type {tag!r} != {hint.__name__}")
            hints = _hints(hint)
            kwargs = {}
            for f in dataclasses.fields(hint):
                if f.name in data:
                    kwargs[f.name] = _decode(data[f.name], hints[f.name], f"{path}.{f.name}")
                elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                    raise IRSerializationError(f"{path}: missing field {f.name!r} for {hint.__name__}")
            unknown = set(data) - {f.name for f in dataclasses.fields(hint)} - {TYPE_KEY}
            if unknown:
                raise IRSerializationError(f"{path}: unknown fields {sorted(unknown)} for {hint.__name__}")
            return hint(**kwargs)
        if hint is Path:
            if not isinstance(data, str):
                raise IRSerializationError(f"{path}: expected string path")
            return Path(data)
        if hint is float:
            if isinstance(data, bool) or not isinstance(data, (int, float)):
                raise IRSerializationError(f"{path}: expected number, got {data!r}")
            return float(data)
        if hint is int:
            if isinstance(data, bool) or not isinstance(data, int):
                raise IRSerializationError(f"{path}: expected int, got {data!r}")
            return data
        if hint is bool:
            if not isinstance(data, bool):
                raise IRSerializationError(f"{path}: expected bool, got {data!r}")
            return data
        if hint is str:
            if not isinstance(data, str):
                raise IRSerializationError(f"{path}: expected str, got {data!r}")
            return data
        if hint is type(None):
            if data is not None:
                raise IRSerializationError(f"{path}: expected null")
            return None
    raise IRSerializationError(f"{path}: unsupported type hint {hint!r}")
