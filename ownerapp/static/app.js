const app = document.querySelector("#app");
const state = {
  view: "home",
  me: null,
  demo: false,
  dash: null,
  alerts: [],
  tenants: [],
  rent: null,
  bills: [],
  billToday: "",
  progress: null,
  runs: [],
  settings: null,
  query: "",
  filter: "active",
  billQuery: "",
  billFilter: "all",
  billSort: "name",
  selected: new Set(),
  toast: "",
  error: "",
  editor: null,
  payer: null,
  history: [],
  oneCa: "",
  logOpen: false,
  runsOpen: false,
  pendingRefresh: null,
  lastAlert: 0,
  ready: null,
  users: [],
  activity: [],
  notifications: [],
  notifUnread: 0,
  notifFilter: "all",
  detailTenantId: null,
  detailPack: null,
  lastNotifPoll: 0,
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  if (response.status === 401 && path !== "/api/login" && path !== "/api/me" && path !== "/api/setup") {
    state.me = { ok: false };
    draw();
    throw new Error("Sign in");
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return data;
}

function money(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return "₹" + number.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function istParts(value) {
  if (!value) return null;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [year, month, day] = text.split("-").map(Number);
    return { day, month, year, time: "" };
  }
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return null;
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  const bag = {};
  fmt.formatToParts(date).forEach((part) => { bag[part.type] = part.value; });
  const suffix = (bag.dayPeriod || "").toUpperCase();
  return {
    day: Number(bag.day),
    month: Number(bag.month),
    year: Number(bag.year),
    time: `${bag.hour}:${bag.minute} ${suffix}`,
  };
}

function formatWhen(value, withTime) {
  const parts = istParts(value);
  if (!parts) return "";
  const date = `${parts.day} ${MONTHS[parts.month - 1]} ${parts.year}`;
  if (withTime === false || !parts.time) return date;
  return `${date}, ${parts.time}`;
}

function ago(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} hr ago`;
  return formatWhen(value, false);
}

function duration(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "";
  const total = Math.max(0, Number(seconds) || 0);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function draw() {
  if (state.me && state.me.needs_setup) {
    app.innerHTML = setupHtml();
    wireSetup();
    return;
  }
  if (!state.me || !state.me.ok) {
    app.innerHTML = loginHtml();
    wireLogin();
    return;
  }
  app.innerHTML = shellHtml();
  wireShell();
}

function setupHtml() {
  const phone = state.me && state.me.phone_url ? state.me.phone_url : "";
  return `
    <div class="login-wrap">
      <form class="login-card" id="setup">
        <h1>Bill Rent Manager</h1>
        <p class="muted">First time on this computer. Choose a PIN for user admin. This does not pay any bill. Other people are added in the users.env file, not in this app.</p>
        <div class="stack">
          <input name="owner_name" placeholder="Your name" required>
          <input class="pin" name="pin" inputmode="numeric" maxlength="8" placeholder="PIN (4 to 8 digits)" required>
          <input class="pin" name="confirm" inputmode="numeric" maxlength="8" placeholder="PIN again" required>
          <label>Email used for bill check
            <input name="email" type="email" placeholder="name@example.com" required>
          </label>
          <p class="muted">Tata Power's form marks Email Id for E-receipt as required.</p>
          <label>Mobile for the e-receipt
            <input name="mobile" inputmode="numeric" maxlength="14" placeholder="10-digit mobile" required>
          </label>
          <p class="muted">The same form asks for a mobile number for the e-receipt.</p>
          <button class="primary" type="submit">Save and open</button>
          <p class="error">${esc(state.error)}</p>
          ${phone ? `<p class="muted">On a phone, open ${esc(phone)} after allowing port 8765. Android: Chrome menu, Add to Home screen. iPhone: Safari Share, Add to Home Screen.</p>` : ""}
        </div>
      </form>
    </div>`;
}

function wireSetup() {
  document.getElementById("setup").onsubmit = async (event) => {
    event.preventDefault();
    state.error = "";
    const form = event.target;
    try {
      state.me = await api("/api/setup", {
        method: "POST",
        body: {
          owner_name: form.owner_name.value,
          pin: form.pin.value,
          confirm: form.confirm.value,
          email: form.email.value,
          mobile: form.mobile.value,
        },
      });
      state.demo = !!state.me.demo;
      await boot();
    } catch (err) {
      state.error = err.message;
      draw();
    }
  };
}

function loginHtml() {
  return `
    <div class="login-wrap">
      <form class="login-card" id="login">
        <h1>Bill Rent Manager</h1>
        <p class="muted">Rent and electricity on this computer. Enter your username and PIN.</p>
        <div class="stack">
          <input name="username" autocomplete="username" placeholder="Username" required>
          <input class="pin" name="pin" inputmode="numeric" autocomplete="current-password" maxlength="8" placeholder="PIN" required>
          <button class="primary" type="submit">Open</button>
          <p class="error">${esc(state.error)}</p>
        </div>
      </form>
    </div>`;
}

function wireLogin() {
  document.getElementById("login").onsubmit = async (event) => {
    event.preventDefault();
    state.error = "";
    try {
      await api("/api/login", {
        method: "POST",
        body: { username: event.target.username.value, pin: event.target.pin.value },
      });
      state.me = { ok: true };
      await boot();
    } catch (err) {
      state.error = err.message;
      draw();
    }
  };
}

function isAdmin() {
  return !!(state.me && state.me.role === "admin");
}

function themeValue() {
  return localStorage.getItem("brm-theme") || "light";
}

function applyTheme(theme) {
  const choice = theme || "light";
  localStorage.setItem("brm-theme", choice);
  const dark = choice === "dark" || (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#10181d" : "#0f4c5c");
}

function shellHtml() {
  const titles = {
    home: "Home",
    tenants: "Tenants",
    rent: "Rent",
    bills: "Bills",
    notifications: "Notifications",
    settings: "Settings",
  };
  const views = isAdmin()
    ? ["home", "tenants", "rent", "bills", "notifications", "settings"]
    : ["home", "tenants", "rent", "bills", "notifications"];
  const who = state.me && state.me.username ? state.me.username : "";
  return `
    <div class="shell">
      <header class="top">
        <div class="brand"><strong>Bill Rent Manager</strong><span>${titles[state.view] || "Home"}${who ? " · " + esc(who) : ""}</span></div>
        ${state.demo ? '<span class="demo-pill">Practice data</span>' : ""}
        <select id="theme-pick" class="theme-pick" aria-label="Theme">
          ${["light", "dark", "system"].map((name) => `<option value="${name}" ${themeValue() === name ? "selected" : ""}>${name[0].toUpperCase() + name.slice(1)}</option>`).join("")}
        </select>
        <button class="ghost" id="reload" type="button">Refresh</button>
        <button class="ghost" id="logout" type="button">Lock</button>
      </header>
      <nav class="nav">
        ${views.map((view) => {
          const badge = view === "notifications" && state.notifUnread
            ? `<span class="nav-badge">${state.notifUnread > 99 ? "99+" : state.notifUnread}</span>`
            : "";
          return `<button type="button" data-view="${view}" class="${state.view === view ? "on" : ""}">${titles[view]}${badge}</button>`;
        }).join("")}
      </nav>
      <main id="main">${viewHtml()}</main>
      ${state.toast ? `<div class="toast">${esc(state.toast)}</div>` : ""}
      ${state.editor ? editorHtml() : ""}
      ${state.payer ? payerHtml() : ""}
    </div>`;
}

function viewHtml() {
  if (state.detailTenantId) return tenantDetailHtml();
  if (state.view === "settings" && !isAdmin()) state.view = "home";
  if (state.view === "home") return homeHtml();
  if (state.view === "tenants") return tenantsHtml();
  if (state.view === "rent") return rentHtml();
  if (state.view === "bills") return billsHtml();
  if (state.view === "notifications") return notificationsHtml();
  return settingsHtml();
}

function homeHtml() {
  const dash = state.dash || {};
  const card = (label, count, extra) => `
    <article class="card ${extra || ""}"><span>${label}</span><b>${count ?? 0}</b></article>`;
  const alerts = state.alerts.slice(0, 12).map((item) => `
    <article class="alert"><strong>${esc(item.title)}</strong><span class="muted">${esc(item.body)}</span></article>
  `).join("") || `<p class="muted">No alerts yet.</p>`;
  const phone = dash.phone_url || (state.me && state.me.phone_url) || "";
  const phoneOn = !!(state.me && state.me.phone_access);
  const secure = window.isSecureContext;
  return `
    ${fixHtml()}
    <section class="cards">
      ${card("Rent due today", dash.rent_due_today && dash.rent_due_today.count)}
      ${card("Overdue", dash.rent_overdue && dash.rent_overdue.count)}
      ${card("Collected " + esc(dash.cycle_label || ""), money(dash.collected) || "₹0", "wide")}
      ${card("Pending " + esc(dash.cycle_label || ""), money(dash.pending) || "₹0", "wide")}
      ${card("Bills unpaid", dash.bills_unpaid && dash.bills_unpaid.count)}
      ${card("Bills due in 3 days", dash.bills_due_soon && dash.bills_due_soon.count)}
      ${card("Bill errors", dash.bill_errors && dash.bill_errors.count)}
    </section>
    <section class="section">
      <h2>Phone on this Wi-Fi</h2>
      ${phoneOn
        ? `<p class="phone-url"><strong>${esc(phone)}</strong></p>
           <p class="muted">On this computer: ${esc((state.me && state.me.local_url) || "")}</p>
           <p class="muted">The phone must be on the same Wi-Fi. Android Chrome: menu, Add to Home screen. iPhone Safari: Share, then Add to Home Screen. On Windows, double-click Allow-Phone-Access.bat once if the phone cannot connect. Away from this Wi-Fi, the phone cannot reach this computer.</p>`
        : `<p class="muted">Phone access is off. This app is open only on this computer. An admin can turn it on in Settings, then restart the app. On Windows, also double-click Allow-Phone-Access.bat once.</p>
           <p class="muted">On this computer: ${esc((state.me && state.me.local_url) || "")}</p>`}
      ${secure ? "" : `<p class="muted">This address is plain http, so the phone browser will not show system alerts or install a full offline app. The list below still works. To allow system alerts, restart on the computer with <strong>--https</strong>, accept the certificate warning on the phone, and open the https address.</p>`}
    </section>
    <section class="section">
      <h2>Alerts</h2>
      <div class="tools">
        <button class="primary" id="enable-notes" type="button">Alerts on this phone</button>
      </div>
      <p class="muted">These stay in the app. A phone away from this Wi-Fi sees them the next time it opens Bill Rent Manager here. There is no WhatsApp and no SMS.</p>
      ${alerts}
    </section>`;
}

function tenantsHtml() {
  const q = state.query.toLowerCase();
  const rows = state.tenants.filter((row) => {
    if (state.filter === "active" && !row.active) return false;
    if (state.filter === "inactive" && row.active) return false;
    if (state.filter === "ca" && !row.ca_no) return false;
    if (state.filter === "norent" && row.rent) return false;
    const blob = `${row.name} ${row.room} ${row.phone} ${row.ca_no}`.toLowerCase();
    return !q || blob.includes(q);
  });
  return `
    <div class="tools">
      <input class="grow" id="search" placeholder="Search name, room, phone, CA" value="${esc(state.query)}">
      <button class="primary" id="add-tenant" type="button">Add</button>
    </div>
    <div class="tools">
      <button class="ghost" id="template-btn" type="button">Template</button>
      <button class="ghost" id="import-btn" type="button">Import</button>
      <button class="ghost" id="export-btn" type="button">Excel</button>
      <input id="import-file" type="file" accept=".csv,.xlsx" hidden>
    </div>
    <div class="chips">
      ${["active", "all", "inactive", "ca", "norent"].map((key) =>
        `<button type="button" data-filter="${key}" class="${state.filter === key ? "on" : ""}">${filterLabel(key)}</button>`
      ).join("")}
    </div>
    <p class="muted">${rows.length} shown</p>
    ${rows.map(tenantCard).join("") || `<p class="muted">No tenants yet. Import the CSV or add one.</p>`}
    ${importReportHtml()}`;
}

function importReportHtml() {
  if (!state.importReport) return "";
  const rows = state.importReport.report || [];
  return `
    <section class="section panel" style="padding:12px">
      <h2>Import report</h2>
      <p class="muted">Added ${state.importReport.added}, updated ${state.importReport.updated}, skipped ${state.importReport.skipped || 0}, rejected ${state.importReport.rejected || 0}.</p>
      ${rows.map((row) => `<p>${esc(row.action)}: ${esc(row.name)} ${row.issues && row.issues.length ? "- " + esc(row.issues.join(" ")) : ""}</p>`).join("")}
      <div class="tools">
        ${state.importReport.preview ? `<button class="primary" id="import-commit" type="button">Save these rows</button>` : ""}
        <label>Duplicates <select id="dup-mode">
          <option value="update">Update existing</option>
          <option value="skip">Skip existing</option>
        </select></label>
      </div>
    </section>`;
}

function filterLabel(key) {
  return { active: "Active", all: "All", inactive: "Inactive", ca: "Has CA", norent: "No rent" }[key];
}

function rentHtml() {
  const pack = state.rent || { rows: [], cycle: "" };
  return `
    <div class="tools">
      <button class="ghost" id="prev-month" type="button">Prev</button>
      <strong>${esc(pack.cycle)}</strong>
      <button class="ghost" id="next-month" type="button">Next</button>
    </div>
    <section class="cards slim">
      <article class="card"><span>Collected</span><b>${money(pack.collected) || "₹0"}</b></article>
      <article class="card"><span>Pending</span><b>${money(pack.pending) || "₹0"}</b></article>
    </section>
    <p class="muted">Green is paid. Orange is due today or within 2 days. Red is overdue. ${esc(pack.paid_count || 0)} paid, ${esc(pack.open_count || 0)} still open.</p>
    ${(pack.rows || []).map((row) => `
      <article class="row-card tone-${esc(row.tone)}">
        <div class="stripe"></div>
        <div>
          <div class="who">${esc(row.name || row.room || "Tenant")}</div>
          <div class="muted">${esc([row.room, money(row.rent) && money(row.rent), row.due_date && "due " + formatWhen(row.due_date, false)].filter(Boolean).join(" · "))}</div>
          <div class="tag ${esc(row.tone)}">${esc(row.tone_label)}${row.paid_mode ? " · " + esc(row.paid_mode) : ""}${row.paid_on ? " · " + esc(formatWhen(row.paid_on, false)) : ""}</div>
        </div>
        <div class="actions">
          <button class="primary" data-pay="${row.id}" type="button">${row.paid ? "Edit" : "Mark paid"}</button>
          ${row.paid ? `<button class="ghost" data-unpay="${row.id}" type="button">Unpaid</button>` : ""}
        </div>
      </article>`).join("")}
  `;
}

function stepLabel(item, tries) {
  if (item.step === "captcha") {
    const n = state.progress && state.progress.captcha_try;
    return n ? `Solving captcha (try ${n}/${tries || 6})` : "Solving captcha";
  }
  return {
    waiting: "Waiting",
    opening: "Opening site",
    reading: "Reading bill",
    done: "Done",
    error: "Error",
    stopped: "Stopped",
  }[item.step] || item.step || "Waiting";
}

function billsHtml() {
  const progress = state.progress || {};
  const percent = progress.percent || 0;
  const running = progress.state === "Running" || progress.state === "Queued";
  const tries = progress.captcha_tries || 6;
  const items = progress.items || [];
  const needsContact = state.settings && !state.settings.receipt_ready && !state.demo;
  const blocked = state.ready && !state.ready.ok && !state.demo;
  return `
    ${fixHtml()}
    ${needsContact ? contactHtml() : ""}
    <section class="panel progress" id="live-panel">
      <div class="live-head">
        <strong class="check-${esc(progress.state || "Idle")}">${esc(progress.state || "Idle")}</strong>
        <span class="muted">${running ? esc(duration(progress.elapsed_seconds)) : ""}${progress.eta_seconds ? " · about " + esc(duration(progress.eta_seconds)) + " left" : ""}</span>
      </div>
      <div class="bar"><i style="width:${percent}%"></i></div>
      ${progress.current_ca ? `<div><strong>${esc(progress.current_name || "CA")}</strong> <span class="muted">${esc(progress.current_ca)}</span></div>` : ""}
      <div class="muted">${esc(progress.summary || progress.message || "")}</div>
      ${items.length ? `<ul class="queue">${items.map((item) => `
        <li class="step-${esc(item.step)}">
          <span>${esc(item.name || item.ca_no)} · ${esc(item.ca_no)}</span>
          <b>${esc(stepLabel(item, tries))}</b>
          ${item.plain ? `<p>${esc(item.plain)} ${esc(item.fix || "")}</p>` : ""}
          ${item.step === "error" ? `<button class="ghost" data-retry-ca="${esc(item.ca_no)}" type="button">Retry this</button>` : ""}
        </li>`).join("")}</ul>` : ""}
      <div class="tools">
        ${running ? `<button class="danger" id="stop-run" type="button">Stop</button>` : ""}
        <button class="ghost" id="retry-errors" type="button">Retry all errors</button>
        <button class="ghost" id="toggle-log" type="button">${state.logOpen ? "Hide log" : "Run log"}</button>
        <button class="ghost" id="toggle-runs" type="button">${state.runsOpen ? "Hide history" : "Run history"}</button>
      </div>
      ${state.logOpen ? `<pre class="log">${esc((progress.log || []).join("\n") || "No lines yet.")}</pre>` : ""}
      ${state.runsOpen ? runsHtml() : ""}
    </section>
    <div class="tools" id="bill-actions">
      <button class="primary" data-refresh="all" type="button" ${blocked ? "disabled" : ""}>All</button>
      <button class="ghost" data-refresh="unpaid" type="button" ${blocked ? "disabled" : ""}>Unpaid</button>
      <button class="ghost" data-refresh="error" type="button" ${blocked ? "disabled" : ""}>Errors</button>
      ${state.selected.size && !blocked ? "" : `<button class="ghost" data-refresh="selected" type="button" ${blocked ? "disabled" : ""}>Refresh selected</button>`}
    </div>
    ${state.selected.size && !blocked ? `<div class="sticky-bar"><button class="primary" id="sticky-selected" type="button">Refresh selected (${state.selected.size})</button></div>` : ""}
    <section class="panel one-ca">
      <h2>Check one CA number</h2>
      <p class="muted">Type an 11-digit CA, or use Refresh on a card below.</p>
      <div class="tools">
        <input id="one-ca" class="grow" inputmode="numeric" placeholder="11-digit CA" value="${esc(state.oneCa)}">
        <button class="primary" id="one-refresh" type="button">Check this CA</button>
      </div>
    </section>
    <div class="tools">
      <input class="grow" id="bill-search" placeholder="Search name or CA" value="${esc(state.billQuery)}">
      <select id="bill-sort">
        <option value="name" ${state.billSort === "name" ? "selected" : ""}>Name</option>
        <option value="due" ${state.billSort === "due" ? "selected" : ""}>Due date</option>
        <option value="amount" ${state.billSort === "amount" ? "selected" : ""}>Amount</option>
      </select>
    </div>
    <div class="chips">
      ${["all", "unpaid", "paid", "due", "error"].map((key) =>
        `<button type="button" data-bill-filter="${key}" class="${state.billFilter === key ? "on" : ""}">${billFilterLabel(key)}</button>`
      ).join("")}
    </div>
    ${visibleBills().map(billCard).join("") || `<p class="muted">No bills match. Add a CA number on a tenant first.</p>`}
  `;
}

function fixHtml() {
  const report = state.ready;
  if (!report || report.ok || state.demo) return "";
  const rows = (report.checks || []).filter((item) => !item.ok).map((item) => `
    <li><strong>${esc(item.name)}</strong> ${esc(item.detail)} ${esc(item.fix || "")}</li>
  `).join("");
  return `
    <section class="panel fix-page">
      <h2>Fix this before checking bills</h2>
      <p>${esc(report.headline || "Something the bill check needs is not ready.")}</p>
      <ul>${rows}</ul>
      <p class="muted">Nothing was sent to Tata Power. After the fix, restart the app and open this page again.</p>
    </section>`;
}

function contactHtml() {
  const s = state.settings || {};
  if (!isAdmin()) {
    return `
      <section class="panel contact-banner">
        <h2>Email and mobile for the bill check</h2>
        <p>An admin needs to save the email and the 10-digit mobile before a bill check can start. Nothing is paid.</p>
      </section>`;
  }
  return `
    <form class="panel contact-banner" id="contact-gate">
      <h2>Email and mobile for the bill check</h2>
      <p>Tata Power's form needs these before a CA can be opened. The email field is marked required. The mobile is for the e-receipt. Nothing is paid.</p>
      <label>Enter email used for bill check
        <input name="receipt_email" type="email" value="${esc(s.receipt_email || "")}" placeholder="name@example.com" required>
      </label>
      <label>Mobile for the e-receipt
        <input name="receipt_mobile" inputmode="numeric" value="${esc(s.receipt_mobile || "")}" placeholder="10-digit mobile" required>
      </label>
      <button class="primary" type="submit">Save and continue</button>
    </form>`;
}

function runsHtml() {
  if (!state.runs.length) return `<p class="muted">No finished runs yet.</p>`;
  return state.runs.map((run) => `
    <details>
      <summary>${esc(formatWhen(run.finished_at) || run.finished_at)} · ${esc(run.summary)}</summary>
      <pre class="log">${esc(run.log_text || "")}</pre>
    </details>`).join("");
}

function billFilterLabel(key) {
  return { all: "All", unpaid: "Unpaid", paid: "Paid", due: "Due soon", error: "Error" }[key];
}

function billChip(row) {
  const status = String(row.status || "");
  const check = String(row.check_state || "");
  if (check === "Error" || status.startsWith("Error")) return "Error";
  if (status === "Paid") return "Paid";
  if (status !== "Unpaid") return "";
  if (!row.due_date) return "Unpaid";
  const today = state.billToday || new Date().toISOString().slice(0, 10);
  if (row.due_date < today) return "Overdue";
  const due = new Date(row.due_date + "T00:00:00");
  const now = new Date(today + "T00:00:00");
  if ((due - now) / 86400000 <= 3) return "Due soon";
  return "Unpaid";
}

function visibleBills() {
  const q = state.billQuery.toLowerCase();
  const rows = state.bills.filter((row) => {
    const chip = billChip(row);
    if (state.billFilter === "unpaid" && row.status !== "Unpaid") return false;
    if (state.billFilter === "paid" && row.status !== "Paid") return false;
    if (state.billFilter === "error" && chip !== "Error") return false;
    if (state.billFilter === "due" && chip !== "Due soon" && chip !== "Overdue") return false;
    const blob = `${row.name} ${row.ca_no} ${row.room}`.toLowerCase();
    return !q || blob.includes(q);
  });
  rows.sort((a, b) => {
    if (state.billSort === "due") return String(a.due_date || "9999").localeCompare(String(b.due_date || "9999"));
    if (state.billSort === "amount") return (b.amount || 0) - (a.amount || 0);
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  return rows;
}

function fact(label, value) {
  if (value === null || value === undefined || value === "") return "";
  return `<div class="muted">${esc(label)} ${esc(value)}</div>`;
}

function kvRow(label, value, big) {
  if (value === null || value === undefined || value === "") return "";
  return `<div class="kv-row"><span class="kv-label">${esc(label)}</span><span class="kv-value${big ? " big" : ""}">${esc(value)}</span></div>`;
}

function daysLabel(days) {
  if (days === null || days === undefined || days === "") return "";
  const n = Number(days);
  if (!Number.isFinite(n)) return "";
  if (n < 0) return `${Math.abs(n)} day(s) overdue`;
  if (n === 0) return "Due today";
  return `${n} day(s) left`;
}

function rentChipFromRow(row) {
  if (!row) return "";
  return row.tone_label || (row.paid ? "Paid" : "Open");
}

function billForTenant(tenant) {
  if (!tenant || !tenant.ca_no) return null;
  return state.bills.find((row) => row.ca_no === tenant.ca_no) || null;
}

function tenantCard(row) {
  const bill = billForTenant(row);
  const chip = bill ? billChip(bill) : "";
  const rentRow = state.rent && state.rent.rows
    ? state.rent.rows.find((item) => item.id === row.id)
    : null;
  const rentStatus = rentChipFromRow(rentRow);
  const rentTone = rentRow ? rentRow.tone : "none";
  return `
    <article class="detail-card row-card tone-${esc(rentTone)}">
      <div class="stripe"></div>
      <div class="detail-body">
        <div class="who">${esc(row.name || "No name")}</div>
        <div class="muted">${esc([row.room, row.phone].filter(Boolean).join(" · "))}</div>
        <section class="detail-section">
          <h3>Rent</h3>
          ${kvRow("Monthly", money(row.rent))}
          ${kvRow("Due day", row.due_day || "")}
          ${kvRow("This month", rentStatus)}
          ${rentRow && rentRow.paid_on ? kvRow("Paid on", formatWhen(rentRow.paid_on, false)) : ""}
        </section>
        <section class="detail-section">
          <h3>Electricity bill</h3>
          ${row.ca_no ? kvRow("CA", row.ca_no) : `<p class="muted">No CA number</p>`}
          ${bill ? `${chip ? `<span class="chip chip-${esc(chip.toLowerCase().replace(/ /g, "-"))}">${esc(chip)}</span>` : ""}
          ${kvRow("Outstanding", money(bill.amount), true)}
          ${kvRow("Due", bill.due_date ? formatWhen(bill.due_date, false) : "")}
          ${kvRow("Exact bill", money(bill.exact_bill))}` : ""}
        </section>
      </div>
      <div class="actions stack-actions">
        <button class="ghost" data-open-tenant="${row.id}" type="button">Open</button>
        ${row.ca_no ? `<button class="primary" data-refresh-ca="${esc(row.ca_no)}" type="button">Refresh</button>` : ""}
        <button class="ghost" data-edit="${row.id}" type="button">Edit</button>
      </div>
    </article>`;
}

function notificationsHtml() {
  const filters = [
    ["all", "All"],
    ["rent", "Rent"],
    ["electricity", "Electricity"],
    ["errors", "Errors"],
  ];
  const items = (state.notifications || []).filter((item) => {
    if (state.notifFilter === "rent") return item.kind === "rent";
    if (state.notifFilter === "electricity") return item.kind === "electricity" && item.category !== "bill_error";
    if (state.notifFilter === "errors") return item.category === "bill_error";
    return true;
  });
  const groups = {};
  items.forEach((item) => {
    const key = item.title || item.category;
    if (!groups[key]) groups[key] = [];
    groups[key].push(item);
  });
  const body = Object.keys(groups).length
    ? Object.entries(groups).map(([title, rows]) => `
      <section class="notif-group">
        <h2>${esc(title)}</h2>
        ${rows.map((item) => notifCard(item)).join("")}
      </section>`).join("")
    : `<p class="muted">Nothing urgent right now.</p>`;
  return `
    <div class="tools">
      <button class="ghost" id="notif-enable" type="button">Browser alerts</button>
      <button class="ghost" id="notif-read-all" type="button">Mark all read</button>
    </div>
    <div class="chips">
      ${filters.map(([key, label]) =>
        `<button type="button" data-notif-filter="${key}" class="${state.notifFilter === key ? "on" : ""}">${label}</button>`
      ).join("")}
    </div>
    ${body}`;
}

function notifCard(item) {
  const days = daysLabel(item.days);
  const unread = !item.read;
  return `
    <article class="detail-card notif-card ${unread ? "unread" : ""}">
      <div class="detail-body">
        <div class="who">${esc(item.name)} <span class="muted">${esc(item.room)}</span></div>
        <p class="muted">${esc(item.body || "")}</p>
        ${kvRow("Amount", money(item.amount), true)}
        ${kvRow("Due", item.due_date ? formatWhen(item.due_date, false) : "")}
        ${days ? kvRow("Timing", days) : ""}
      </div>
      <div class="actions stack-actions">
        ${item.kind === "rent" ? `<button class="primary" data-notif-pay="${item.tenant_id}" type="button">Mark rent paid</button>` : ""}
        ${item.ca_no ? `<button class="ghost" data-notif-refresh="${esc(item.ca_no)}" type="button">Refresh bill</button>` : ""}
        <button class="ghost" data-open-tenant="${item.tenant_id}" type="button">Open tenant</button>
        ${unread ? `<button class="ghost" data-notif-read="${esc(item.id)}" type="button">Mark read</button>` : ""}
      </div>
    </article>`;
}

function tenantDetailHtml() {
  const pack = state.detailPack || {};
  const row = pack.tenant || {};
  const bill = pack.bill;
  const chip = bill ? billChip(bill) : "";
  const rent = pack.rent_this_month || {};
  const rentHist = (pack.rent_history || []).map((item) =>
    `<p class="muted">${esc(item.cycle)} · ${esc(item.status)} · ${esc(money(item.amount) || "")} ${item.paid_on ? "· " + esc(formatWhen(item.paid_on, false)) : ""}</p>`
  ).join("") || `<p class="muted">No rent history.</p>`;
  const billHist = (pack.bill_history || []).slice(0, 20).map((item) =>
    `<p class="muted">${esc(formatWhen(item.checked_at))} · ${esc(item.status || "")} · ${esc(money(item.amount) || "")} ${item.due_date ? "· due " + esc(formatWhen(item.due_date, false)) : ""}</p>`
  ).join("") || `<p class="muted">No bill checks saved yet.</p>`;
  return `
    <div class="tools sticky-head">
      <button class="ghost" id="detail-back" type="button">Back</button>
      <strong>${esc(row.name || "Tenant")}</strong>
    </div>
    <article class="detail-card">
      <div class="detail-body">
        <div class="muted">${esc([row.room, row.phone, row.ca_no ? "CA " + row.ca_no : ""].filter(Boolean).join(" · "))}</div>
        <section class="detail-section">
          <h3>Rent</h3>
          ${kvRow("Monthly", money(row.rent))}
          ${kvRow("Due day", row.due_day || "")}
          ${kvRow("This month", rent.tone_label || "")}
          ${kvRow("Paid", rent.paid_amount != null ? money(rent.paid_amount) : "")}
          <h4>History</h4>
          ${rentHist}
        </section>
        <section class="detail-section">
          <h3>Electricity bill</h3>
          ${bill ? `${chip ? `<span class="chip chip-${esc(chip.toLowerCase().replace(/ /g, "-"))}">${esc(chip)}</span>` : ""}
          ${kvRow("Outstanding", money(bill.amount), true)}
          ${kvRow("Exact bill", money(bill.exact_bill), true)}
          ${kvRow("Due", bill.due_date ? formatWhen(bill.due_date, false) : "")}
          ${kvRow("Consumer", bill.consumer_name || "")}
          ${kvRow("Bill", [bill.bill_no, bill.bill_date ? formatWhen(bill.bill_date, false) : ""].filter(Boolean).join(" · "))}
          ${bill.note ? `<p class="muted">${esc(bill.note)}</p>` : ""}` : `<p class="muted">No bill on file. Add a CA and refresh.</p>`}
          <h4>Check history</h4>
          ${billHist}
        </section>
        ${row.notes ? `<section class="detail-section"><h3>Notes</h3><p>${esc(row.notes)}</p></section>` : ""}
      </div>
      <div class="actions detail-actions">
        ${rent.tone !== "paid" && row.active ? `<button class="primary" id="detail-pay" type="button">Mark rent paid</button>` : ""}
        ${row.ca_no ? `<button class="primary" id="detail-refresh" type="button">Refresh bill</button>` : ""}
        <button class="ghost" id="detail-edit" type="button">Edit tenant</button>
      </div>
    </article>`;
}

function billCard(row) {
  const chip = billChip(row);
  const checked = row.checked_at ? ago(row.checked_at) : "";
  const tenant = state.tenants.find((item) => item.ca_no === row.ca_no);
  const tenantId = tenant ? tenant.id : "";
  return `
    <article class="detail-card row-card">
      <div class="stripe"></div>
      <div class="detail-body">
        <label class="who-line"><input type="checkbox" data-ca="${esc(row.ca_no)}" ${state.selected.has(row.ca_no) ? "checked" : ""}> <span class="who">${esc(row.name)}</span></label>
        <div class="muted">${esc([row.room, "CA " + row.ca_no].filter(Boolean).join(" · "))}</div>
        <section class="detail-section">
          <h3>Electricity bill</h3>
          ${chip ? `<span class="chip chip-${esc(chip.toLowerCase().replace(/ /g, "-"))}">${esc(chip)}</span>` : ""}
          ${kvRow("Outstanding", money(row.amount), true)}
          ${kvRow("Exact bill", money(row.exact_bill), true)}
          ${kvRow("Due", row.due_date ? formatWhen(row.due_date, false) : "")}
          ${kvRow("Consumer", row.consumer_name || "")}
          ${kvRow("Bill no / date", [row.bill_no, row.bill_date ? formatWhen(row.bill_date, false) : ""].filter(Boolean).join(" · "))}
          ${kvRow("Last payment", [money(row.last_payment), row.last_paid_on ? formatWhen(row.last_paid_on, false) : ""].filter(Boolean).join(" on "))}
          ${checked ? `<p class="muted">Checked ${esc(checked)}</p>` : ""}
          ${row.note ? `<p class="muted">${esc(row.note)}</p>` : ""}
        </section>
      </div>
      <div class="actions stack-actions">
        ${tenantId ? `<button class="ghost" data-open-tenant="${tenantId}" type="button">Open</button>` : ""}
        <button class="primary" data-refresh-ca="${esc(row.ca_no)}" type="button">Refresh</button>
        <button class="ghost" data-diagnose-ca="${esc(row.ca_no)}" type="button">Diagnose</button>
      </div>
    </article>`;
}

function settingsHtml() {
  const s = state.settings || {};
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  return `
    <form class="panel stack" id="settings-form" style="padding:16px">
      <label>Email used for bill check<input name="receipt_email" type="email" value="${esc(s.receipt_email || "")}" required></label>
      <p class="muted">Tata Power marks Email Id for E-receipt as required. The mobile is the e-receipt number on the same form.</p>
      <label>Mobile for the e-receipt<input name="receipt_mobile" inputmode="numeric" value="${esc(s.receipt_mobile || "")}" required></label>
      <label>Seconds between CA numbers<input name="request_delay" value="${esc(s.request_delay || 3)}"></label>
      <label>Captcha tries (1 to 12)<input name="captcha_tries" value="${esc(s.captcha_tries || 6)}"></label>
      <label>Bill check mode
        <select name="bill_check_mode">
          ${["auto", "fast", "browser"].map((name) => `<option value="${name}" ${(s.bill_check_mode || "auto") === name ? "selected" : ""}>${name === "auto" ? "Auto (Fast, then Browser on failure)" : name === "fast" ? "Fast" : "Browser"}</option>`).join("")}
        </select>
      </label>
      <p class="muted">Bill checks run one CA at a time. The delay above is the wait between CA numbers (at least 2 seconds). Auto tries the fast HTTP check first, then Microsoft Edge if that fails. Browser needs Playwright and Edge installed. Nothing is paid.</p>
      <label><input type="checkbox" name="auto_refresh" ${s.auto_refresh ? "checked" : ""}> Weekly auto-refresh while this app is open</label>
      <label>Weekday
        <select name="auto_weekday">
          ${days.map((name, index) => `<option value="${index}" ${Number(s.auto_weekday) === index ? "selected" : ""}>${name}</option>`).join("")}
        </select>
      </label>
      <label>Time<input name="auto_time" value="${esc(s.auto_time || "09:00")}"></label>
      <label>Theme
        <select name="theme">
          ${["light", "dark", "system"].map((name) => `<option value="${name}" ${themeValue() === name ? "selected" : ""}>${name[0].toUpperCase() + name.slice(1)}</option>`).join("")}
        </select>
      </label>
      <button class="primary" type="submit">Save</button>
      <p class="muted">Auto-refresh runs only while the app window is open and the computer is on. Monday 9:00 is the default. The app never clicks Pay. Light is the default theme. System follows this computer.</p>
    </form>
    <section class="panel stack" style="padding:16px;margin-top:12px">
      <h2>Phone on this Wi-Fi</h2>
      <p class="muted">Off until you turn it on and restart. The app then listens for phones on this network. Anyone on the Wi-Fi who knows a username and PIN can open it. Setup, backup, and restore stay on this computer.</p>
      <p id="phone-state">${state.me && state.me.phone_access ? "Phone access is on. Restart if you just changed it." : "Phone access is off."}</p>
      <button class="primary" id="phone-toggle" type="button">${state.me && state.me.phone_access ? "Turn phone access off" : "Turn phone access on"}</button>
    </section>
    <section class="panel stack" style="padding:16px;margin-top:12px">
      <h2>People who can sign in</h2>
      <p>Accounts are not created in this app. Open <strong>users.env</strong> in Notepad, add or delete a line, save, then restart the app.</p>
      <p class="muted">${esc((state.me && state.me.users_file) || "users.env")}</p>
      <pre class="log">admin=123456
ramesh=4821
priya=9090:admin</pre>
      <p class="muted">The first line is an admin. Other lines are staff unless they end with :admin. Type a PIN of 4 to 8 digits. The app replaces it with a hash and does not keep the digits. Do not use 0000, 1234, or a repeated digit. A staff member can view, refresh bills, mark rent paid, and add or edit tenants. A staff member cannot open Settings, delete a tenant, or restore a backup. At most 10 users.</p>
    </section>
    <section class="panel stack" style="padding:16px;margin-top:12px">
      <h2>Activity</h2>
      ${(state.activity || []).slice(0, 40).map((item) => `<p class="muted">${esc(item.created_at)} · ${esc(item.username)} · ${esc(item.action)}${item.detail ? " · " + esc(item.detail) : ""}</p>`).join("") || `<p class="muted">No activity yet.</p>`}
    </section>
    <section class="panel stack" style="padding:16px;margin-top:12px">
      <h2>Backup</h2>
      <p class="muted">The database file is the whole list. Copy it somewhere safe, or put a previous file back.</p>
      <div class="tools">
        <button class="primary" id="backup-btn" type="button">Download backup</button>
        <button class="ghost" id="restore-btn" type="button">Restore</button>
        <input id="restore-file" type="file" accept=".db" hidden>
      </div>
    </section>`;
}

function editorHtml() {
  const row = state.editor;
  const blank = !row.id;
  return `
    <div class="sheet" id="sheet">
      <form class="panel stack" id="tenant-form">
        <h2>${blank ? "Add tenant" : "Edit tenant"}</h2>
        <input name="name" placeholder="Name" value="${esc(row.name || "")}">
        <input name="room" placeholder="Room / flat" value="${esc(row.room || "")}">
        <input name="phone" placeholder="Phone" value="${esc(row.phone || "")}">
        <input name="rent" placeholder="Monthly rent" value="${esc(row.rent ?? "")}">
        <input name="due_day" placeholder="Due day (18 or 18TH)" value="${esc(row.due_day ?? "")}">
        <input name="ca_no" placeholder="11-digit CA" value="${esc(row.ca_no || "")}">
        <textarea name="notes" placeholder="Notes">${esc(row.notes || "")}</textarea>
        <label><input type="checkbox" name="active" ${row.active === false ? "" : "checked"}> Active</label>
        <button class="primary" type="submit">Save</button>
        ${blank || !isAdmin() ? "" : `<button class="danger" id="delete-tenant" type="button">Delete</button>`}
        <button class="ghost" id="close-sheet" type="button">Close</button>
      </form>
    </div>`;
}

function payerHtml() {
  const row = state.payer;
  const history = state.history.map((item) => `
    <p class="muted">${esc(item.cycle)} · ${esc(item.status)} · ${esc(money(item.amount) || "")} ${item.paid_on ? "· " + esc(formatWhen(item.paid_on, false)) : ""} ${item.mode ? "· " + esc(item.mode) : ""}</p>
  `).join("");
  return `
    <div class="sheet">
      <form class="panel stack" id="pay-form">
        <h2>Mark paid · ${esc(row.name || "tenant")}</h2>
        <input name="amount" placeholder="Amount" value="${esc(row.paid_amount ?? row.rent ?? "")}">
        <input name="paid_on" type="date" value="${esc((row.paid_on || (state.dash && state.dash.today) || "").slice(0, 10))}">
        <select name="mode">
          ${["Cash", "UPI", "Bank", "Other"].map((mode) => `<option ${row.paid_mode === mode ? "selected" : ""}>${mode}</option>`).join("")}
        </select>
        <input name="note" placeholder="Note" value="${esc(row.entry_note || "")}">
        <button class="primary" type="submit">Mark paid</button>
        ${history ? `<h2>Payment history</h2>${history}` : `<p class="muted">No earlier payments saved.</p>`}
        <button class="ghost" id="close-sheet" type="button">Close</button>
      </form>
    </div>`;
}

function wireShell() {
  const theme = document.getElementById("theme-pick");
  if (theme) theme.onchange = () => applyTheme(theme.value);
  document.getElementById("logout").onclick = async () => {
    await api("/api/logout", { method: "POST" });
    state.me = { ok: false };
    draw();
  };
  document.getElementById("reload").onclick = async () => {
    await refreshView();
    draw();
    toast("Updated");
  };
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.onclick = async () => {
      state.detailTenantId = null;
      state.detailPack = null;
      state.view = button.dataset.view;
      await refreshView();
      draw();
    };
  });
  if (state.detailTenantId) wireTenantDetail();
  else if (state.view === "home") wireHome();
  else if (state.view === "tenants") wireTenants();
  else if (state.view === "rent") wireRent();
  else if (state.view === "bills") wireBills();
  else if (state.view === "notifications") wireNotifications();
  else if (state.view === "settings") wireSettings();
  wireOpenTenantButtons();
  const closer = document.getElementById("close-sheet");
  if (closer) closer.onclick = () => { state.editor = null; state.payer = null; draw(); };
  const form = document.getElementById("tenant-form");
  if (form) form.onsubmit = saveTenant;
  const del = document.getElementById("delete-tenant");
  if (del) del.onclick = deleteTenant;
  const pay = document.getElementById("pay-form");
  if (pay) pay.onsubmit = savePayment;
}

async function openTenant(id) {
  state.detailTenantId = Number(id);
  state.detailPack = await api("/api/tenants/" + id + "/detail");
  draw();
}

function wireOpenTenantButtons() {
  document.querySelectorAll("[data-open-tenant]").forEach((button) => {
    button.onclick = () => openTenant(button.dataset.openTenant);
  });
}

function wireTenantDetail() {
  const back = document.getElementById("detail-back");
  if (back) back.onclick = () => {
    state.detailTenantId = null;
    state.detailPack = null;
    draw();
  };
  const edit = document.getElementById("detail-edit");
  if (edit) edit.onclick = () => {
    const row = state.detailPack && state.detailPack.tenant;
    if (row) {
      state.editor = row;
      draw();
    }
  };
  const refresh = document.getElementById("detail-refresh");
  if (refresh) refresh.onclick = () => {
    const ca = state.detailPack && state.detailPack.tenant && state.detailPack.tenant.ca_no;
    if (ca) refreshBills("one", ca);
  };
  const pay = document.getElementById("detail-pay");
  if (pay) pay.onclick = async () => {
    const tenant = state.detailPack && state.detailPack.tenant;
    if (!tenant) return;
    if (!state.rent) state.rent = await api("/api/rent");
    state.payer = state.rent.rows.find((row) => row.id === tenant.id) || { ...tenant, id: tenant.id };
    state.history = (await api("/api/rent/history?tenant_id=" + tenant.id)).entries;
    draw();
  };
}

async function loadNotifications() {
  const pack = await api("/api/notifications");
  state.notifications = pack.items || [];
  state.notifUnread = pack.unread || 0;
}

function wireNotifications() {
  document.querySelectorAll("[data-notif-filter]").forEach((button) => {
    button.onclick = () => { state.notifFilter = button.dataset.notifFilter; draw(); };
  });
  const all = document.getElementById("notif-read-all");
  if (all) all.onclick = async () => {
    await api("/api/notifications/read-all", { method: "POST" });
    await loadNotifications();
    draw();
  };
  const enable = document.getElementById("notif-enable");
  if (enable) enable.onclick = async () => {
    if (!("Notification" in window)) {
      toast("This browser cannot show system alerts.");
      return;
    }
    const result = await Notification.requestPermission();
    toast(result === "granted" ? "Notifications while this tab is open." : "Notifications stay in the list only.");
  };
  document.querySelectorAll("[data-notif-read]").forEach((button) => {
    button.onclick = async () => {
      await api("/api/notifications/read", { method: "POST", body: { id: button.dataset.notifRead } });
      await loadNotifications();
      draw();
    };
  });
  document.querySelectorAll("[data-notif-refresh]").forEach((button) => {
    button.onclick = () => refreshBills("one", button.dataset.notifRefresh);
  });
  document.querySelectorAll("[data-notif-pay]").forEach((button) => {
    button.onclick = async () => {
      if (!state.rent) state.rent = await api("/api/rent");
      state.payer = state.rent.rows.find((row) => String(row.id) === button.dataset.notifPay);
      if (!state.payer) return;
      state.history = (await api("/api/rent/history?tenant_id=" + button.dataset.notifPay)).entries;
      draw();
    };
  });
}

function wireHome() {
  const button = document.getElementById("enable-notes");
  if (!button) return;
  button.onclick = async () => {
    if (!("Notification" in window)) {
      toast("This browser cannot show system alerts. The list on this page still works.");
      return;
    }
    const result = await Notification.requestPermission();
    toast(result === "granted" ? "Alerts will show while this page is open." : "Alerts stay inside the app.");
  };
}

function wireTenants() {
  const search = document.getElementById("search");
  search.oninput = () => {
    state.query = search.value;
    const caret = search.selectionStart;
    draw();
    const again = document.getElementById("search");
    if (again) { again.focus(); again.selectionStart = again.selectionEnd = caret; }
  };
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.onclick = () => { state.filter = button.dataset.filter; draw(); };
  });
  document.getElementById("add-tenant").onclick = () => { state.editor = { active: true }; draw(); };
  document.querySelectorAll("[data-edit]").forEach((button) => {
    button.onclick = () => {
      state.editor = state.tenants.find((row) => String(row.id) === button.dataset.edit);
      draw();
    };
  });
  document.querySelectorAll("[data-refresh-ca]").forEach((button) => {
    button.onclick = () => refreshBills("one", button.dataset.refreshCa);
  });
  document.getElementById("import-btn").onclick = () => document.getElementById("import-file").click();
  document.getElementById("import-file").onchange = (event) => importFile(event.target.files[0], true);
  document.getElementById("template-btn").onclick = () => { window.location = "/api/tenants/template.xlsx"; };
  document.getElementById("export-btn").onclick = () => { window.location = "/api/export.xlsx"; };
  const commit = document.getElementById("import-commit");
  if (commit) commit.onclick = () => importFile(state.importFile, false);
}

async function importFile(file, preview) {
  if (!file) return;
  state.importFile = file;
  const mode = (document.getElementById("dup-mode") || {}).value || "update";
  const body = new FormData();
  body.append("file", file);
  const result = await api(`/api/tenants/import?preview=${preview ? 1 : 0}&on_duplicate=${mode}`, { method: "POST", body });
  state.importReport = result;
  if (!preview) {
    toast(`Added ${result.added}, updated ${result.updated}, skipped ${result.skipped}, rejected ${result.rejected}.`);
    await refreshView();
  }
  draw();
}

function wireRent() {
  document.getElementById("prev-month").onclick = () => loadRent(-1);
  document.getElementById("next-month").onclick = () => loadRent(1);
  document.querySelectorAll("[data-pay]").forEach((button) => {
    button.onclick = async () => {
      state.payer = state.rent.rows.find((row) => String(row.id) === button.dataset.pay);
      state.history = (await api("/api/rent/history?tenant_id=" + button.dataset.pay)).entries;
      draw();
    };
  });
  document.querySelectorAll("[data-unpay]").forEach((button) => {
    button.onclick = async () => {
      await api("/api/rent/mark", { method: "POST", body: { tenant_id: Number(button.dataset.unpay), cycle: state.rent.cycle, status: "unpaid" } });
      await refreshView();
      draw();
    };
  });
}

function wireBills() {
  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.onclick = () => refreshBills(button.dataset.refresh);
  });
  const one = document.getElementById("one-ca");
  one.oninput = () => { state.oneCa = one.value; };
  document.getElementById("one-refresh").onclick = () => refreshBills("one", one.value);
  document.querySelectorAll("[data-ca]").forEach((box) => {
    box.onchange = () => {
      if (box.checked) state.selected.add(box.dataset.ca);
      else state.selected.delete(box.dataset.ca);
      draw();
    };
  });
  document.querySelectorAll("[data-refresh-ca]").forEach((button) => {
    button.onclick = () => refreshBills("one", button.dataset.refreshCa);
  });
  document.querySelectorAll("[data-diagnose-ca]").forEach((button) => {
    button.onclick = () => diagnoseBill(button.dataset.diagnoseCa);
  });
  document.querySelectorAll("[data-retry-ca]").forEach((button) => {
    button.onclick = () => refreshBills("one", button.dataset.retryCa);
  });
  const retry = document.getElementById("retry-errors");
  if (retry) retry.onclick = () => refreshBills("error");
  const stop = document.getElementById("stop-run");
  if (stop) stop.onclick = async () => {
    await api("/api/bills/cancel", { method: "POST" });
    toast("Stopping after the current CA.");
  };
  document.getElementById("toggle-log").onclick = () => { state.logOpen = !state.logOpen; draw(); };
  document.getElementById("toggle-runs").onclick = () => { state.runsOpen = !state.runsOpen; draw(); };
  const search = document.getElementById("bill-search");
  search.oninput = () => { state.billQuery = search.value; draw(); };
  document.getElementById("bill-sort").onchange = (event) => { state.billSort = event.target.value; draw(); };
  document.querySelectorAll("[data-bill-filter]").forEach((button) => {
    button.onclick = () => { state.billFilter = button.dataset.billFilter; draw(); };
  });
  const sticky = document.getElementById("sticky-selected");
  if (sticky) sticky.onclick = () => refreshBills("selected");
  const gate = document.getElementById("contact-gate");
  if (gate) gate.onsubmit = saveContact;
}

function wireSettings() {
  document.getElementById("settings-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      state.settings = await api("/api/settings", {
        method: "PUT",
        body: {
          receipt_email: form.receipt_email.value,
          receipt_mobile: form.receipt_mobile.value,
          request_delay: form.request_delay.value,
          captcha_tries: form.captcha_tries.value,
          bill_check_mode: form.bill_check_mode.value,
          auto_refresh: form.auto_refresh.checked,
          auto_weekday: Number(form.auto_weekday.value),
          auto_time: form.auto_time.value,
        },
      });
      applyTheme(form.theme.value);
      toast("Saved");
    } catch (err) {
      toast(err.message);
    }
  };
  const phoneToggle = document.getElementById("phone-toggle");
  if (phoneToggle) phoneToggle.onclick = async () => {
    const enabled = !(state.me && state.me.phone_access);
    try {
      await api("/api/phone-access", { method: "POST", body: { enabled } });
      state.me.phone_access = enabled;
      toast(enabled ? "Phone access turns on after you restart the app." : "Phone access turns off after you restart the app.");
      draw();
    } catch (err) {
      toast(err.message);
    }
  };
  document.getElementById("backup-btn").onclick = () => { window.location = "/api/backup.db"; };
  document.getElementById("restore-btn").onclick = () => document.getElementById("restore-file").click();
  document.getElementById("restore-file").onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (!confirm("Replace the tenant, rent, and bill data on this computer with this file?")) return;
    const body = new FormData();
    body.append("file", file);
    await api("/api/restore", { method: "POST", body });
    toast("Backup restored");
    await refreshView();
    draw();
  };
}

async function saveContact(event) {
  event.preventDefault();
  const form = event.target;
  try {
    state.settings = await api("/api/settings", {
      method: "PUT",
      body: {
        receipt_email: form.receipt_email.value,
        receipt_mobile: form.receipt_mobile.value,
      },
    });
    const pending = state.pendingRefresh;
    state.pendingRefresh = null;
    toast("Saved");
    if (pending) await refreshBills(pending.mode, pending.ca, true);
    else draw();
  } catch (err) {
    toast(err.message);
  }
}

async function saveTenant(event) {
  event.preventDefault();
  const form = event.target;
  const body = {
    name: form.name.value,
    room: form.room.value,
    phone: form.phone.value,
    rent: form.rent.value,
    due_day: form.due_day.value,
    ca_no: form.ca_no.value,
    notes: form.notes.value,
    active: form.active.checked,
  };
  try {
    if (state.editor.id) await api("/api/tenants/" + state.editor.id, { method: "PUT", body });
    else await api("/api/tenants", { method: "POST", body });
    state.editor = null;
    await refreshView();
    draw();
  } catch (err) {
    toast(err.message);
  }
}

async function deleteTenant() {
  if (!confirm("Delete this tenant and their rent history on this computer?")) return;
  await api("/api/tenants/" + state.editor.id, { method: "DELETE" });
  state.editor = null;
  await refreshView();
  draw();
}

async function savePayment(event) {
  event.preventDefault();
  const form = event.target;
  await api("/api/rent/mark", {
    method: "POST",
    body: {
      tenant_id: state.payer.id,
      cycle: state.rent.cycle,
      status: "paid",
      amount: form.amount.value,
      paid_on: form.paid_on.value,
      mode: form.mode.value,
      note: form.note.value,
    },
  });
  state.payer = null;
  await refreshView();
  draw();
}

async function loadRent(delta) {
  const cycle = shiftMonth(state.rent.cycle, delta);
  state.rent = await api("/api/rent?cycle=" + cycle);
  draw();
}

function shiftMonth(cycle, delta) {
  const [year, month] = cycle.split("-").map(Number);
  const next = new Date(Date.UTC(year, month - 1 + delta, 1));
  return next.getUTCFullYear() + "-" + String(next.getUTCMonth() + 1).padStart(2, "0");
}

async function diagnoseBill(ca) {
  try {
    toast(`Running diagnose for CA ${ca}…`);
    const report = await api("/api/bills/diagnose", { method: "POST", body: { ca_no: ca } });
    const steps = (report.steps || [])
      .map((item) => `${item.step}: ${JSON.stringify(item)}`)
      .join("\n");
    const result = report.result || {};
    const path = report.report_path ? `\nReport: ${report.report_path}` : "";
    alert(
      `Diagnose ${ca}\nStatus: ${result.status || "?"}\nAmount: ${result.amount ?? ""}\nNote: ${result.note || ""}\n\n${steps}${path}`
    );
  } catch (err) {
    toast(err.message);
  }
}

async function refreshBills(mode, ca, ready) {
  if (!state.demo && state.settings && !state.settings.receipt_ready && !ready) {
    state.pendingRefresh = { mode, ca: ca || "" };
    state.view = "bills";
    toast("Enter the email and mobile, then the check will start.");
    draw();
    const gate = document.getElementById("contact-gate");
    if (gate) gate.scrollIntoView({ block: "center" });
    return;
  }
  try {
    const result = await api("/api/bills/refresh", {
      method: "POST",
      body: { mode, ca_no: ca || "", selected: [...state.selected] },
    });
    toast(result.message);
    state.view = "bills";
    state.progress = await api("/api/bills/progress");
    draw();
  } catch (err) {
    toast(err.message);
    if (/email|mobile/i.test(err.message)) {
      state.pendingRefresh = { mode, ca: ca || "" };
      state.view = "bills";
      if (state.settings) state.settings.receipt_ready = false;
      draw();
    }
  }
}

async function refreshView() {
  if (state.view === "home") {
    state.dash = await api("/api/dashboard");
    const pack = await api("/api/alerts");
    state.alerts = pack.alerts;
  } else if (state.view === "tenants") {
    state.tenants = (await api("/api/tenants")).tenants;
    state.rent = await api("/api/rent");
    const pack = await api("/api/bills");
    state.bills = pack.bills;
    state.billToday = pack.today || state.billToday;
  } else if (state.view === "notifications") {
    await loadNotifications();
  } else if (state.view === "rent") {
    const cycle = state.rent ? state.rent.cycle : "";
    state.rent = await api("/api/rent" + (cycle ? "?cycle=" + cycle : ""));
  } else if (state.view === "bills") {
    state.ready = await api("/api/ready");
    const pack = await api("/api/bills");
    state.bills = pack.bills;
    state.progress = pack.progress;
    state.billToday = pack.today || state.billToday;
    state.runs = (await api("/api/bills/runs")).runs;
    state.settings = await api("/api/settings");
    state.demo = !!pack.demo || !!(state.settings && state.settings.demo);
  } else if (state.view === "settings" && isAdmin()) {
    state.settings = await api("/api/settings");
    state.activity = (await api("/api/activity")).activity;
  }
}

function toast(message) {
  state.toast = message;
  draw();
  setTimeout(() => {
    if (state.toast === message) {
      state.toast = "";
      draw();
    }
  }, 3200);
}

function watchProgress() {
  const source = new EventSource("/api/bills/stream");
  let primed = false;
  source.onmessage = async (event) => {
    const snap = JSON.parse(event.data);
    if (!primed) {
      primed = true;
      state.progress = snap;
      state.lastSummary = snap.summary || "";
      if (state.view === "bills") draw();
      return;
    }
    const changed = JSON.stringify(snap) !== JSON.stringify(state.progress);
    state.progress = snap;
    if ((snap.state === "Done" || snap.state === "Stopped") && snap.summary && snap.summary !== state.lastSummary) {
      state.lastSummary = snap.summary;
      if (state.view === "bills") {
        const pack = await api("/api/bills");
        state.bills = pack.bills;
        state.runs = (await api("/api/bills/runs")).runs;
      }
      toast(snap.summary);
      return;
    }
    if (state.view === "bills" && changed) {
      if (snap.state === "Done" || snap.state === "Stopped") {
        const pack = await api("/api/bills");
        state.bills = pack.bills;
        state.runs = (await api("/api/bills/runs")).runs;
      }
      draw();
    }
  };
  source.onerror = () => {
    source.close();
    setTimeout(pollProgress, 2000);
  };
}

async function pollProgress() {
  try {
    const snap = await api("/api/bills/progress");
    state.progress = snap;
    if (state.view === "bills") draw();
  } catch (err) {
    return;
  }
  setTimeout(pollProgress, 2000);
}

async function pollAlerts() {
  try {
    const pack = await api("/api/alerts");
    const fresh = pack.alerts.filter((item) => item.id > state.lastAlert && !item.seen);
    if (pack.alerts.length) state.lastAlert = pack.alerts[0].id;
    fresh.reverse().forEach((item) => {
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification(item.title, { body: item.body });
      }
    });
    if (fresh.length && state.view === "home") {
      state.alerts = pack.alerts;
      draw();
    }
    if (fresh.length) await api("/api/alerts/seen", { method: "POST" });
  } catch (err) {
    return;
  }
  setTimeout(pollAlerts, 8000);
}

async function boot() {
  const me = await api("/api/me");
  state.demo = !!me.demo;
  state.me = me;
  state.settings = await api("/api/settings");
  state.ready = await api("/api/ready");
  await loadNotifications();
  await refreshView();
  if (state.view !== "home") state.dash = await api("/api/dashboard");
  draw();
  watchProgress();
  pollAlerts();
  pollNotifications();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

async function pollNotifications() {
  try {
    const pack = await api("/api/notifications");
    const fresh = (pack.items || []).filter((item) => !item.read);
    const prev = state.lastNotifPoll;
    state.notifications = pack.items || [];
    state.notifUnread = pack.unread || 0;
    state.lastNotifPoll = Date.now();
    if (prev && fresh.length) {
      fresh.forEach((item) => {
        if ("Notification" in window && Notification.permission === "granted") {
          new Notification(item.title, { body: `${item.name}: ${item.body || ""}` });
        }
      });
      if (state.view === "notifications") draw();
      else if (state.notifUnread) {
        const nav = document.querySelector('[data-view="notifications"]');
        if (nav) nav.classList.add("has-badge");
      }
    }
  } catch (err) {
    return;
  }
  setTimeout(pollNotifications, 12000);
}

api("/api/me").then((me) => {
  state.me = me;
  state.demo = !!me.demo;
  if (me.needs_setup) draw();
  else if (me.ok) boot();
  else draw();
}).catch(() => draw());
