(() => {
  "use strict";

  const token = new URLSearchParams(window.location.search).get("token") || "";
  const defaults = {
    llm: "mock",
    llm_model: "",
    llm_base_url: "",
    llm_profiles: "",
    llm_profile: "",
    llm_strategy: "single",
  };
  const state = {
    boot: null,
    config: { ...defaults },
    jobId: null,
    eventSource: null,
    lastResult: "",
    skillDir: null,
    terminalJobId: null,
    theme: localStorage.getItem("book2skill-theme") || "dark",
    fontSize: Number(localStorage.getItem("book2skill-font") || 15),
  };
  const $ = (id) => document.getElementById(id);

  function api(path, options = {}) {
    const headers = Object.assign({ "X-Book2Skill-Token": token }, options.headers || {});
    return fetch(path, Object.assign({}, options, { headers })).then(async (response) => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "请求失败");
      return payload;
    });
  }

  function lines(id) {
    return $(id).value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  }

  function setLog(text) {
    const log = $("event-log");
    log.textContent += `${text}\n`;
    log.scrollTop = log.scrollHeight;
  }

  function setResult(value) {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    state.lastResult = text;
    $("result").textContent = text;
  }

  function formatEta(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "ETA —";
    if (seconds < 60) return `ETA ${Math.round(seconds)} 秒`;
    return `ETA ${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
  }

  function setPage(name) {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.page === name));
    document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${name}`));
  }

  function setTheme(theme) {
    state.theme = theme;
    localStorage.setItem("book2skill-theme", theme);
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll("[data-theme-set]").forEach((button) => button.classList.toggle("active", button.dataset.themeSet === theme));
  }

  function setFont(delta) {
    state.fontSize = Math.min(20, Math.max(12, state.fontSize + delta));
    localStorage.setItem("book2skill-font", String(state.fontSize));
    $("font-label").textContent = `${state.fontSize}px`;
    document.body.style.fontSize = `${state.fontSize}px`;
  }

  function loadConfig() {
    try {
      const saved = JSON.parse(localStorage.getItem("book2skill-config") || "{}");
      state.config = Object.assign({}, defaults, saved);
    } catch (_error) {
      state.config = { ...defaults };
    }
    const ids = {
      llm: "llm",
      llm_model: "llm-model",
      llm_base_url: "llm-base-url",
      llm_profiles: "llm-profiles",
      llm_profile: "llm-profile",
      llm_strategy: "llm-strategy",
    };
    Object.entries(state.config).forEach(([key, value]) => {
      const element = $(ids[key]);
      if (element && typeof value === "string") element.value = value;
    });
    syncConfigSummary();
  }

  function readConfig() {
    state.config = {
      llm: $("llm").value,
      llm_model: $("llm-model").value.trim(),
      llm_base_url: $("llm-base-url").value.trim(),
      llm_profiles: $("llm-profiles").value.trim(),
      llm_profile: $("llm-profile").value.trim(),
      llm_strategy: $("llm-strategy").value,
    };
  }

  function syncConfigSummary() {
    if (!$("config-summary")) return;
    const mode = state.config.llm === "mock" ? "Mock 离线模式" : "OpenAI-compatible 模式";
    const profile = state.config.llm_profiles ? ` · profile: ${state.config.llm_profiles}` : "";
    $("config-summary").textContent = `当前配置：${mode}${profile}`;
  }

  function saveConfig() {
    readConfig();
    localStorage.setItem("book2skill-config", JSON.stringify(state.config));
    $("config-status").textContent = "已保存到本机浏览器配置";
    syncConfigSummary();
  }

  function setBusy(busy) {
    $("run").disabled = busy;
    $("cancel").disabled = !busy;
    $("pick-files").disabled = busy;
    $("mode").disabled = busy;
    $("install-dry-run").disabled = busy || !state.skillDir;
  }

  function handleEvent(event) {
    if (event.type === "started" && !state.jobId) state.jobId = event.job_id;
    if (event.job_id && state.jobId && event.job_id !== state.jobId) return;
    if (event.type === "started") {
      $("job-kind").textContent = event.kind;
      setBusy(true);
      setLog(`任务开始：${event.kind}`);
    } else if (event.type === "progress") {
      const value = Number.isFinite(event.overall_completed) ? event.overall_completed : 0;
      $("progress-bar").style.width = `${Math.max(0, Math.min(100, value))}%`;
      $("progress-value").textContent = `${Math.round(value)}%`;
      $("progress-stage").textContent = event.detail ? `${event.stage} · ${event.detail}` : event.stage;
      $("progress-eta").textContent = formatEta(event.eta_seconds);
    } else if (event.type === "cancellation_requested") {
      $("action-hint").textContent = "正在等待安全检查点…";
    } else if (event.type === "completed") {
      state.terminalJobId = event.job_id;
      setBusy(false);
      $("job-kind").textContent = "完成";
      $("progress-bar").style.width = "100%";
      $("progress-value").textContent = "100%";
      $("progress-stage").textContent = "完成";
      $("progress-eta").textContent = "ETA 完成";
      setResult(event.result);
      if (event.result && event.result.bundle_path) $("bundle-path").textContent = event.result.bundle_path;
      if (event.result && event.result.skill_dir) {
        state.skillDir = event.result.skill_dir;
        $("skill-path").textContent = event.result.skill_dir;
      }
      $("install-dry-run").disabled = !state.skillDir;
      setLog("任务完成。");
    } else if (event.type === "cancelled") {
      state.terminalJobId = event.job_id;
      setBusy(false);
      $("job-kind").textContent = "已取消";
      $("progress-stage").textContent = "已在安全检查点取消";
      $("progress-eta").textContent = "ETA 已取消";
      setLog("任务已取消，旧有输出未被覆盖。");
    } else if (event.type === "failed") {
      state.terminalJobId = event.job_id;
      setBusy(false);
      $("job-kind").textContent = "失败";
      setResult(event.error || { error: "任务失败" });
      setLog(`任务失败：${(event.error && event.error.message) || "未知错误"}`);
    }
  }

  function connectEvents() {
    if (state.eventSource) state.eventSource.close();
    state.eventSource = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    state.eventSource.onmessage = (message) => {
      try { handleEvent(JSON.parse(message.data)); } catch (_error) { setLog("收到无法解析的事件。"); }
    };
    state.eventSource.onerror = () => { $("connection").textContent = "事件连接重试中"; };
    state.eventSource.onopen = () => { $("connection").textContent = "本机安全连接"; };
  }

  async function bootstrap() {
    const data = await api("/api/bootstrap");
    state.boot = data;
    $("version").textContent = `v${data.version} · Windows 本地模式 · 默认离线`;
    $("root-path").textContent = data.paths.root;
    $("bundle-path").textContent = data.paths.bundles;
    $("skill-path").textContent = data.paths.skills;
    $("env-path").textContent = data.paths.env_file;
    $("profiles-path").textContent = data.paths.profiles_file;
    $("settings-root-path").textContent = data.paths.root;
    loadConfig();
    if (data.job && data.job.busy) setBusy(true);
  }

  async function pickFiles() {
    if (!window.pywebview || !window.pywebview.api) {
      $("action-hint").textContent = "当前浏览器未连接原生文件选择器，请手动填写路径。";
      return;
    }
    const paths = await window.pywebview.api.pick_sources();
    if (paths && paths.length) $("sources").value = paths.join("\n");
  }

  async function run() {
    state.jobId = null;
    state.terminalJobId = null;
    readConfig();
    const mode = $("mode").value;
    const payload = {
      sources: lines("sources"),
      rights_note: $("rights-note").value.trim(),
      llm: state.config.llm,
      llm_model: state.config.llm_model || null,
      llm_base_url: state.config.llm_base_url || null,
      llm_profiles: state.config.llm_profiles || null,
      llm_profile: state.config.llm_profile || null,
      llm_strategy: state.config.llm_strategy || "single",
    };
    if (mode === "build") {
      Object.assign(payload, {
        name: $("skill-name").value.trim(),
        description: $("skill-description").value.trim(),
        use_when: lines("use-when"),
        do_not_use_when: lines("do-not-use-when"),
      });
    }
    $("event-log").textContent = "";
    setResult("任务已提交，等待进度事件…");
    $("action-hint").textContent = "";
    try {
      const response = await api(`/api/jobs/${mode}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      state.jobId = response.job_id;
      if (state.terminalJobId !== response.job_id) setBusy(true);
    } catch (error) {
      setResult({ error: error.message });
    }
  }

  async function cancel() {
    try { await api("/api/jobs/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
    catch (error) { $("action-hint").textContent = error.message; }
  }

  async function installDryRun() {
    if (!state.skillDir) return;
    try {
      const result = await api("/api/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: $("install-host").value, skill_dir: state.skillDir, dry_run: true }),
      });
      setResult({ install_preview: result });
      setLog("已生成宿主安装预览；未写入宿主目录。");
    } catch (error) {
      setResult({ error: error.message });
    }
  }

  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => setPage(tab.dataset.page)));
  document.querySelectorAll("[data-theme-set]").forEach((button) => button.addEventListener("click", () => setTheme(button.dataset.themeSet)));
  $("font-minus").addEventListener("click", () => setFont(-1));
  $("font-plus").addEventListener("click", () => setFont(1));
  $("mode").addEventListener("change", () => { $("skill-fields").hidden = $("mode").value !== "build"; });
  $("pick-files").addEventListener("click", pickFiles);
  $("run").addEventListener("click", run);
  $("cancel").addEventListener("click", cancel);
  $("install-dry-run").addEventListener("click", installDryRun);
  $("save-config").addEventListener("click", saveConfig);
  $("copy-result").addEventListener("click", async () => { try { await navigator.clipboard.writeText(state.lastResult); } catch (_error) {} });

  setTheme(state.theme);
  setFont(0);
  $("skill-fields").hidden = $("mode").value !== "build";
  bootstrap().catch((error) => { $("connection").textContent = "连接失败"; setResult({ error: error.message }); });
  connectEvents();
})();
