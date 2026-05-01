const els = {
  localIp: document.getElementById("localIp"),
  esp32Ip: document.getElementById("esp32Ip"),
  controlPort: document.getElementById("controlPort"),
  statusPort: document.getElementById("statusPort"),
  statusText: document.getElementById("statusText"),
  connectBtn: document.getElementById("connectBtn"),
  servo1: document.getElementById("servo1"),
  servo2: document.getElementById("servo2"),
  servo3: document.getElementById("servo3"),
  servo4: document.getElementById("servo4"),
  servo1Value: document.getElementById("servo1Value"),
  servo2Value: document.getElementById("servo2Value"),
  servo3Value: document.getElementById("servo3Value"),
  servo4Value: document.getElementById("servo4Value"),
  centerBtn: document.getElementById("centerBtn"),
  refreshServoBtn: document.getElementById("refreshServoBtn"),
  toneFreq: document.getElementById("toneFreq"),
  toneVolume: document.getElementById("toneVolume"),
  toneFreqValue: document.getElementById("toneFreqValue"),
  toneVolumeValue: document.getElementById("toneVolumeValue"),
  toneBtn: document.getElementById("toneBtn"),
  beepBtn: document.getElementById("beepBtn"),
  stopToneBtn: document.getElementById("stopToneBtn"),
  screenText: document.getElementById("screenText"),
  sendScreenTextBtn: document.getElementById("sendScreenTextBtn"),
  screenChineseBtn: document.getElementById("screenChineseBtn"),
  cameraState: document.getElementById("cameraState"),
  cameraPreview: document.getElementById("cameraPreview"),
  snapshotBtn: document.getElementById("snapshotBtn"),
  logPanel: document.getElementById("logPanel"),
};

let servoTimer = null;
let eventsWs = null;
let previewWs = null;
const servoSliders = [els.servo1, els.servo2, els.servo3, els.servo4];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function appendLog(message) {
  const lines = els.logPanel.textContent.split("\n").filter(Boolean);
  lines.push(message);
  els.logPanel.textContent = lines.slice(-120).join("\n");
  els.logPanel.scrollTop = els.logPanel.scrollHeight;
}

function setServoValues(angles) {
  servoSliders.forEach((slider, idx) => {
    const angle = angles[idx] ?? 90;
    slider.value = angle;
    document.getElementById(`servo${idx + 1}Value`).textContent = `${angle}°`;
  });
}

function bindValueLabels() {
  [els.toneFreq, els.toneVolume].forEach((input) => {
    input.addEventListener("input", () => {
      els.toneFreqValue.textContent = `${els.toneFreq.value} Hz`;
      els.toneVolumeValue.textContent = `${els.toneVolume.value}%`;
    });
  });

  servoSliders.forEach((slider, idx) => {
    slider.addEventListener("input", () => {
      document.getElementById(`servo${idx + 1}Value`).textContent = `${slider.value}°`;
      if (servoTimer) {
        clearTimeout(servoTimer);
      }
      servoTimer = setTimeout(sendServoAngles, 20);
    });
  });
}

async function sendServoAngles() {
  servoTimer = null;
  await api("/api/servo/set", {
    method: "POST",
    body: JSON.stringify({
      angles: servoSliders.map((slider) => Number(slider.value)),
    }),
  });
}

