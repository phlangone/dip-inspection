"use strict";
const $ = (id) => document.getElementById(id);
const page = document.body.dataset.page;
let csrf = document.querySelector('meta[name="csrf-token"]').content;
let isAdmin = false,
  products = [],
  editing = null,
  settingsLoaded = false;
let productSignature = "",
  state = {};
const labels = {
  STOPPED: "PROCESSAMENTO PARADO",
  INITIALIZING: "INICIALIZANDO",
  WAITING_PART: "AGUARDANDO PEÇA",
  OCR: "IDENTIFICANDO CI",
  INSPECTING_SIDE_A: "INSPECIONANDO LATERAL A",
  INSPECTING_SIDE_B: "INSPECIONANDO LATERAL B",
  CLASSIFYING: "CLASSIFICANDO",
  WAITING_ROBOT: "AGUARDANDO ROBÔ",
  RUNNING: "PROCESSAMENTO ATIVO",
  RECOVERING: "RECUPERANDO SOFTWARE",
  ERROR: "ERRO NO PROCESSAMENTO",
  FAULT: "INTERVENÇÃO NECESSÁRIA",
  ONLINE: "ONLINE",
  READY: "PRONTO",
  OFFLINE: "INDISPONÍVEL",
  DEGRADED: "DEGRADADO",
  CONNECTED: "CONECTADO",
  DISCONNECTED: "DESCONECTADO",
  UNAVAILABLE: "INDISPONÍVEL",
  STARTING: "INICIANDO",
  NORMAL: "OK",
  ANOMALIA: "NOK",
  IDENTIFICADO: "IDENTIFICADO",
  SEM_TEXTO: "SEM TEXTO",
  BAIXA_CONFIANCA: "BAIXA CONFIANÇA",
  AMBIGUO: "AMBÍGUO",
  PENDENTE: "PENDENTE",
};
const translated = (value) => labels[value] || value || "—";
const number = (value, digits = 0) =>
  Number(value || 0).toLocaleString("pt-BR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
const metric = (value, digits = 1) =>
  value == null ? "—" : number(value, digits);
const time = (value) =>
  value ? new Date(value * 1000).toLocaleTimeString("pt-BR") : "—";
const set = (id, text) => {
  if ($(id)) $(id).textContent = text;
};
const color = (status) =>
  [
    "ONLINE",
    "READY",
    "RUNNING",
    "CONNECTED",
    "NORMAL",
    "IDENTIFICADO",
    "APROVADO",
  ].includes(status)
    ? "success"
    : ["ERROR", "FAULT", "ANOMALIA", "REFUGO"].includes(status)
      ? "danger"
      : ["RECOVERING", "DEGRADED", "BAIXA_CONFIANCA", "AMBIGUO"].includes(
            status,
          )
        ? "warning"
        : "neutral";
function badge(id, status, text) {
  const el = $(id);
  if (el) {
    el.className = "badge " + color(status);
    el.textContent = text || translated(status);
  }
}
function notify(message, error = false) {
  const el = $("notice");
  el.hidden = false;
  el.textContent = message;
  el.className = "notice" + (error ? " error-notice" : "");
}
async function api(path, data) {
  const options =
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify(data),
        };
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      isAdmin = false;
      settingsLoaded = false;
      refreshPermissions();
    }
    throw new Error(value.error || "Operação não concluída.");
  }
  return value;
}
function refreshPermissions() {
  set("auth-button", isAdmin ? "Sair do modo admin" : "Acesso administrativo");
  document.querySelectorAll(".admin-action").forEach((el) => {
    el.disabled = !isAdmin;
    el.title = isAdmin ? "" : "Entre como administrador para usar esta ação.";
  });
}
function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function statusCell(value) {
  const td = element("td");
  td.append(element("span", translated(value), "badge " + color(value)));
  return td;
}
function renderDashboard() {
  set("part-number", state.current_part_number || "AGUARDANDO");
  set("ocr-status", translated(state.ocr_status));
  set(
    "ocr-score",
    (state.ocr_confidence == null
      ? "—"
      : number(state.ocr_confidence * 100, 1) + "%") +
      " / " +
      metric(state.pn_score),
  );
  for (const side of ["a", "b"]) {
    set(
      "side-" + side + "-status",
      translated(state["side_" + side + "_status"]),
    );
    set(
      "side-" + side + "-score",
      "Score " +
        metric(state["side_" + side + "_score"]) +
        " · limite " +
        metric(state["side_" + side + "_threshold"], 0),
    );
  }
  set("final-result", state.final_result || "Aguardando inspeção");
  set("destination", destination(state.destination));
  $("result-block").className =
    "result-block" +
    (state.final_result === "APROVADO"
      ? " approved"
      : state.final_result === "REFUGO"
        ? " rejected"
        : "");
  set(
    "presence-text",
    state.piece_present === true
      ? "Trilho · peça presente"
      : state.piece_present === false
        ? "Trilho · aguardando peça"
        : "Trilho · aguardando informação",
  );
  badge("camera-badge", state.health.camera);
  const online = state.health.camera === "ONLINE";
  $("camera-placeholder").hidden = online;
  set(
    "camera-message",
    state.station_status === "STOPPED"
      ? "Processamento parado"
      : state.health.camera === "DEGRADED"
        ? "Imagem desatualizada"
        : "Câmera indisponível",
  );
  set("camera-time", time(state.last_camera_frame));
  set("total-processed", number(state.total_processed));
  set("total-approved", number(state.total_approved));
  set("total-rejected", number(state.total_rejected));
  set("reject-rate", number(state.reject_rate, 1) + "%");
  const counts = $("product-counts");
  counts.replaceChildren();
  products
    .filter((p) => p.enabled)
    .forEach((p) => {
      const card = element("div", null, "product-count");
      const info = element("span", p.part_number);
      info.append(element("small", "Caixa " + p.destination));
      card.append(
        info,
        element(
          "strong",
          number((state.approved_count_by_part_number || {})[p.part_number]),
        ),
      );
      counts.append(card);
    });
  if (!products.some((p) => p.enabled))
    counts.append(element("p", "Nenhum produto ativo configurado.", "help"));
  const tbody = $("history-body");
  tbody.replaceChildren();
  for (const item of (state.recent_history || []).slice().reverse()) {
    const row = element("tr");
    row.append(
      element("td", time(item.timestamp)),
      element("td", item.part_number || "Indeterminado"),
      element(
        "td",
        translated(item.ocr_status) +
          " / " +
          (item.ocr_confidence == null
            ? "—"
            : number(item.ocr_confidence * 100, 1) + "%"),
      ),
      statusCell(item.side_a_status),
      statusCell(item.side_b_status),
      statusCell(item.final_result),
      element("td", destination(item.destination)),
    );
    tbody.append(row);
  }
  if (!tbody.children.length) {
    const row = element("tr"),
      cell = element("td", "Nenhuma inspeção concluída.", "empty");
    cell.colSpan = 7;
    row.append(cell);
    tbody.append(row);
  }
}
function destination(value) {
  return value === 5 ? "REFUGO" : value ? "CAIXA " + value : "—";
}
function renderProducts() {
  const tbody = $("products-body");
  tbody.replaceChildren();
  products.forEach((p, index) => {
    const row = element("tr");
    row.append(
      element("td", p.part_number),
      element("td", "Caixa " + p.destination),
      statusCell(p.enabled ? "ATIVO" : "INATIVO"),
    );
    const actions = element("td");
    for (const [action, label] of [
      ["edit", "Editar"],
      ["toggle", p.enabled ? "Desabilitar" : "Habilitar"],
      ["delete", "Excluir"],
    ]) {
      const button = element(
        "button",
        label,
        "button quiet small admin-action",
      );
      button.type = "button";
      button.dataset.productAction = action;
      button.dataset.index = index;
      actions.append(button);
    }
    row.append(actions);
    tbody.append(row);
  });
  if (!products.length) {
    const row = element("tr"),
      cell = element("td", "Nenhum produto cadastrado.", "empty");
    cell.colSpan = 4;
    row.append(cell);
    tbody.append(row);
  }
  refreshPermissions();
}
async function loadProducts() {
  const data = await api("/api/products");
  const signature = JSON.stringify(data);
  products = data.products;
  const changed = signature !== productSignature;
  productSignature = signature;
  if (page === "products" && changed) renderProducts();
}
function clearProduct() {
  editing = null;
  set("product-form-title", "Adicionar produto");
  $("product-form").reset();
  $("product-enabled").checked = true;
}
async function saveProducts(next) {
  await api("/api/products", { reject_destination: 5, products: next });
  productSignature = "";
  await loadProducts();
  notify("Produtos salvos. O worker aplicará a configuração entre ciclos.");
}
function renderSystem() {
  const names = {
    worker: "Worker de inspeção",
    camera: "Câmera USB",
    rtde: "Universal Robots · RTDE",
    mqtt: "Telemetria MQTT",
    ocr: "RapidOCR",
    presence: "Referência de presença",
    side_a: "Modelo · lateral A",
    side_b: "Modelo · lateral B",
  };
  const grid = $("health-grid");
  grid.replaceChildren();
  for (const [key, label] of Object.entries(names)) {
    const card = element("div", null, "health-item");
    card.append(
      element("span", label),
      element(
        "span",
        translated(state.health[key]),
        "badge " + color(state.health[key]),
      ),
    );
    grid.append(card);
  }
  set(
    "uptime",
    "Supervisor ativo há " +
      number(Math.floor(state.uptime_seconds / 60)) +
      " min",
  );
  set(
    "worker-pid",
    state.worker_pid ? "PID " + state.worker_pid : "Worker parado",
  );
  set("last-error", state.last_error || "Nenhum erro registrado.");
  set("last-inspection", time(state.last_inspection));
  set("last-camera", time(state.last_camera_frame));
  set("last-rtde", time(state.last_rtde_packet));
  set("last-mqtt", time(state.last_mqtt_publish));
  set(
    "restart-attempt",
    state.restart_attempt
      ? "Tentativa " + state.restart_attempt
      : "Nenhuma tentativa nesta execução",
  );
  $("safety-panel").hidden = !state.safety_latched;
}
async function loadSettings() {
  if (!isAdmin || settingsLoaded) return;
  const data = await api("/api/settings");
  const mapping = {
    "robot-host": data.robot.host,
    "robot-port": data.robot.port,
    "robot-frequency": data.robot.frequency,
    "mqtt-host": data.mqtt.host,
    "mqtt-port": data.mqtt.port,
    "mqtt-prefix": data.mqtt.topic_prefix,
    "mqtt-user": data.mqtt.username,
    "restart-limit": data.application.restart_limit,
    "restart-window": data.application.restart_window_seconds,
    "worker-timeout": data.application.worker_timeout_seconds,
    "camera-device": data.camera.device || "",
    "camera-name": data.camera.camera_name,
    "preview-fps": data.camera.preview_fps,
    "side-a-threshold": data.vision.side_a_threshold,
    "side-b-threshold": data.vision.side_b_threshold,
  };
  for (const [id, value] of Object.entries(mapping)) $(id).value = value;
  $("auto-start").checked = data.application.auto_start_worker;
  $("auto-recovery").checked = data.application.auto_recovery;
  set(
    "mqtt-password-state",
    data.mqtt.password_configured
      ? "Senha MQTT salva. Campo vazio preserva a senha."
      : "Nenhuma senha MQTT salva.",
  );
  settingsLoaded = true;
}
async function wifiStatus() {
  if (!isAdmin) return;
  const data = await api("/api/wifi");
  badge("wifi-badge", data.status);
  set(
    "wifi-current",
    data.message ||
      [data.interface, data.ssid || "Sem SSID", data.ip]
        .filter(Boolean)
        .join(" · "),
  );
  if (page === "system") {
    const card = element("div", null, "health-item");
    card.append(
      element("span", "Wi-Fi"),
      element("span", translated(data.status), "badge " + color(data.status)),
    );
    $("health-grid").append(card);
  }
  if (data.profile_uuid && $("wifi-profile"))
    $("wifi-profile").value = data.profile_uuid;
}
async function poll() {
  try {
    state = await api("/api/state");
    badge(
      "station-badge",
      state.station_status,
      translated(state.station_status),
    );
    if (page === "index") {
      await loadProducts();
      renderDashboard();
    }
    if (page === "products") await loadProducts();
    if (page === "system") {
      renderSystem();
      if (isAdmin) {
        const logs = await api("/api/system/logs");
        set("log-lines", logs.lines.join("\n") || "Nenhum evento registrado.");
        await wifiStatus();
      }
    }
    if (page === "settings" && isAdmin) {
      await loadSettings();
      await wifiStatus();
    }
  } catch (error) {
    badge("station-badge", "ERROR", "CONEXÃO COM PAINEL PERDIDA");
    notify(error.message, true);
  } finally {
    setTimeout(poll, 1000);
  }
}
$("auth-button").addEventListener("click", async () => {
  if (isAdmin) {
    try {
      const data = await api("/api/logout", {});
      csrf = data.csrf;
      isAdmin = false;
      settingsLoaded = false;
      refreshPermissions();
      if (page === "settings") {
        $("settings-form").reset();
        set("wifi-current", "Entre como administrador para consultar a rede.");
      }
      if (page === "system")
        set("log-lines", "Entre como administrador para consultar os logs.");
    } catch (error) {
      notify(error.message, true);
    }
  } else {
    $("login-dialog").showModal();
    $("admin-password").focus();
  }
});
$("cancel-login").addEventListener("click", () => $("login-dialog").close());
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/api/login", {
      password: $("admin-password").value,
    });
    csrf = result.csrf;
    isAdmin = true;
    $("admin-password").value = "";
    $("login-dialog").close();
    refreshPermissions();
    notify("Modo administrativo ativo.");
    if (page === "settings") await loadSettings();
  } catch (error) {
    set("login-error", error.message);
  }
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const confirmations = {
    stop: "Parar somente o processamento? Este botão não é uma parada de segurança do robô.",
    restart:
      "Reiniciar o processamento? Uma inspeção em andamento será invalidada. Verifique o ciclo no controlador.",
    "reset-production":
      "Apagar todos os contadores e o histórico? Esta ação não pode ser desfeita pelo painel.",
    "acknowledge-safety":
      "Você resolveu e verificou localmente a condição de segurança do robô? Isso libera somente um novo Start do software.",
  };
  if (confirmations[action] && !window.confirm(confirmations[action])) return;
  button.disabled = true;
  try {
    await api("/api/system/" + action, { confirmed: true });
    notify("Ação concluída: " + button.textContent + ".");
  } catch (error) {
    notify(error.message, true);
  } finally {
    refreshPermissions();
  }
});
if (page === "products") {
  $("cancel-product").addEventListener("click", clearProduct);
  $("product-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const item = {
      part_number: $("product-pn").value.trim().toUpperCase(),
      destination: Number($("product-box").value),
      enabled: $("product-enabled").checked,
    };
    const next = products.map((p) => ({ ...p }));
    if (editing === null) next.push(item);
    else next[editing] = item;
    try {
      await saveProducts(next);
      clearProduct();
    } catch (error) {
      notify(error.message, true);
    }
  });
  $("products-body").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-product-action]");
    if (!button) return;
    const index = Number(button.dataset.index),
      item = products[index],
      action = button.dataset.productAction;
    if (action === "edit") {
      editing = index;
      set("product-form-title", "Editar produto");
      $("product-pn").value = item.part_number;
      $("product-box").value = item.destination;
      $("product-enabled").checked = item.enabled;
      $("product-pn").focus();
      return;
    }
    if (action === "delete" && !confirm("Excluir " + item.part_number + "?"))
      return;
    const next = products.map((p) => ({ ...p }));
    if (action === "delete") next.splice(index, 1);
    else next[index].enabled = !next[index].enabled;
    try {
      await saveProducts(next);
    } catch (error) {
      notify(error.message, true);
    }
  });
}
if (page === "settings") {
  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (
      !confirm(
        "Salvar novas configurações? Elas serão aplicadas no próximo início do processamento.",
      )
    )
      return;
    const data = {
      confirmed: true,
      robot: {
        host: $("robot-host").value.trim(),
        port: Number($("robot-port").value),
        frequency: Number($("robot-frequency").value),
      },
      mqtt: {
        host: $("mqtt-host").value.trim(),
        port: Number($("mqtt-port").value),
        topic_prefix: $("mqtt-prefix").value.trim(),
        username: $("mqtt-user").value,
      },
      application: {
        auto_start_worker: $("auto-start").checked,
        auto_recovery: $("auto-recovery").checked,
        restart_limit: Number($("restart-limit").value),
        restart_window_seconds: Number($("restart-window").value),
        worker_timeout_seconds: Number($("worker-timeout").value),
      },
      camera: {
        device: $("camera-device").value.trim() || null,
        camera_name: $("camera-name").value,
        preview_fps: Number($("preview-fps").value),
      },
      vision: {
        side_a_threshold: Number($("side-a-threshold").value),
        side_b_threshold: Number($("side-b-threshold").value),
      },
    };
    if ($("mqtt-clear-password").checked) data.mqtt.password = "";
    else if ($("mqtt-password").value)
      data.mqtt.password = $("mqtt-password").value;
    try {
      await api("/api/settings", data);
      $("mqtt-password").value = "";
      $("mqtt-clear-password").checked = false;
      settingsLoaded = false;
      await loadSettings();
      notify("Configurações salvas. Inicie o processamento em Sistema.");
    } catch (error) {
      notify(error.message, true);
    }
  });
  $("mqtt-test").addEventListener("click", async () => {
    try {
      const data = await api("/api/mqtt/test", {});
      notify(data.message, !data.ok);
    } catch (error) {
      notify(error.message, true);
    }
  });
  $("wifi-scan").addEventListener("click", async () => {
    try {
      const data = await api("/api/wifi", { action: "scan" });
      const list = $("wifi-networks");
      list.replaceChildren();
      for (const row of data.networks) {
        const fields = [];
        let value = "",
          escaped = false;
        for (const c of row) {
          if (escaped) {
            value += c;
            escaped = false;
          } else if (c === "\\") {
            escaped = true;
          } else if (c === ":") {
            fields.push(value);
            value = "";
          } else value += c;
        }
        fields.push(value);
        const [ssid, signal, security] = fields;
        if (!ssid) continue;
        const button = element(
          "button",
          ssid + " · sinal " + signal + "% · " + (security || "aberta"),
        );
        button.type = "button";
        button.addEventListener("click", () => {
          $("wifi-ssid").value = ssid;
        });
        list.append(button);
      }
      if (!list.children.length)
        list.append(element("p", "Nenhuma rede encontrada.", "help"));
    } catch (error) {
      notify(error.message, true);
    }
  });
  async function changeWifi(action) {
    if (
      !confirm(
        "A conexão com esta página poderá ser perdida. Confirma a alteração do Wi-Fi?",
      )
    )
      return;
    try {
      const result = await api("/api/wifi", {
        action,
        confirmed: true,
        ssid: $("wifi-ssid").value,
        password: $("wifi-password").value,
        profile_uuid: $("wifi-profile").value,
      });
      $("wifi-password").value = "";
      if (result.profile_uuid) $("wifi-profile").value = result.profile_uuid;
      notify("Operação Wi-Fi concluída. Acesse o novo IP, se necessário.");
      await wifiStatus();
    } catch (error) {
      $("wifi-password").value = "";
      notify(error.message, true);
    }
  }
  $("wifi-connect").addEventListener("click", () => changeWifi("connect"));
  $("wifi-disconnect").addEventListener("click", () =>
    changeWifi("disconnect"),
  );
  $("wifi-reconnect").addEventListener("click", () => changeWifi("reconnect"));
}
(async () => {
  try {
    const data = await api("/api/session");
    isAdmin = data.admin;
    csrf = data.csrf;
    if (!data.configured)
      notify(
        "Dashboard disponível. Configure a senha administrativa no UNO Q para liberar os controles.",
      );
  } catch (error) {
    notify(error.message, true);
  }
  refreshPermissions();
  poll();
})();
