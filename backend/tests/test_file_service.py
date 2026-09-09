"""Pure unit tests for local image validation and normalization."""
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.services import file_service


def image_bytes(image_format: str, size=(2, 2), **kwargs) -> bytes:
    output = BytesIO()
    Image.new("RGBA" if image_format == "PNG" and kwargs.pop("transparent", False) else "RGB", size, kwargs.pop("color", "red")).save(
        output, format=image_format, **kwargs
    )
    return output.getvalue()


def upload(data: bytes, filename: str, content_type: str | None) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(data), headers={"content-type": content_type or ""})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [("JPEG", "photo.jpg", "image/jpeg"), ("PNG", "photo.png", "image/png"), ("WEBP", "photo.webp", "image/webp")],
)
async def test_standard_images_are_retained(image_format, filename, content_type):
    data = image_bytes(image_format)
    result, suffix, result_type = await file_service.validate_image_upload(upload(data, filename, content_type))
    assert result == data
    assert suffix == {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image_format]
    assert result_type == content_type


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [("BMP", "photo.bmp", "image/bmp"), ("JPEG", "photo.png", "image/png"), ("WEBP", "photo.bin", "")],
)
async def test_nonstandard_declarations_are_converted_to_jpeg(image_format, filename, content_type):
    data = image_bytes(image_format)
    result, suffix, result_type = await file_service.validate_image_upload(upload(data, filename, content_type))
    assert result != data
    assert suffix == ".jpg"
    assert result_type == "image/jpeg"
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (2, 2)


@pytest.mark.anyio
async def test_transparent_png_is_composited_on_white_before_jpeg_conversion():
    data = image_bytes("PNG", color=(255, 0, 0, 0), transparent=True)
    result, suffix, result_type = await file_service.validate_image_upload(upload(data, "photo.bmp", "image/bmp"))
    assert suffix == ".jpg"
    assert result_type == "image/jpeg"
    with Image.open(BytesIO(result)) as image:
        assert image.getpixel((0, 0))[0] > 245
        assert image.getpixel((0, 0))[1] > 245
        assert image.getpixel((0, 0))[2] > 245


@pytest.mark.anyio
async def test_converted_metadata_uses_converted_size():
    data = image_bytes("BMP", size=(20, 20), color="blue")
    result, _, result_type = await file_service.validate_image_upload(upload(data, "photo.bmp", "image/bmp"))
    assert result_type == "image/jpeg"
    assert len(result) != len(data)
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"


@pytest.mark.anyio
@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\ntruncated"])
async def test_empty_or_corrupt_images_are_rejected(data):
    with pytest.raises(HTTPException) as error:
        await file_service.validate_image_upload(upload(data, "photo.png", "image/png"))
    assert error.value.status_code == 400


@pytest.mark.anyio
async def test_oversized_upload_is_rejected():
    with pytest.raises(HTTPException, match="不能超过"):
        await file_service.validate_image_upload(upload(b"x" * 11, "photo.jpg", "image/jpeg"), max_size_bytes=10)


@pytest.mark.anyio
async def test_pixel_bomb_is_rejected(monkeypatch):
    monkeypatch.setattr(file_service, "_MAX_IMAGE_PIXELS", 4)
    data = image_bytes("PNG", size=(3, 2))
    with pytest.raises(HTTPException, match="像素量过大"):
        await file_service.validate_image_upload(upload(data, "photo.png", "image/png"))
