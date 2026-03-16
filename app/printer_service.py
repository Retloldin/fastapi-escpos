from __future__ import annotations

import io
from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from escpos.constants import (
    QR_ECLEVEL_H,
    QR_ECLEVEL_L,
    QR_ECLEVEL_M,
    QR_ECLEVEL_Q,
    QR_MODEL_2,
)
from escpos.exceptions import DeviceNotFoundError, Error as EscposError
from escpos.printer import Usb
from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.models import BarcodePrintRequest, QRPrintRequest, TextPrintRequest
from app.text_rendering import MarkdownBlock, MarkdownImage, MarkdownTextLine


class PrinterServiceError(RuntimeError):
    """Base error for the printer service."""


class PrinterConfigurationError(PrinterServiceError):
    """Raised when printer settings or USB dependencies are invalid."""


class InvalidImageError(PrinterServiceError):
    """Raised when the uploaded payload is not a valid image."""


class PrinterExecutionError(PrinterServiceError):
    """Raised when the physical print job cannot be completed."""


class PrinterService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = Lock()

    def print_text(self, payload: TextPrintRequest) -> None:
        self.print_text_content(
            payload.text,
            align=payload.align,
            bold=payload.bold,
            underline=payload.underline,
            feed=payload.feed,
            cut=payload.cut,
        )

    def print_text_content(
        self,
        text: str,
        *,
        align: str = "left",
        bold: bool = False,
        underline: int = 0,
        feed: int = 3,
        cut: bool = True,
    ) -> None:
        def job(printer: Usb) -> None:
            printer.set(
                align=align,
                bold=bold,
                underline=underline,
            )
            printer.text(text)
            if not text.endswith("\n"):
                printer.text("\n")
            if feed:
                printer.ln(feed)
            if cut:
                printer.cut(mode=self.settings.printer_cut_mode, feed=False)

        self._run_job(job)

    def print_image(self, image_bytes: bytes, *, center: bool, feed: int, cut: bool) -> None:
        try:
            with Image.open(io.BytesIO(image_bytes)) as uploaded_image:
                printable_image = uploaded_image.copy()
        except UnidentifiedImageError as exc:
            raise InvalidImageError("The uploaded file is not a valid image.") from exc

        def job(printer: Usb) -> None:
            printer.image(printable_image, center=center)
            if feed:
                printer.ln(feed)
            if cut:
                printer.cut(mode=self.settings.printer_cut_mode, feed=False)

        try:
            self._run_job(job)
        finally:
            printable_image.close()

    def print_markdown(
        self,
        blocks: list[MarkdownBlock],
        *,
        align: str = "left",
        bold: bool = False,
        underline: int = 0,
        feed: int = 3,
        cut: bool = True,
    ) -> None:
        printable_images: list[Image.Image] = []
        try:
            for block in blocks:
                if isinstance(block, MarkdownImage):
                    with Image.open(io.BytesIO(block.image_bytes)) as uploaded_image:
                        printable_images.append(uploaded_image.copy())

            image_iterator = iter(printable_images)

            def job(printer: Usb) -> None:
                printer.set(align=align, underline=underline, bold=False)
                for block in blocks:
                    if isinstance(block, MarkdownTextLine):
                        if not block.spans:
                            printer.text("\n")
                            continue

                        printer.set(align=align, underline=underline, bold=False)
                        for span in block.spans:
                            printer.set(bold=bold or span.bold)
                            printer.text(span.text)
                        printer.set(bold=False)
                        printer.text("\n")
                        continue

                    printable_image = next(image_iterator)
                    printer.image(printable_image, center=align == "center")
                    printer.text("\n")

                if feed:
                    printer.ln(feed)
                if cut:
                    printer.cut(mode=self.settings.printer_cut_mode, feed=False)

            self._run_job(job)
        except UnidentifiedImageError as exc:
            raise InvalidImageError("The Markdown-referenced image is not valid.") from exc
        finally:
            for image in printable_images:
                image.close()

    def print_qr(self, payload: QRPrintRequest) -> None:
        ec_map = {
            "L": QR_ECLEVEL_L,
            "M": QR_ECLEVEL_M,
            "Q": QR_ECLEVEL_Q,
            "H": QR_ECLEVEL_H,
        }

        def job(printer: Usb) -> None:
            printer.qr(
                payload.content,
                ec=ec_map[payload.error_correction],
                size=payload.size,
                model=QR_MODEL_2,
                native=payload.native,
                center=payload.center,
            )
            if payload.feed:
                printer.ln(payload.feed)
            if payload.cut:
                printer.cut(mode=self.settings.printer_cut_mode, feed=False)

        self._run_job(job)

    def print_barcode(self, payload: BarcodePrintRequest) -> None:
        def job(printer: Usb) -> None:
            printer.barcode(
                payload.code,
                payload.symbology,
                height=payload.height,
                width=payload.width,
                pos=payload.text_position,
                font=payload.font,
                align_ct=payload.align_center,
                check=payload.check,
            )
            if payload.feed:
                printer.ln(payload.feed)
            if payload.cut:
                printer.cut(mode=self.settings.printer_cut_mode, feed=False)

        self._run_job(job)

    def _run_job(self, callback) -> None:
        with self._lock:
            with self._open_printer() as printer:
                try:
                    callback(printer)
                except EscposError as exc:
                    raise PrinterExecutionError(
                        f"Failed while sending ESC/POS data to the printer: {exc}"
                    ) from exc
                except Exception as exc:
                    raise PrinterExecutionError(
                        f"Could not complete the print job: {exc}"
                    ) from exc

    @contextmanager
    def _open_printer(self) -> Iterator[Usb]:
        if not Usb.is_usable():
            raise PrinterConfigurationError(
                "The USB backend is not available. Install libusb/pyusb on the system."
            )

        printer_kwargs = {
            "timeout": self.settings.printer_timeout,
            "in_ep": self.settings.printer_in_ep,
            "out_ep": self.settings.printer_out_ep,
        }
        if self.settings.printer_profile:
            printer_kwargs["profile"] = self.settings.printer_profile

        printer = Usb(
            self.settings.printer_vendor_id,
            self.settings.printer_product_id,
            **printer_kwargs,
        )

        try:
            printer.open()
            yield printer
        except DeviceNotFoundError as exc:
            raise PrinterExecutionError(
                "Could not open the USB printer. Check the vendor/product ID, endpoints, and permissions."
            ) from exc
        except EscposError as exc:
            raise PrinterExecutionError(
                f"The ESC/POS library returned an error while opening the printer: {exc}"
            ) from exc
        finally:
            try:
                printer.close()
            except Exception:
                pass