async function connectDevice() {
  const payload = {
    localIp: els.localIp.value.trim(),
    esp32Ip: els.esp32Ip.value.trim(),
    controlPort: Number(els.controlPort.value),
    statusPort: Number(els.statusPort.value),
  };
  const result = await api("/api/connect", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  applyStatusSnapshot(result.status);
}

function applyStatusSnapshot(status) {
  els.localIp.value = status.localIp;
  els.esp32Ip.value = status.esp32Ip;
  els.controlPort.value = status.controlPort;
  els.statusPort.value = status.statusPort;
  els.statusText.textContent = status.statusText;
  els.cameraState.textContent = status.cameraConnected ? "摄像头已连接" : "摄像头未连接";
  setServoValues(status.servoAngles);
  els.logPanel.textContent = (status.logs || []).join("\n");
}

function connectEventSocket() {
  if (eventsWs) {
    eventsWs.close();
  }
  eventsWs = new WebSocket(`${location.origin.replace("http", "ws")}/ws/events`);
  eventsWs.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "status_snapshot") {
      applyStatusSnapshot(payload);
    } else if (payload.type === "servo_status") {
      setServoValues(payload.angles);
      els.statusText.textContent = payload.status;
    } else if (payload.type === "log") {
      appendLog(payload.message);
      els.statusText.textContent = payload.message;
    } else if (payload.type === "camera_state") {
      els.cameraState.textContent = payload.connected ? "摄像头已连接" : "摄像头未连接";
    } else if (payload.type === "snapshot_saved") {
      appendLog(`截图已保存: ${payload.fileName}`);
      els.statusText.textContent = `截图已保存: ${payload.fileName}`;
    } else if (payload.type === "connection") {
      appendLog(`UDP 已绑定 ${payload.localIp}:${payload.statusPort}`);
    }
  };
};

function connectPreviewSocket() {
  if (previewWs) {
    previewWs.close();
  }
  previewWs = new WebSocket(`${location.origin.replace("http", "ws")}/ws/camera-preview`);
  previewWs.binaryType = "blob";
  previewWs.onmessage = (event) => {
    const url = URL.createObjectURL(event.data);
    els.cameraPreview.src = url;
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
}

function bindButtons() {
  els.connectBtn.addEventListener("click", async () => {
    try {
      await connectDevice();
    } catch (error) {
      appendLog(error.message);
      els.statusText.textContent = error.message;
    }
  });

  els.centerBtn.addEventListener("click", () =>
    api("/api/servo/center", { method: "POST", body: "{}" }).catch((error) => appendLog(error.message))
  );
  els.refreshServoBtn.addEventListener("click", () =>
    api("/api/servo/status", { method: "POST", body: "{}" }).catch((error) => appendLog(error.message))
  );

  els.toneBtn.addEventListener("click", () =>
    api("/api/audio/tone", {
      method: "POST",
      body: JSON.stringify({ frequency: Number(els.toneFreq.value), volume: Number(els.toneVolume.value) }),
    }).catch((error) => appendLog(error.message))
  );
  els.beepBtn.addEventListener("click", () =>
    api("/api/audio/tone", {
      method: "POST",
      body: JSON.stringify({
        frequency: Number(els.toneFreq.value),
        volume: Number(els.toneVolume.value),
        durationMs: 500,
      }),
    }).catch((error) => appendLog(error.message))
  );
  els.stopToneBtn.addEventListener("click", () =>
    api("/api/audio/stop", { method: "POST", body: "{}" }).catch((error) => appendLog(error.message))
  );

  document.querySelectorAll("[data-screen-command]").forEach((button) => {
    button.addEventListener("click", () =>
      api("/api/screen/command", {
        method: "POST",
        body: JSON.stringify({ command: button.dataset.screenCommand }),
      }).catch((error) => appendLog(error.message))
    );
  });

  els.sendScreenTextBtn.addEventListener("click", () =>
    api("/api/screen/command", {
      method: "POST",
      body: JSON.stringify({ command: `SCREEN TEXT ${els.screenText.value.trim()}` }),
    }).catch((error) => appendLog(error.message))
  );

  els.screenChineseBtn.addEventListener("click", () => {
    els.screenText.value = "你好，中文显示测试";
    els.sendScreenTextBtn.click();
  });

  els.snapshotBtn.addEventListener("click", async () => {
    try {
      const result = await api("/api/camera/snapshot", { method: "POST", body: "{}" });
      appendLog(`截图已保存: ${result.fileName}`);
    } catch (error) {
      appendLog(error.message);
      els.statusText.textContent = error.message;
    }
  });
}

async function init() {
  bindValueLabels();
  bindButtons();
  connectEventSocket();
  connectPreviewSocket();
  const status = await api("/api/status");
  applyStatusSnapshot(status);
}

init().catch((error) => {
  appendLog(error.message);
  els.statusText.textContent = error.message;
});
