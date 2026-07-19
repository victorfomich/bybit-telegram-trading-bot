let lastLogId = 0;
let terminalCleared = false;
let activeBot = "bot1";

function updateSyncTime() {
  const sync = document.getElementById("sync-time");
  if (!sync) return;
  sync.textContent = new Date().toLocaleString("ru-RU");
}

function setActiveBot(botId) {
  activeBot = botId;
  document.querySelectorAll(".bot-btn").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-bot") === botId);
  });
  const label = document.getElementById("active-bot-label");
  if (label) label.textContent = botId === "bot1" ? "Bot 1" : "Bot 2";
  const channel = document.getElementById("active-channel-label");
  if (channel) {
    const cid = (window.BOT_CHANNELS || {})[botId];
    channel.textContent = cid ? `канал ${cid}` : "канал не настроен в .env";
  }
  const hidden = document.getElementById("settings_bot_id");
  if (hidden) hidden.value = botId;

  const st = (window.BOT_SETTINGS || {})[botId];
  if (st) {
    const enabled = document.getElementById("enabled");
    const margin = document.getElementById("margin_usdt");
    const tp = document.getElementById("tp_adjust_pct");
    const closePct = document.getElementById("close_at_tp1_pct");
    const minLev = document.getElementById("min_leverage");
    const aiEnabled = document.getElementById("ai_enabled");
    if (enabled) enabled.checked = !!st.enabled;
    if (margin) margin.value = st.margin_usdt;
    if (tp) tp.value = st.tp_adjust_pct;
    if (closePct) closePct.value = st.close_at_tp1_pct;
    if (minLev) minLev.value = st.min_leverage ?? 1;
    if (aiEnabled) aiEnabled.checked = !!st.ai_enabled;
    const marginStat = document.getElementById("margin-stat");
    const tpStat = document.getElementById("tp-stat");
    if (marginStat) marginStat.textContent = `${st.margin_usdt} USDT`;
    if (tpStat) tpStat.textContent = `${st.tp_adjust_pct}%`;
  }

  document.querySelectorAll(".closed-row, .closed-card").forEach((row) => {
    row.style.display = row.getAttribute("data-bot") === botId ? "" : "none";
  });

  refreshOpenPositions();
  refreshLastChannelSignal(false);
  refreshNotifyStatus();
}

function initBotSwitch() {
  document.querySelectorAll(".bot-btn").forEach((btn) => {
    btn.addEventListener("click", () => setActiveBot(btn.getAttribute("data-bot")));
  });
}

function setActiveTab(tab) {
  document.querySelectorAll(".nav-btn, .mnav-btn").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-tab") === tab);
  });
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  document.getElementById(`panel-${tab}`)?.classList.add("active");

  if (tab === "terminal") refreshLogs(true);
  if (tab === "check") refreshLastChannelSignal(false);
  if (tab === "dashboard") refreshOpenPositions();
  if (tab === "settings") refreshNotifyStatus();

  window.scrollTo({ top: 0, behavior: "smooth" });
}

function initTabs() {
  document.querySelectorAll(".nav-btn, .mnav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-tab");
      setActiveTab(tab);
      if (window.matchMedia("(max-width: 900px)").matches) closeMobileMenu();
    });
  });
}

function renderNotifyStatus(status) {
  const box = document.getElementById("notify-bot-status");
  const pill = document.getElementById("notify-status");
  if (!box) return;

  const ready = status.ready;
  const linked = status.user_linked;

  if (pill) {
    pill.textContent = ready ? "OK" : linked ? "ЖДЁТ" : "OFF";
    pill.classList.toggle("on", ready);
    pill.classList.toggle("off", !ready);
  }

  box.innerHTML = `
    <div class="notify-row"><span>Бот</span><strong>${status.bot_id}</strong></div>
    <div class="notify-row"><span>Статус</span><strong>${ready ? "Всё в порядке" : status.configured ? "Не готов" : "Не настроен"}</strong></div>
    <div class="notify-row"><span>Notify бот</span><strong>${status.bot_username ? "@" + status.bot_username : "—"}</strong></div>
    <div class="notify-row"><span>Получатель</span><strong>${status.username}</strong></div>
    <div class="notify-row"><span>Привязан</span><strong>${linked ? "Да" : "Нет"}</strong></div>
    <div class="notify-message">${status.message}${status.last_error ? `<br><br>Ошибка: ${status.last_error}` : ""}</div>
  `;
}

async function refreshNotifyStatus() {
  const res = await fetch(`/api/notify-bot/status?bot_id=${activeBot}`);
  if (!res.ok) return;
  renderNotifyStatus(await res.json());
  refreshAiStatus();
}

