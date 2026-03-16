from __future__ import annotations

import io
from functools import lru_cache
import logging
import traceback

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from app.auth import require_bearer_token
from app.config import Settings, get_settings
from app.models import BarcodePrintRequest, QRPrintRequest, TextPrintRequest
from app.printer_service import (
    InvalidImageError,
    PrinterConfigurationError,
    PrinterExecutionError,
    PrinterService,
)
from app.text_rendering import MarkdownImage, decode_text_file, parse_markdown

logger = logging.getLogger("escpos_fastapi")
settings = get_settings()

if settings.app_debug:
    logger.setLevel(logging.INFO)

app = FastAPI(
    title="ESC/POS USB Print Server",
    version="1.0.0",
    description="API for printing receipts on a USB ESC/POS printer.",
    debug=settings.app_debug,
)

if settings.app_cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _debug_log(message: str, **context: object) -> None:
    if not settings.app_debug:
        return
    logger.info("%s | %s", message, context)


def _image_debug_info(image_bytes: bytes) -> dict:
    image_info = {
        "exists": bool(image_bytes),
        "width": None,
        "height": None,
        "format": None,
    }

    if not image_bytes:
        return image_info

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_info["width"] = image.width
            image_info["height"] = image.height
            image_info["format"] = image.format
    except UnidentifiedImageError:
        image_info["format"] = "invalid"

    return image_info


def _textfile_debug_info(file_bytes: bytes, *, extension: str, rendered_text: str | None) -> dict:
    return {
        "exists": bool(file_bytes),
        "extension": extension,
        "bytes": len(file_bytes),
        "rendered_chars": len(rendered_text) if rendered_text is not None else None,
        "rendered_preview": rendered_text[:120] if rendered_text else None,
    }


def _markdown_debug_info(blocks: list[object]) -> dict:
    image_blocks = [block for block in blocks if isinstance(block, MarkdownImage)]
    return {
        "blocks": len(blocks),
        "images": len(image_blocks),
        "image_sources": [block.source for block in image_blocks],
    }


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if settings.app_debug:
        _debug_log(
            "Request received",
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            content_type=request.headers.get("content-type"),
            client=request.client.host if request.client else None,
        )

    response = await call_next(request)

    if settings.app_debug:
        _debug_log(
            "Request completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
        )

    return response


@lru_cache(maxsize=1)
def get_printer_service() -> PrinterService:
    return PrinterService(settings)


def _raise_http_error(exc: Exception) -> None:
    logger.exception("Failed while processing a print request", exc_info=exc)

    status_code = 500
    message = "Unexpected printing error."

    if isinstance(exc, InvalidImageError):
        status_code = 400
        message = str(exc)
    elif isinstance(exc, PrinterConfigurationError):
        status_code = 500
        message = str(exc)
    elif isinstance(exc, PrinterExecutionError):
        status_code = 503
        message = str(exc)

    if settings.app_debug:
        trace = "".join(traceback.TracebackException.from_exception(exc).format(chain=True))
        raise HTTPException(
            status_code=status_code,
            detail={
                "message": message,
                "exception_type": exc.__class__.__name__,
                "traceback": trace,
            },
        ) from exc

    raise HTTPException(status_code=status_code, detail=message) from exc


