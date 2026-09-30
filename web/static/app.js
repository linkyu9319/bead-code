const form = document.querySelector("#generator-form");
const imageInput = document.querySelector("#image");
const uploadTitle = document.querySelector("#upload-title");
const darkColor = document.querySelector("#dark-color");
const colorValue = document.querySelector("#color-value");
const resultSection = document.querySelector("#result");
const loading = document.querySelector("#loading");
const toast = document.querySelector("#toast");
const dropZone = document.querySelector("#drop-zone");
const platformColors = { wechat: "#07C160", alipay: "#1677FF", other: "#111827" };
let activeMode = "qr";

document.querySelectorAll(".mode-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchMode(tab.dataset.mode));
});

document.querySelectorAll('input[name="platform"]').forEach((input) => {
  input.addEventListener("change", () => {
    darkColor.value = platformColors[input.value];
    colorValue.value = platformColors[input.value];
  });
});

darkColor.addEventListener("input", () => { colorValue.value = darkColor.value.toUpperCase(); });
imageInput.addEventListener("change", () => updateUploadTitle());

["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropZone.classList.add("drag");
}));
["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault();
  dropZone.classList.remove("drag");
}));
dropZone.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length) {
    imageInput.files = event.dataTransfer.files;
    updateUploadTitle();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!imageInput.files.length) {
    return showError(activeMode === "qr" ? "请先选择二维码图片" : "请先选择普通图片");
  }

  loading.classList.remove("hidden");
  resultSection.classList.add("hidden");
  try {
    const endpoint = activeMode === "qr" ? "api/generate" : "api/generate-image";
    const response = await fetch(endpoint, { method: "POST", body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "生成失败");
    renderResult(data);
  } catch (error) {
    showError(error.message || "网络异常，请稍后重试");
  } finally {
    loading.classList.add("hidden");
  }
});

function switchMode(mode) {
  activeMode = mode;
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    const selected = tab.dataset.mode === mode;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".mode-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.panel !== mode);
  });

  const isQr = mode === "qr";
  document.querySelector("#form-subtitle").textContent = isQr
    ? "静态微信、支付宝及普通二维码"
    : "头像、动漫角色、Logo 及其他图片";
  document.querySelector("#submit-label").textContent = isQr ? "生成并验证" : "生成拼豆图纸";
  document.querySelector("#loading-title").textContent = isQr
    ? "正在识别并复扫验证…"
    : "正在减色并匹配实体色号…";
  updateUploadTitle();
  resultSection.classList.add("hidden");
}

function updateUploadTitle() {
  uploadTitle.textContent = imageInput.files[0]?.name
    || (activeMode === "qr" ? "选择二维码图片" : "选择普通图片");
}

function renderResult(data) {
  const summary = data.summary;
  document.querySelector("#blueprint-preview").src = `${data.files["blueprint.png"]}?v=${Date.now()}`;
  document.querySelector("#download-png").href = `${data.files["blueprint.png"]}?download=1`;
  document.querySelector("#download-pdf").href = data.files["blueprint.pdf"];
  document.querySelector("#download-materials").href = data.files["materials.csv"];

  if (data.mode === "image") {
    renderImageSummary(summary, data.files);
  } else {
    renderQrSummary(summary);
  }

  const warning = document.querySelector("#warning");
  warning.textContent = data.warning || "";
  warning.classList.toggle("hidden", !data.warning);
  document.querySelector("#expiry").textContent = `文件将在约 ${data.expires_in_minutes} 分钟后自动清理，请及时下载。`;
  resultSection.classList.remove("hidden");
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderQrSummary(summary) {
  document.querySelector("#verified-text").textContent = "自动扫码复验通过";
  document.querySelector("#stats").innerHTML = [
    [summary.grid_size + " × " + summary.grid_size, "拼豆网格"],
    [summary.physical_size_cm + " cm", "预计边长"],
    [summary.dark_beads.toLocaleString(), "深色拼豆"],
    [summary.light_beads.toLocaleString(), "浅色拼豆"],
  ].map(([value, label]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join("");
  document.querySelector("#palette-summary").innerHTML = `
    <strong>${escapeHtml(summary.palette_title)}</strong>
    <span><i style="background:${summary.dark_hex}"></i>深色 ${escapeHtml(summary.dark_code)} · ${summary.dark_hex}</span>
    <span><i style="background:${summary.light_hex}"></i>浅色 ${escapeHtml(summary.light_code)} · ${summary.light_hex}</span>
  `;
  document.querySelector("#download-preview").classList.add("hidden");
}

function renderImageSummary(summary, files) {
  document.querySelector("#verified-text").textContent = "已完成减色和实体色号匹配";
  document.querySelector("#stats").innerHTML = [
    [summary.grid_size + " × " + summary.grid_size, "拼豆网格"],
    [summary.physical_size_cm + " cm", "预计边长"],
    [summary.total_beads.toLocaleString(), "拼豆总数"],
    [summary.color_count, "实际颜色"],
  ].map(([value, label]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join("");

  const visibleMaterials = summary.materials.slice(0, 16);
  const chips = visibleMaterials.map((item) => `
    <span><i style="background:${item.hex}"></i>${escapeHtml(item.code)} · ${item.count}</span>
  `).join("");
  const remaining = summary.materials.length - visibleMaterials.length;
  document.querySelector("#palette-summary").innerHTML = `
    <strong>${escapeHtml(summary.palette_title)} · 使用 ${summary.color_count} 色</strong>
    ${chips}${remaining > 0 ? `<span>另有 ${remaining} 色，详见材料清单</span>` : ""}
  `;

  const previewDownload = document.querySelector("#download-preview");
  previewDownload.href = `${files["preview.png"]}?download=1`;
  previewDownload.classList.remove("hidden");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function showError(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 4500);
}
