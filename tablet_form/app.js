const STORAGE_KEY_API_BASE = "tabletform_api_base_url";
const STORAGE_KEY_QUEUE = "tabletform_pending_queue";
const SUBMIT_PATH = "/api/tablet-form";

const form = document.getElementById("tabletForm");
const submitBtn = document.getElementById("submitBtn");
const statusBanner = document.getElementById("statusBanner");
const queueInfo = document.getElementById("queueInfo");
const entryDateEl = document.getElementById("entryDate");
const entryTimeEl = document.getElementById("entryTime");

const settingsBtn = document.getElementById("settingsBtn");
const settingsDialog = document.getElementById("settingsDialog");
const settingsForm = document.getElementById("settingsForm");
const settingsCancel = document.getElementById("settingsCancel");
const apiBaseUrlInput = document.getElementById("apiBaseUrl");

function getApiBaseUrl() {
    return (localStorage.getItem(STORAGE_KEY_API_BASE) || "").replace(/\/+$/, "");
}

function setApiBaseUrl(url) {
    localStorage.setItem(STORAGE_KEY_API_BASE, url.replace(/\/+$/, ""));
}

function getQueue() {
    try {
        return JSON.parse(localStorage.getItem(STORAGE_KEY_QUEUE) || "[]");
    } catch (e) {
        return [];
    }
}

function setQueue(queue) {
    localStorage.setItem(STORAGE_KEY_QUEUE, JSON.stringify(queue));
    renderQueueInfo();
}

function renderQueueInfo() {
    const count = getQueue().length;
    if (count > 0) {
        queueInfo.hidden = false;
        queueInfo.textContent = `${count} submission(s) waiting to sync…`;
    } else {
        queueInfo.hidden = true;
    }
}

function showStatus(message, kind) {
    statusBanner.hidden = false;
    statusBanner.textContent = message;
    statusBanner.className = `status-banner ${kind}`;
    window.clearTimeout(showStatus._t);
    showStatus._t = window.setTimeout(() => {
        statusBanner.hidden = true;
    }, 4000);
}

function updateClock() {
    const now = new Date();
    entryDateEl.value = now.toLocaleDateString();
    entryTimeEl.value = now.toLocaleTimeString();
}
updateClock();
setInterval(updateClock, 1000);

function openSettings() {
    apiBaseUrlInput.value = getApiBaseUrl();
    settingsDialog.showModal();
}

settingsBtn.addEventListener("click", openSettings);
settingsCancel.addEventListener("click", () => settingsDialog.close());

settingsForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const url = apiBaseUrlInput.value.trim();
    if (!url) return;
    setApiBaseUrl(url);
    settingsDialog.close();
    showStatus("Server URL saved.", "ok");
});

async function postEntry(entry) {
    // Empty base = same-origin request (default when served by PCM_Tracer itself)
    const base = getApiBaseUrl();
    const response = await fetch(`${base}${SUBMIT_PATH}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry),
    });
    if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`HTTP_${response.status}: ${detail}`);
    }
    return response.json();
}

async function flushQueue() {
    let queue = getQueue();
    if (queue.length === 0) return;
    const remaining = [];
    for (const entry of queue) {
        try {
            await postEntry(entry);
        } catch (e) {
            remaining.push(entry);
        }
    }
    setQueue(remaining);
    if (remaining.length === 0 && queue.length > 0) {
        showStatus("Queued submissions synced.", "ok");
    }
}

form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submitBtn.disabled = true;

    const entry = {
        bin_location: document.getElementById("binLocation").value.trim(),
        sku_barcode: document.getElementById("skuBarcode").value.trim(),
        qty: parseInt(document.getElementById("qty").value, 10),
        picker_detail: document.getElementById("pickerDetail").value.trim(),
        shipment_number: document.getElementById("shipmentNumber").value.trim(),
    };

    try {
        await postEntry(entry);
        showStatus("Submitted successfully.", "ok");
        form.reset();
        updateClock();
        await flushQueue();
    } catch (err) {
        // Save locally and retry later (offline-friendly)
        const queue = getQueue();
        queue.push(entry);
        setQueue(queue);
        showStatus("Offline or server unreachable — saved locally, will retry.", "offline");
    } finally {
        submitBtn.disabled = false;
    }
});

window.addEventListener("online", flushQueue);
setInterval(flushQueue, 30000);
renderQueueInfo();

if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("./service-worker.js").catch(() => { });
    });
}
