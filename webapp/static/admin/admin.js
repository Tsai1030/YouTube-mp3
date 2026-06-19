const tokenInput = document.querySelector("#admin-token");
const cookiesInput = document.querySelector("#cookies");
const isBase64 = document.querySelector("#is-base64");
const cookieForm = document.querySelector("#cookie-form");
const saveButton = document.querySelector("#save-button");
const validateButton = document.querySelector("#validate-button");
const refreshStatusButton = document.querySelector("#refresh-status");
const statusLine = document.querySelector("#status");

const st = {
  loaded: document.querySelector("#st-loaded"),
  source: document.querySelector("#st-source"),
  size: document.querySelector("#st-size"),
  updated: document.querySelector("#st-updated"),
  persist: document.querySelector("#st-persist"),
  validation: document.querySelector("#st-validation"),
};

function tokenHeaders() {
  const token = tokenInput.value.trim();
  return token ? { "X-App-Token": token } : {};
}

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", isError);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "請求失敗。");
  }
  return payload;
}

function formatTime(epoch) {
  if (!epoch) {
    return "—";
  }
  return new Date(epoch * 1000).toLocaleString();
}

function renderValidation(validation) {
  if (!validation) {
    return "—";
  }
  const mark = validation.ok ? "✅" : "❌";
  return `${mark} ${validation.message || ""} (${formatTime(validation.checkedAt)})`;
}

function renderStatus(data) {
  st.loaded.textContent = data.loaded ? "是" : "否";
  st.source.textContent = data.source || "（無）";
  st.size.textContent = data.byteLength ? `${data.byteLength} bytes` : "—";
  st.updated.textContent = formatTime(data.updatedAt);
  st.persist.textContent = data.persistBackend ? data.persistBackend : "未設定（重啟會遺失）";
  st.validation.textContent = renderValidation(data.lastValidation);
}

async function loadStatus() {
  if (!tokenInput.value.trim()) {
    setStatus("請先輸入管理員 token。");
    return;
  }
  try {
    const data = await requestJson("/api/admin/cookies/status", { headers: tokenHeaders() });
    renderStatus(data);
    setStatus("狀態已更新。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

cookieForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = cookiesInput.value.trim();
  if (!content) {
    setStatus("請先貼上 cookies 內容。", true);
    return;
  }

  saveButton.disabled = true;
  setStatus("儲存並驗證中...");

  try {
    const body = isBase64.checked
      ? { cookies_base64: content }
      : { cookies_text: cookiesInput.value };
    const result = await requestJson("/api/admin/cookies", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...tokenHeaders() },
      body: JSON.stringify({ ...body, validate: true, persist: true }),
    });

    cookiesInput.value = ""; // never keep secrets in the textarea after success

    const parts = ["已儲存 cookies。"];
    if (result.persist) {
      parts.push(result.persist.persisted ? "已持久化到 Upstash。" : `未持久化：${result.persist.error}`);
    }
    if (result.validation) {
      parts.push(result.validation.ok ? "驗證通過 ✅" : `驗證失敗 ❌：${result.validation.message}`);
    }
    setStatus(parts.join(" "), result.validation && !result.validation.ok);
    await loadStatus();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    saveButton.disabled = false;
  }
});

validateButton.addEventListener("click", async () => {
  if (!tokenInput.value.trim()) {
    setStatus("請先輸入管理員 token。", true);
    return;
  }
  validateButton.disabled = true;
  setStatus("驗證中...");
  try {
    const result = await requestJson("/api/admin/cookies/validate", {
      method: "POST",
      headers: tokenHeaders(),
    });
    setStatus(result.ok ? "驗證通過 ✅" : `驗證失敗 ❌：${result.message}`, !result.ok);
    await loadStatus();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    validateButton.disabled = false;
  }
});

refreshStatusButton.addEventListener("click", loadStatus);
