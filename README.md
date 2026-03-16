# ESC/POS FastAPI USB Print Server

A FastAPI server for printing text or image receipts on a USB-connected ESC/POS printer using `python-escpos`.

## Requirements

- Python 3.9 or newer
- `libusb` available on the system

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Printer settings are loaded from `.env`.

```dotenv
PRINTER_VENDOR_ID=0x0000
PRINTER_PRODUCT_ID=0x0000
PRINTER_IN_EP=0x82
PRINTER_OUT_EP=0x01
PRINTER_TIMEOUT=0
PRINTER_PROFILE=
PRINTER_CUT_MODE=FULL
APP_DEBUG=false
APP_CORS_ALLOWED_ORIGINS=["https://example.com"]
API_BEARER_TOKEN=replace-with-a-strong-token
```

- With `APP_DEBUG=true`, print failures include the full traceback in the HTTP response to simplify debugging and the server also logs each incoming request. For `/print/image`, records whether a file exists plus its width, height, and detected format.
- All requests require `Authorization: Bearer <token>` using the `API_BEARER_TOKEN` value.
- `APP_CORS_ALLOWED_ORIGINS` should be defined as a JSON array of allowed origins.

To find `VENDOR_ID` and `PRODUCT_ID`, you can use:

```bash
lsusb
```

On macOS, this is often useful:

```bash
system_profiler SPUSBDataType
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or use the prepared environment directly:

```bash
./run_server.sh
```

## Install as a Debian 13 Service

An installer for Debian 13 (`trixie`) is included at `scripts/install_debian_service.sh`.

Initial installation:

```bash
sudo ./install_debian_service.sh
```

Service update:

```bash
sudo ./install_debian_service.sh --update
```

The script installs system packages, syncs the project to `/opt/fastapi-escpos`, creates `.venv`, installs Python dependencies, and generates a `systemd` unit.

## Endpoints

### `GET /health`

Returns the service status and the loaded printer configuration.

### `POST /print/text`

Prints a text receipt from JSON:

```bash
curl -X POST http://127.0.0.1:8000/print/text \
  -H "Authorization: Bearer replace-with-a-strong-token" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "SAMPLE STORE\n123 Example Street\n----------------\nTotal: 12.50 EUR",
    "align": "left",
    "bold": false,
    "underline": 0,
    "feed": 3,
    "cut": true
  }'
```

### `POST /print/image`

Prints an uploaded image as `multipart/form-data`:

```bash
curl -X POST http://127.0.0.1:8000/print/image \
  -H "Authorization: Bearer replace-with-a-strong-token" \
  -F "file=@ticket.png" \
  -F "center=true" \
  -F "feed=3" \
  -F "cut=true"
```

### `POST /print/textfile`

Prints an uploaded `.txt` or `.md` file as `multipart/form-data`. `.md` files are parsed before printing, including bold text (`**text**`) and Markdown images (`![alt](source)`).
Supported Markdown image sources are `data:image/...`, and `http(s)` URLs.

```bash
curl -X POST http://127.0.0.1:8000/print/textfile \
  -H "Authorization: Bearer replace-with-a-strong-token" \
  -F "file=@ticket.md" \
  -F "align=left" \
  -F "bold=false" \
  -F "underline=0" \
  -F "feed=3" \
  -F "cut=true"
```

### `POST /print/qr`

Prints a QR code from JSON:

```bash
curl -X POST http://127.0.0.1:8000/print/qr \
  -H "Authorization: Bearer replace-with-a-strong-token" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "https://example.com/orders/123",
    "error_correction": "M",
    "size": 5,
    "native": false,
    "center": true,
    "feed": 3,
    "cut": true
  }'
```

### `POST /print/barcode`

Prints a barcode from JSON:

```bash
curl -X POST http://127.0.0.1:8000/print/barcode \
  -H "Authorization: Bearer replace-with-a-strong-token" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "123456789012",
    "symbology": "EAN13",
    "height": 64,
    "width": 3,
    "text_position": "BELOW",
    "font": "A",
    "align_center": true,
    "check": true,
    "feed": 3,
    "cut": true
  }'
```

## AI Disclosure

This repository was created with assistance from GPT-5.4 subsequently reviewed and tested by the maintainer.

## Notes

- The service serializes printer access to avoid concurrent jobs.
- If the printer does not respond, verify USB permissions, endpoints, and the ESC/POS profile.
- Image centering works best when `PRINTER_PROFILE` matches the real printer model and defines the paper width.
- Actual barcode format support depends on the printer ESC/POS profile and whether rendering is handled in hardware or software.