async function refreshAiStatus() {
  const box = document.getElementById("ai-status-box");
  if (!box) return;
  const res = await fetch("/api/ai/status");
  if (!res.ok) return;
  const data = await res.json();
  const enabled = !!(data.bots || {})[activeBot];
  box.className = "notify-status-box " + (data.configured ? "ok" : "err");
  box.innerHTML = `
    <div class="notify-row"><span>OpenAI ключ</span><strong>${data.configured ? "Задан" : "Не задан"}</strong></div>
    <div class="notify-row"><span>Модель</span><strong>${data.model || "—"}</strong></div>
    <div class="notify-row"><span>ИИ для ${activeBot}</span><strong>${enabled ? "ВКЛ" : "ВЫКЛ"}</strong></div>
    <div class="notify-message">${data.message}${enabled ? ". Следит за закрытием/бу/SL." : ". Follow-up сообщения игнорируются."}</div>
  `;
}

async function testAi() {
  const box = document.getElementById("ai-status-box");
  const btn = document.getElementById("ai-test-btn");
  if (btn) btn.disabled = true;
  if (box) {
    box.className = "notify-status-box";
    box.innerHTML = '<div class="empty">Тестирую ChatGPT...</div>';
  }
  try {
    const res = await fetch("/api/ai/test", { method: "POST" });
    const data = await res.json();
    const d = data.decision || {};
    const detail = data.ok
      ? `AI работает · action=${d.action} · ${d.symbol || ""} · conf=${d.confidence ?? "?"}`
      : `AI ошибка: ${data.message || "unknown"}`;
    if (box) {
      box.className = "notify-status-box " + (data.ok ? "ok" : "err");
      box.innerHTML = `<div class="notify-message">${detail}</div>`;
    }
    alert(detail);
  } catch (e) {
    if (box) {
      box.className = "notify-status-box err";
      box.innerHTML = `<div class="notify-message">AI тест не удался: ${e}</div>`;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderLastChannelSignal(signal) {
  const wrapper = document.getElementById("last-channel-signal");
  if (!wrapper) return;

  if (!signal) {
    wrapper.innerHTML = '<div class="empty">Сигналов пока нет. Нажми «Обновить из канала».</div>';
    return;
  }

  const title = signal.is_signal
    ? `${signal.symbol} · ${signal.side} · x${signal.leverage}`
    : "Сообщение не распознано как сигнал";

  const parsed = signal.is_signal
    ? `
      <div class="data-grid">
        <div class="data-item"><span>Монета</span><strong>${signal.symbol}</strong></div>
        <div class="data-item"><span>Направление</span><strong>${signal.side}</strong></div>
        <div class="data-item"><span>Плечо</span><strong>x${signal.leverage}</strong></div>
        <div class="data-item"><span>Тип входа</span><strong>${signal.entry_kind}</strong></div>
        <div class="data-item"><span>Рынок</span><strong>${signal.entry_market ?? "—"}</strong></div>
        <div class="data-item"><span>Лимит</span><strong>${signal.entry_limit ?? "—"}</strong></div>
        <div class="data-item"><span>TP 1</span><strong>${signal.tp1}</strong></div>
        <div class="data-item"><span>TP 2</span><strong>${signal.tp2}</strong></div>
        <div class="data-item"><span>TP 3</span><strong>${signal.tp3}</strong></div>
        <div class="data-item"><span>Stop Loss</span><strong>${signal.sl}</strong></div>
      </div>`
    : `<div class="error-box">Ошибка парсинга: ${signal.parse_error || "неизвестно"}</div>`;

  wrapper.innerHTML = `
    <div class="signal-card">
      <div class="signal-top">
        <div>
          <div class="signal-title">${title}</div>
          <div class="muted">Бот: ${signal.bot_id || activeBot} · Получено: ${signal.received_at || "—"}</div>
        </div>
        <span class="badge ${signal.is_signal ? "on" : "off"}">${signal.is_signal ? "РАСПОЗНАН" : "НЕ СИГНАЛ"}</span>
      </div>
      ${parsed}
      <div class="raw-block">
        <div class="raw-label">Исходный текст</div>
        <pre>${signal.raw_text || ""}</pre>
      </div>
    </div>`;
  updateSyncTime();
}

async function refreshLastChannelSignal(force = false) {
  const endpoint = force
    ? `/api/channel/refresh?bot_id=${activeBot}`
    : `/api/channel/last-signal?bot_id=${activeBot}`;
  const options = force ? { method: "POST" } : {};
  const res = await fetch(endpoint, options);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const wrapper = document.getElementById("last-channel-signal");
    if (wrapper) wrapper.innerHTML = `<div class="empty">${data.detail || "Ошибка загрузки"}</div>`;
    return;
  }
  const data = await res.json();
  renderLastChannelSignal(data.signal);
}

function renderLogs(logs, append = true) {
  const terminal = document.getElementById("terminal");
  if (!terminal) return;

  if (!logs.length && !append) {
    terminal.innerHTML = '<div class="terminal-line muted">Логов пока нет</div>';
    return;
  }

  if (!append || terminalCleared) {
    terminal.innerHTML = "";
    terminalCleared = false;
  }

  logs.forEach((log) => {
    const line = document.createElement("div");
    line.className = `terminal-line level-${log.level}`;
    line.innerHTML = `
      <span class="time">[${log.time}]</span>
      <span class="source">${log.source}</span>
      <span class="message">${escapeHtml(log.message)}</span>`;
    terminal.appendChild(line);
    lastLogId = Math.max(lastLogId, log.id);
  });

  terminal.scrollTop = terminal.scrollHeight;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function refreshLogs(full = false) {
  const url = full || lastLogId === 0 ? "/api/logs" : `/api/logs?after=${lastLogId}`;
  const res = await fetch(url);
  if (!res.ok) return;
  const data = await res.json();
  renderLogs(data.logs || [], !full && lastLogId > 0);
}

async function refreshOpenPositions() {
  const wrapper = document.getElementById("open-positions");
  if (!wrapper) return;

  const res = await fetch(`/api/positions/open?bot_id=${activeBot}`);
  if (!res.ok) return;
  const positions = await res.json();

  const count = document.getElementById("open-count");
  if (count) count.textContent = String(positions.length);

  if (!positions.length) {
    wrapper.innerHTML = '<div class="empty">Нет открытых позиций</div>';
    return;
  }

  const cards = positions
    .map(
      (p) => `
    <div class="pos-card">
      <div class="pos-card-top">
        <div class="pos-card-sym">${p.symbol}</div>
        <div class="badge">${p.side} · x${p.leverage}</div>
      </div>
      <div class="pos-card-grid">
        <div><span>Объём</span><strong>${p.qty}${p.pending_limit_qty ? ` +L ${p.pending_limit_qty}` : ""}</strong></div>
        <div><span>Статус</span><strong>${p.status || "OPEN"}</strong></div>
        <div><span>Вход</span><strong>${p.entry_price}</strong></div>
        <div><span>PnL</span><strong class="pnl">${p.unrealized_pnl}</strong></div>
        <div><span>TP</span><strong>${p.tp_price}</strong></div>
        <div><span>SL</span><strong>${p.sl_price}</strong></div>
        <div><span>Прогноз TP</span><strong>${Number(p.tp_projection_usdt).toFixed(2)}</strong></div>
        <div><span>Прогноз SL</span><strong>${Number(p.sl_projection_usdt).toFixed(2)}</strong></div>
      </div>
    </div>`
    )
    .join("");

  wrapper.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Пара</th>
          <th>Сторона</th>
          <th>Плечо</th>
          <th>Объём</th>
          <th>Вход</th>
          <th>TP</th>
          <th>SL</th>
          <th>Статус</th>
          <th>PnL</th>
          <th>Прогноз TP</th>
          <th>Прогноз SL</th>
        </tr>
      </thead>
      <tbody>
        ${positions
          .map(
            (p) => `
          <tr>
            <td>#${p.id}</td>
            <td><strong>${p.symbol}</strong></td>
            <td>${p.side}</td>
            <td>x${p.leverage}</td>
            <td>${p.qty}${p.pending_limit_qty ? ` +лимит ${p.pending_limit_qty}@${p.pending_limit_price}` : ""}</td>
            <td>${p.entry_price}</td>
            <td>${p.tp_price}</td>
            <td>${p.sl_price}</td>
            <td>${p.status || "OPEN"}</td>
            <td class="pnl">${p.unrealized_pnl}</td>
            <td>${Number(p.tp_projection_usdt).toFixed(4)}</td>
            <td>${Number(p.sl_projection_usdt).toFixed(4)}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>
    <div class="mobile-cards">${cards}</div>`;
  updateSyncTime();
}

function showTestResult(elId, ok, text) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.style.display = "block";
  el.textContent = text;
}

const settingsForm = document.getElementById("settings-form");
if (settingsForm) {
  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      bot_id: activeBot,
      enabled: document.getElementById("enabled").checked,
      margin_usdt: Number(document.getElementById("margin_usdt").value),
      tp_adjust_pct: Number(document.getElementById("tp_adjust_pct").value),
      close_at_tp1_pct: Number(document.getElementById("close_at_tp1_pct").value),
      min_leverage: Number(document.getElementById("min_leverage").value) || 1,
      ai_enabled: document.getElementById("ai_enabled").checked,
    };

    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      window.BOT_SETTINGS[activeBot] = payload;
      const status = document.getElementById(`status-${activeBot}`);
      if (status) {
        status.textContent = payload.enabled ? "ВКЛ" : "ВЫКЛ";
        status.classList.toggle("on", payload.enabled);
        status.classList.toggle("off", !payload.enabled);
      }
      alert("Настройки сохранены");
    } else {
      alert("Ошибка сохранения");
    }
  });
}

