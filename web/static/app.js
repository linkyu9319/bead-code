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

document.querySelectorAll('input[name="platform"]').forEach((input) => {
  input.addEventListener("change", () => {
    darkColor.value = platformColors[input.value];
    colorValue.value = platformColors[input.value];
  });
});

darkColor.addEventListener("input", () => { colorValue.value = darkColor.value.toUpperCase(); });
imageInput.addEventListener("change", () => {
  uploadTitle.textContent = imageInput.files[0]?.name || "选择二维码图片";
});

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
    uploadTitle.textContent = event.dataTransfer.files[0].name;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!imageInput.files.length) return showError("请先选择二维码图片");
  loading.classList.remove("hidden");
  resultSection.classList.add("hidden");
  try {
    const response = await fetch("api/generate", { method: "POST", body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "生成失败");
    renderResult(data);
  } catch (error) {
    showError(error.message || "网络异常，请稍后重试");
  } finally {
    loading.classList.add("hidden");
  }
});

function renderResult(data) {
  const summary = data.summary;
  document.querySelector("#blueprint-preview").src = `${data.files["blueprint.png"]}?v=${Date.now()}`;
  document.querySelector("#download-png").href = `${data.files["blueprint.png"]}?download=1`;
  document.querySelector("#download-pdf").href = data.files["blueprint.pdf"];
  document.querySelector("#download-materials").href = data.files["materials.csv"];
  document.querySelector("#stats").innerHTML = [
    [summary.grid_size + " × " + summary.grid_size, "拼豆网格"],
    [summary.physical_size_cm + " cm", "预计边长"],
    [summary.dark_beads.toLocaleString(), "深色拼豆"],
    [summary.light_beads.toLocaleString(), "浅色拼豆"],
  ].map(([value, label]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join("");
  document.querySelector("#palette-summary").innerHTML = `
    <strong>${summary.palette_title}</strong>
    <span><i style="background:${summary.dark_hex}"></i>深色 ${summary.dark_code} · ${summary.dark_hex}</span>
    <span><i style="background:${summary.light_hex}"></i>浅色 ${summary.light_code} · ${summary.light_hex}</span>
  `;
  const warning = document.querySelector("#warning");
  warning.textContent = data.warning || "";
  warning.classList.toggle("hidden", !data.warning);
  document.querySelector("#expiry").textContent = `文件将在约 ${data.expires_in_minutes} 分钟后自动清理，请及时下载。`;
  resultSection.classList.remove("hidden");
  resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showError(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 4500);
}
