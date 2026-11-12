from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, UploadFile

import web_app
from qr_bead_blueprint.core import QrDecodeError


def _start_request():
    image = UploadFile(filename="qr.png", file=io.BytesIO(b"private-qr-image"))
    task = asyncio.create_task(web_app.generate_blueprint(
        image=image,
        platform="other",
        beads_per_module=1,
        bead_size_mm=5.0,
        dark_color="#111827",
        light_color="#FFFFFF",
        palette="mard-221",
        compatibility_mode="auto",
        allow_low_contrast=False,
    ))
    return task, image


@pytest.fixture
def job_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "job"
    root.mkdir()
    store = web_app.JobStore()
    monkeypatch.setattr(store, "create", lambda: ("test-job", root))
    monkeypatch.setattr(web_app, "job_store", store)
    return root


def test_busy_request_is_rejected_before_reading_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario():
        gate = asyncio.Queue(1)
        gate.put_nowait(None)
        monkeypatch.setattr(web_app, "_admission_slots", lambda: gate)
        body_read = False

        async def body():
            nonlocal body_read
            body_read = True
            yield b"must-not-be-read"

        transport = httpx.ASGITransport(app=web_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/generate", content=body())
            health = await client.get("/health")

        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert not body_read
        assert gate.qsize() == 1
        assert health.status_code == 200

    asyncio.run(scenario())


def test_admission_capacity_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario():
        gate = asyncio.Queue(2)
        monkeypatch.setattr(web_app, "_admission_slots", lambda: gate)
        entered = asyncio.Queue()
        hold = asyncio.Event()

        async def handler(scope, receive, send):
            entered.put_nowait(None)
            await hold.wait()

        middleware = web_app.ConversionAdmissionMiddleware(handler)
        scope = {"type": "http", "method": "POST", "path": "/api/generate"}
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            raise AssertionError("busy middleware must not read the body")

        first = asyncio.create_task(middleware(scope, receive, send))
        second = asyncio.create_task(middleware(scope, receive, send))
        try:
            await asyncio.wait_for(entered.get(), 2)
            await asyncio.wait_for(entered.get(), 2)
            await middleware(scope, receive, send)
            assert messages[0]["status"] == 503
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert gate.qsize() == 1

            replacement = asyncio.create_task(middleware(scope, receive, send))
            try:
                await asyncio.wait_for(entered.get(), 2)
                hold.set()
                await asyncio.gather(second, replacement)
                assert gate.empty()
            finally:
                replacement.cancel()
                await asyncio.gather(replacement, return_exceptions=True)
        finally:
            hold.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(scenario())


def test_queued_cancellation_removes_original(
    job_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario():
        slots = asyncio.Semaphore(0)
        monkeypatch.setattr(web_app, "_conversion_slots", lambda: slots)
        saved = asyncio.Event()
        save_upload = web_app._save_upload

        async def save(image, destination):
            await save_upload(image, destination)
            saved.set()

        monkeypatch.setattr(web_app, "_save_upload", save)
        task, image = _start_request()
        try:
            await asyncio.wait_for(saved.wait(), 2)
            assert (job_root / "upload.bin").exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not job_root.exists()
            assert image.file.closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_fails", [False, True])
def test_running_cancellation_waits_before_cleanup(
    job_root: Path, monkeypatch: pytest.MonkeyPatch, worker_fails: bool,
) -> None:
    async def scenario():
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(web_app, "_conversion_slots", lambda: slots)
        started = threading.Event()
        release = threading.Event()

        def convert(**kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError("worker was not released")
            assert kwargs["upload_path"].exists()
            if worker_fails:
                raise QrDecodeError("conversion failed after cancellation")
            return None, job_root, "clean"

        monkeypatch.setattr(web_app, "_convert_with_fallback", convert)
        task, image = _start_request()
        try:
            async def wait_for_start():
                while not started.is_set():
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_for_start(), 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert slots.locked()
                assert (job_root / "upload.bin").exists()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert not job_root.exists()
            assert image.file.closed
            assert not slots.locked()
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_conversion_failure_cleans_files_and_releases_slot(
    job_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario():
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(web_app, "_conversion_slots", lambda: slots)

        def convert(**kwargs):
            raise QrDecodeError("invalid QR")

        monkeypatch.setattr(web_app, "_convert_with_fallback", convert)
        task, image = _start_request()
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 422
        assert not job_root.exists()
        assert image.file.closed
        assert not slots.locked()

    asyncio.run(scenario())