document.getElementById("refresh-channel-btn")?.addEventListener("click", () => {
  refreshLastChannelSignal(true);
});

document.getElementById("clear-terminal-btn")?.addEventListener("click", () => {
  const terminal = document.getElementById("terminal");
  if (terminal) terminal.innerHTML = "";
  terminalCleared = true;
});

const testLastBtn = document.getElementById("test-last-signal-btn");
if (testLastBtn) {
  testLastBtn.addEventListener("click", async () => {
    testLastBtn.disabled = true;
    testLastBtn.textContent = "Открываю...";
    try {
      const res = await fetch(`/api/test-trade/from-last-signal?bot_id=${activeBot}`, { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        showTestResult("test-last-result", true, `Сделка открыта: ${data.symbol} (#${data.position_id})`);
        refreshOpenPositions();
      } else {
        showTestResult("test-last-result", false, `Ошибка: ${data.detail}`);
      }
    } catch (e) {
      showTestResult("test-last-result", false, `Ошибка сети: ${e}`);
    } finally {
      testLastBtn.disabled = false;
      testLastBtn.textContent = "Открыть по последнему сигналу";
    }
  });
}

const manualForm = document.getElementById("manual-trade-form");
if (manualForm) {
  manualForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      bot_id: activeBot,
      symbol: document.getElementById("m_symbol").value.trim(),
      side: document.getElementById("m_side").value,
      leverage: Number(document.getElementById("m_leverage").value),
      margin_usdt: Number(document.getElementById("m_margin").value),
      tp_price: Number(document.getElementById("m_tp").value),
      sl_price: Number(document.getElementById("m_sl").value),
    };
    const res = await fetch("/api/test-trade/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      showTestResult("test-manual-result", true, `Сделка открыта: ${data.symbol} (#${data.position_id})`);
      refreshOpenPositions();
    } else {
      showTestResult("test-manual-result", false, `Ошибка: ${data.detail}`);
    }
  });
}

