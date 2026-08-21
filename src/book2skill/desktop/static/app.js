(() => {
  "use strict";

  const token = new URLSearchParams(window.location.search).get("token") || "";
  const state = { jobId: null, eventSource: null, lastResult: "", skillDir: null, terminalJobId: null };
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
      if (event.result.bundle_path) $("bundle-path").textContent = event.result.bundle_path;
      if (event.result.skill_dir) {
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
    $("version").textContent = `v${data.version} · Windows 本地模式 · 默认离线`;
    $("root-path").textContent = data.paths.root;
    $("bundle-path").textContent = data.paths.bundles;
    $("skill-path").textContent = data.paths.skills;
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
    const mode = $("mode").value;
    const payload = {
      sources: lines("sources"),
      rights_note: $("rights-note").value.trim(),
      llm: $("llm").value,
      llm_model: $("llm-model").value.trim() || null,
      llm_base_url: $("llm-base-url").value.trim() || null,
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
        body: JSON.stringify({
          host: $("install-host").value,
          skill_dir: state.skillDir,
          dry_run: true,
        }),
      });
      setResult({ install_preview: result });
      setLog("已生成宿主安装预览；未写入宿主目录。");
    } catch (error) {
      setResult({ error: error.message });
    }
  }

  $("mode").addEventListener("change", () => { $("skill-fields").hidden = $("mode").value !== "build"; });
  $("pick-files").addEventListener("click", pickFiles);
  $("run").addEventListener("click", run);
  $("cancel").addEventListener("click", cancel);
  $("install-dry-run").addEventListener("click", installDryRun);
  $("copy-result").addEventListener("click", async () => { try { await navigator.clipboard.writeText(state.lastResult); } catch (_error) {} });
  bootstrap().catch((error) => { $("connection").textContent = "连接失败"; setResult({ error: error.message }); });
  connectEvents();
})();
