from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from qr_bead_blueprint import (
    BlueprintConfig,
    ImageBlueprintConfig,
    QrDecodeError,
    convert_image_to_blueprint,
    convert_qr_to_blueprint,
)
from qr_bead_blueprint.palettes import PALETTE_FILES


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
JOB_TTL_SECONDS = 30 * 60
QR_FILE_NAMES = {
    "blueprint.png",
    "blueprint.pdf",
    "scan_preview.png",
    "materials.csv",
    "metadata.json",
}
IMAGE_FILE_NAMES = {
    "blueprint.png",
    "blueprint.pdf",
    "preview.png",
    "materials.csv",
    "metadata.json",
}
ALLOWED_FILE_NAMES = QR_FILE_NAMES | IMAGE_FILE_NAMES


@dataclass(frozen=True)
class GeneratedJob:
    root: Path
    result_dir: Path
    expires_at: float


class JobStore:
    """管理短期生成结果；任务过期后同时删除图纸和二维码数据。"""

    def __init__(self, ttl_seconds: int = JOB_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, GeneratedJob] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, Path]:
        self.cleanup_expired()
        job_id = uuid.uuid4().hex
        root = Path(tempfile.mkdtemp(prefix="beadcode-"))
        return job_id, root

    def publish(self, job_id: str, root: Path, result_dir: Path) -> GeneratedJob:
        job = GeneratedJob(
            root=root,
            result_dir=result_dir,
            expires_at=time.monotonic() + self.ttl_seconds,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> GeneratedJob:
        self.cleanup_expired()
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def discard(self, job_id: str, root: Path | None = None) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        # 隐私规则：失败任务也要清除上传原图，不能依赖系统临时目录自行回收。
        target = job.root if job else root
        if target is not None:
            shutil.rmtree(target, ignore_errors=True)

    def cleanup_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                (job_id, job)
                for job_id, job in self._jobs.items()
                if job.expires_at <= now
            ]
            for job_id, _job in expired:
                self._jobs.pop(job_id, None)
        for _job_id, job in expired:
            shutil.rmtree(job.root, ignore_errors=True)

    def close_all(self) -> None:
        """服务退出时清空仍存活的敏感任务，避免容器重启后留下孤儿文件。"""
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            shutil.rmtree(job.root, ignore_errors=True)


job_store = JobStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """定期执行隐私清理；停止服务时再做一次全量清理。"""
    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(60)
            job_store.cleanup_expired()

    cleanup_task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        job_store.close_all()