function closeMobileMenu() {
  document.getElementById("sidebar")?.classList.remove("open");
  document.getElementById("sidebar-backdrop")?.classList.remove("open");
  document.body.classList.remove("menu-open");
}

function initMobileMenu() {
  const toggle = document.getElementById("menu-toggle");
  const backdrop = document.getElementById("sidebar-backdrop");
  const sidebar = document.getElementById("sidebar");
  if (!toggle || !sidebar) return;

  toggle.addEventListener("click", () => {
    const open = !sidebar.classList.contains("open");
    sidebar.classList.toggle("open", open);
    backdrop?.classList.toggle("open", open);
    document.body.classList.toggle("menu-open", open);
  });
  backdrop?.addEventListener("click", closeMobileMenu);
}

const _fetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _fetch(...args);
  if (res.status === 401 && !String(args[0] || "").includes("/api/login")) {
    window.location.href = "/login";
  }
  return res;
};

initMobileMenu();
initBotSwitch();
initTabs();

async function bootstrapUi() {
  try {
    for (const botId of ["bot1", "bot2"]) {
      const res = await fetch(`/api/settings?bot_id=${botId}`);
      if (!res.ok) continue;
      const data = await res.json();
      window.BOT_SETTINGS = window.BOT_SETTINGS || {};
      window.BOT_SETTINGS[botId] = {
        enabled: !!data.enabled,
        margin_usdt: data.margin_usdt,
        tp_adjust_pct: data.tp_adjust_pct,
        close_at_tp1_pct: data.close_at_tp1_pct,
        min_leverage: data.min_leverage ?? 1,
        ai_enabled: !!data.ai_enabled,
      };
    }
  } catch (e) {
    // ignore, use embedded defaults
  }
  setActiveBot("bot1");
  refreshLogs(true);
  refreshNotifyStatus();
}

bootstrapUi();

setInterval(() => {
  const dashboard = document.getElementById("panel-dashboard");
  const check = document.getElementById("panel-check");
  const terminal = document.getElementById("panel-terminal");
  const settings = document.getElementById("panel-settings");

  if (dashboard?.classList.contains("active")) refreshOpenPositions();
  if (check?.classList.contains("active")) refreshLastChannelSignal(false);
  if (terminal?.classList.contains("active")) refreshLogs(false);
  if (settings?.classList.contains("active")) refreshNotifyStatus();
  else refreshNotifyStatus(); // сайдбар статус notify обновляем всегда
}, 3000);