@app.get("/health")
def health(
    _: None = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    _debug_log("Health payload", printer_profile=settings.printer_profile)
    return {
        "status": "ok",
        "printer": {
            "vendor_id": hex(settings.printer_vendor_id),
            "product_id": hex(settings.printer_product_id),
            "in_ep": hex(settings.printer_in_ep),
            "out_ep": hex(settings.printer_out_ep),
            "profile": settings.printer_profile,
        },
    }


@app.post("/print/text")
async def print_text(
    payload: TextPrintRequest,
    _: None = Depends(require_bearer_token),
    printer_service: PrinterService = Depends(get_printer_service),
) -> dict:
    _debug_log("Payload text", payload=payload.model_dump())
    try:
        await run_in_threadpool(printer_service.print_text, payload)
    except Exception as exc:
        _raise_http_error(exc)

    return {"status": "sent", "type": "text", "message": "Text receipt sent to printer."}


@app.post("/print/image")
async def print_image(
    file: UploadFile = File(...),
    center: bool = Form(True),
    feed: int = Form(3, ge=0, le=10),
    cut: bool = Form(True),
    _: None = Depends(require_bearer_token),
    printer_service: PrinterService = Depends(get_printer_service),
) -> dict:
    image_bytes = await file.read()
    await file.close()

    _debug_log(
        "Payload image",
        filename=file.filename,
        content_type=file.content_type,
        center=center,
        feed=feed,
        cut=cut,
        image=_image_debug_info(image_bytes),
    )

    if not image_bytes:
        raise HTTPException(status_code=400, detail="The image file is empty.")

    try:
        await run_in_threadpool(
            printer_service.print_image,
            image_bytes,
            center=center,
            feed=feed,
            cut=cut,
        )
    except Exception as exc:
        _raise_http_error(exc)

    return {
        "status": "sent",
        "type": "image",
        "filename": file.filename,
        "message": "Image sent to printer.",
    }


@app.post("/print/textfile")
async def print_textfile(
    file: UploadFile = File(...),
    align: str = Form("left"),
    bold: bool = Form(False),
    underline: int = Form(0, ge=0, le=2),
    feed: int = Form(3, ge=0, le=10),
    cut: bool = Form(True),
    _: None = Depends(require_bearer_token),
    printer_service: PrinterService = Depends(get_printer_service),
) -> dict:
    filename = file.filename or ""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    file_bytes = await file.read()
    await file.close()

    if extension not in {"txt", "md"}:
        raise HTTPException(status_code=400, detail="Only .txt and .md files are supported.")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The text file is empty.")
    if align not in {"left", "center", "right"}:
        raise HTTPException(status_code=400, detail="align must be left, center, or right.")

    try:
        raw_text = decode_text_file(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    markdown_blocks = None
    normalized_text = None
    if extension == "md":
        try:
            markdown_blocks = parse_markdown(raw_text)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not parse Markdown: {exc}",
            ) from exc
        normalized_text = "\n".join(
            "".join(span.text for span in block.spans)
            for block in markdown_blocks
            if hasattr(block, "spans")
        ).strip("\n")
        if not any(
            isinstance(block, MarkdownImage) or getattr(block, "spans", None)
            for block in markdown_blocks
        ):
            raise HTTPException(status_code=400, detail="The file does not contain printable Markdown content.")
    else:
        normalized_text = raw_text.replace("\r\n", "\n").strip("\n")
        if not normalized_text.strip():
            raise HTTPException(status_code=400, detail="The file does not contain printable text.")

    _debug_log(
        "Payload textfile",
        filename=filename,
        content_type=file.content_type,
        align=align,
        bold=bold,
        underline=underline,
        feed=feed,
        cut=cut,
        textfile=_textfile_debug_info(
            file_bytes,
            extension=extension,
            rendered_text=normalized_text,
        ),
        markdown=_markdown_debug_info(markdown_blocks) if markdown_blocks is not None else None,
    )

    try:
        if markdown_blocks is not None:
            await run_in_threadpool(
                printer_service.print_markdown,
                markdown_blocks,
                align=align,
                bold=bold,
                underline=underline,
                feed=feed,
                cut=cut,
            )
        else:
            await run_in_threadpool(
                printer_service.print_text_content,
                normalized_text,
                align=align,
                bold=bold,
                underline=underline,
                feed=feed,
                cut=cut,
            )
    except Exception as exc:
        _raise_http_error(exc)

    return {
        "status": "sent",
        "type": "textfile",
        "filename": filename,
        "message": "Text file sent to printer.",
    }


@app.post("/print/qr")
async def print_qr(
    payload: QRPrintRequest,
    _: None = Depends(require_bearer_token),
    printer_service: PrinterService = Depends(get_printer_service),
) -> dict:
    _debug_log("Payload qr", payload=payload.model_dump())
    try:
        await run_in_threadpool(printer_service.print_qr, payload)
    except Exception as exc:
        _raise_http_error(exc)

    return {"status": "sent", "type": "qr", "message": "QR code sent to printer."}


@app.post("/print/barcode")
async def print_barcode(
    payload: BarcodePrintRequest,
    _: None = Depends(require_bearer_token),
    printer_service: PrinterService = Depends(get_printer_service),
) -> dict:
    _debug_log("Payload barcode", payload=payload.model_dump())
    try:
        await run_in_threadpool(printer_service.print_barcode, payload)
    except Exception as exc:
        _raise_http_error(exc)

    return {
        "status": "sent",
        "type": "barcode",
        "message": "Barcode sent to printer.",
    }