app = FastAPI(
    title="BeadCode｜拼豆码工坊",
    description="将静态收款二维码或普通图片转换为可制作的拼豆施工图。",
    version="1.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """返回手机与桌面共用的单页生成界面。"""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    """供 Docker、Nginx 或运维平台检查进程是否可用。"""
    return {"status": "ok"}


@app.post("/api/generate")
async def generate_blueprint(
    image: UploadFile = File(...),
    platform: str = Form("other"),
    beads_per_module: int = Form(2),
    bead_size_mm: float = Form(5.0),
    dark_color: str = Form("#111827"),
    light_color: str = Form("#FFFFFF"),
    palette: str = Form("mard-221"),
    compatibility_mode: str = Form("auto"),
    allow_low_contrast: bool = Form(False),
) -> dict[str, object]:
    """接收二维码并生成图纸；原图在转换结束后立即删除，结果短期保留。"""
    _validate_form(platform, compatibility_mode, palette)
    job_id, root = job_store.create()
    upload_path = root / "upload.bin"

    try:
        await _save_upload(image, upload_path)
        result, result_dir, used_mode = _convert_with_fallback(
            upload_path=upload_path,
            root=root,
            beads_per_module=beads_per_module,
            bead_size_mm=bead_size_mm,
            dark_color=dark_color,
            light_color=light_color,
            palette=palette,
            compatibility_mode=compatibility_mode,
            allow_low_contrast=allow_low_contrast,
        )
        upload_path.unlink(missing_ok=True)
        job_store.publish(job_id, root, result_dir)
    except (ValueError, QrDecodeError) as exc:
        job_store.discard(job_id, root)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        job_store.discard(job_id, root)
        raise
    except Exception as exc:
        job_store.discard(job_id, root)
        raise HTTPException(status_code=500, detail="生成失败，请稍后重试") from exc
    finally:
        await image.close()

    warning = None
    if used_mode == "preserve":
        warning = "已使用原始平台模块兼容模式；制作实物时建议选择每模块 2×2 颗并充分测试。"
    elif allow_low_contrast:
        warning = "当前颜色低于保守对比度标准；数字预览已复扫通过，但实物必须额外测试。"

    return {
        "job_id": job_id,
        "expires_in_minutes": JOB_TTL_SECONDS // 60,
        "mode": used_mode,
        "warning": warning,
        "summary": {
            "qr_version": result.version,
            "module_size": result.module_size,
            "grid_size": result.bead_grid_size,
            "dark_beads": result.dark_beads,
            "light_beads": result.light_beads,
            "physical_size_cm": round(result.physical_size_mm / 10, 1),
            "scan_verified": result.scan_verified,
            "palette_key": result.palette_key,
            "palette_title": result.palette_title,
            "dark_code": result.dark_code,
            "light_code": result.light_code,
            "dark_hex": result.dark_hex,
            "light_hex": result.light_hex,
        },
        "files": {
            name: f"api/jobs/{job_id}/files/{name}"
            for name in QR_FILE_NAMES
        },
    }


@app.post("/api/generate-image")
async def generate_image_blueprint(
    image: UploadFile = File(...),
    grid_size: int = Form(48),
    color_limit: int = Form(16),
    bead_size_mm: float = Form(5.0),
    palette: str = Form("mard-221"),
) -> dict[str, object]:
    """接收普通图片并生成多色拼豆图纸；原图转换完成后立即删除。"""
    if palette not in PALETTE_FILES:
        raise HTTPException(status_code=422, detail="不支持的色号标准")

    job_id, root = job_store.create()
    upload_path = root / "upload.bin"
    result_dir = root / "result-image"
    try:
        await _save_upload(image, upload_path)
        config = ImageBlueprintConfig(
            grid_size=grid_size,
            color_limit=color_limit,
            bead_size_mm=bead_size_mm,
            palette=palette,
        )
        result = convert_image_to_blueprint(upload_path, result_dir, config)
        upload_path.unlink(missing_ok=True)
        job_store.publish(job_id, root, result_dir)
    except ValueError as exc:
        job_store.discard(job_id, root)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        job_store.discard(job_id, root)
        raise
    except Exception as exc:
        job_store.discard(job_id, root)
        raise HTTPException(status_code=500, detail="生成失败，请稍后重试") from exc
    finally:
        await image.close()

    return {
        "job_id": job_id,
        "expires_in_minutes": JOB_TTL_SECONDS // 60,
        "mode": "image",
        "warning": "当前版本会从图片中心自动裁成正方形；文字和五官靠近边缘时请先自行裁剪。",
        "summary": {
            "grid_size": result.grid_size,
            "total_beads": result.grid_size**2,
            "physical_size_cm": round(result.physical_size_mm / 10, 1),
            "color_count": len(result.materials),
            "palette_key": result.palette_key,
            "palette_title": result.palette_title,
            "materials": [
                {"code": item.code, "hex": item.hex, "count": item.count}
                for item in result.materials
            ],
        },
        "files": {
            name: f"api/jobs/{job_id}/files/{name}"
            for name in IMAGE_FILE_NAMES
        },
    }


@app.get("/api/jobs/{job_id}/files/{file_name}")
def get_generated_file(
    job_id: str,
    file_name: str,
    download: bool = Query(False),
) -> FileResponse:
    """读取白名单结果文件；PNG 可按预览或手机下载两种方式返回。"""
    if file_name not in ALLOWED_FILE_NAMES:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        job = job_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="结果不存在或已过期") from exc

    path = job.result_dir / file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    # 手机浏览器对 download 属性支持不一致，由服务端附件响应保证点击按钮后真正触发保存。
    disposition = (
        "attachment"
        if download or file_name in {"blueprint.pdf", "materials.csv"}
        else "inline"
    )
    return FileResponse(path, filename=file_name, content_disposition_type=disposition)


async def _save_upload(image: UploadFile, destination: Path) -> None:
    """流式保存并限制体积，避免公开部署后被大文件耗尽内存或磁盘。"""
    total = 0
    with destination.open("wb") as file:
        while chunk := await image.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="图片不能超过 10MB")
            file.write(chunk)
    if total == 0:
        raise HTTPException(status_code=422, detail="请选择图片")


def _convert_with_fallback(
    upload_path: Path,
    root: Path,
    beads_per_module: int,
    bead_size_mm: float,
    dark_color: str,
    light_color: str,
    palette: str,
    compatibility_mode: str,
    allow_low_contrast: bool,
):
    """优先生成干净高纠错码；自动模式失败后再兼容平台原始矩阵。"""
    modes = [compatibility_mode] if compatibility_mode != "auto" else ["clean", "preserve"]
    last_error: Exception | None = None

    for mode in modes:
        result_dir = root / f"result-{mode}"
        config = BlueprintConfig(
            beads_per_module=beads_per_module,
            bead_size_mm=bead_size_mm,
            dark_color=dark_color,
            light_color=light_color,
            palette=palette,
            allow_low_contrast=allow_low_contrast,
            preserve_source_modules=mode == "preserve",
        )
        try:
            result = convert_qr_to_blueprint(upload_path, result_dir, config)
            return result, result_dir, mode
        except QrDecodeError as exc:
            last_error = exc
            shutil.rmtree(result_dir, ignore_errors=True)

    raise QrDecodeError(str(last_error or "二维码生成失败"))


def _validate_form(platform: str, compatibility_mode: str, palette: str) -> None:
    if platform not in {"wechat", "alipay", "other"}:
        raise HTTPException(status_code=422, detail="不支持的二维码类型")
    if compatibility_mode not in {"auto", "clean", "preserve"}:
        raise HTTPException(status_code=422, detail="不支持的兼容模式")
    if palette not in PALETTE_FILES:
        raise HTTPException(status_code=422, detail="不支持的色号标准")

