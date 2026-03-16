from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TextPrintRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Receipt text to print.")
    align: Literal["left", "center", "right"] = Field(
        "left",
        description="Text alignment.",
    )
    bold: bool = Field(False, description="Print text in bold.")
    underline: int = Field(0, ge=0, le=2, description="Underline level.")
    feed: int = Field(3, ge=0, le=10, description="Blank lines to feed at the end.")
    cut: bool = Field(True, description="Cut the paper at the end of the receipt.")

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").strip("\n")
        if not normalized.strip():
            raise ValueError("Receipt text cannot be empty.")
        return normalized


class QRPrintRequest(BaseModel):
    content: str = Field(..., min_length=1, description="QR code content.")
    error_correction: str = Field(
        "M",
        description="Error correction level: L, M, Q, or H.",
    )
    size: int = Field(5, ge=1, le=16, description="QR block size.")
    native: bool = Field(
        False,
        description="If true, let the printer render the QR code.",
    )
    center: bool = Field(True, description="Center the QR code.")
    feed: int = Field(3, ge=0, le=10, description="Blank lines to feed at the end.")
    cut: bool = Field(True, description="Cut the paper at the end of the receipt.")

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("QR content cannot be empty.")
        return normalized

    @field_validator("error_correction")
    @classmethod
    def normalize_error_correction(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"L", "M", "Q", "H"}:
            raise ValueError("error_correction must be one of: L, M, Q, H.")
        return normalized


class BarcodePrintRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Barcode content.")
    symbology: str = Field(
        ...,
        min_length=1,
        description="Barcode type, for example EAN13 or CODE128.",
    )
    height: int = Field(64, ge=1, le=255, description="Barcode height.")
    width: int = Field(3, ge=2, le=6, description="Module width.")
    text_position: str = Field(
        "BELOW",
        description="Text position: ABOVE, BELOW, BOTH, or OFF.",
    )
    font: str = Field("A", description="Human-readable text font: A or B.")
    align_center: bool = Field(True, description="Center the barcode.")
    check: bool = Field(True, description="Validate the content before printing.")
    feed: int = Field(3, ge=0, le=10, description="Blank lines to feed at the end.")
    cut: bool = Field(True, description="Cut the paper at the end of the receipt.")

    @field_validator("code", "symbology")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value cannot be empty.")
        return normalized

    @field_validator("symbology")
    @classmethod
    def normalize_symbology(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("text_position")
    @classmethod
    def normalize_text_position(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"ABOVE", "BELOW", "BOTH", "OFF"}:
            raise ValueError("text_position must be ABOVE, BELOW, BOTH, or OFF.")
        return normalized

    @field_validator("font")
    @classmethod
    def normalize_font(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"A", "B"}:
            raise ValueError("font must be A or B.")
        return normalized
