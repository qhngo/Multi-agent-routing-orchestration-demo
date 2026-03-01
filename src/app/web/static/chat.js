const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const welcomeText = document.getElementById("welcome-text");
const logoutButton = document.getElementById("logout-button");
const newConversationButton = document.getElementById("new-conversation-button");
const clearConversationButton = document.getElementById("clear-conversation-button");

let sessionId = "web-session";

function addMessage(text, role, metricsOverride = null) {
  const node = document.createElement("div");
  node.className = `msg ${role === "user" ? "msg-user" : "msg-agent"}`;
  const parsed = parseAgentMetrics(text, role, metricsOverride);
  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = parsed.answerText;
  node.appendChild(body);

  if (parsed.metricsText) {
    const metrics = document.createElement("div");
    metrics.className = "msg-meta";
    metrics.textContent = parsed.metricsText;
    node.appendChild(metrics);
  }
  chatLog.appendChild(node);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function addCollapsedPlannerPanel(title, bodyLines) {
  const node = document.createElement("div");
  node.className = "msg msg-agent msg-planner";

  const details = document.createElement("details");
  details.className = "planner-details";

  const summary = document.createElement("summary");
  summary.className = "planner-summary";
  summary.textContent = title;
  details.appendChild(summary);

  const body = document.createElement("pre");
  body.className = "planner-body";
  body.textContent = Array.isArray(bodyLines) ? bodyLines.join("\n") : String(bodyLines ?? "");
  details.appendChild(body);

  node.appendChild(details);
  chatLog.appendChild(node);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function parseAgentMetrics(text, role, metricsOverride = null) {
  const value = String(text ?? "");
  if (role !== "agent") {
    return { answerText: value, metricsText: "" };
  }

  const match = value.match(/^(.*)\n\n\[(processing_time_s=.*|total_tokens=.*)\]$/s);
  const values = match ? parseMetricsPair(match[2]) : { processing_time_s: null, total_tokens: null };
  const answerText = match ? match[1] : value;
  const handlingAgent = metricsOverride?.handling_agent ?? null;
  const processingTime =
    metricsOverride?.processing_time_s != null
      ? metricsOverride.processing_time_s
      : values.processing_time_s;
  const totalTokens =
    metricsOverride?.total_tokens != null
      ? metricsOverride.total_tokens
      : values.total_tokens;

  if (handlingAgent == null && processingTime == null && totalTokens == null) {
    return { answerText: value, metricsText: "" };
  }

  const formattedMetrics = formatMetricsText(
    handlingAgent,
    processingTime,
    totalTokens
  );

  return {
    answerText,
    metricsText: formattedMetrics,
  };
}

function parseMetricsPair(text) {
  const result = { processing_time_s: null, total_tokens: null };
  const rawMetrics = String(text ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  for (const item of rawMetrics) {
    const [key, ...rest] = item.split("=");
    if (!key || rest.length === 0) {
      continue;
    }
    const valuePart = rest.join("=").trim();
    if (key.trim() === "processing_time_s") {
      const num = Number(valuePart);
      result.processing_time_s = Number.isFinite(num) ? num : valuePart;
    } else if (key.trim() === "total_tokens") {
      const num = Number(valuePart);
      result.total_tokens = Number.isFinite(num) ? num : valuePart;
    }
  }
  return result;
}

function extractHandlingAgentFromTrace(trace) {
  if (!Array.isArray(trace)) {
    return null;
  }
  for (const entry of trace) {
    if (typeof entry === "string" && entry.startsWith("router:selected:")) {
      return entry.slice("router:selected:".length).trim() || null;
    }
  }
  for (const entry of trace) {
    if (typeof entry === "string" && entry.startsWith("router:fallback:")) {
      return entry.slice("router:fallback:".length).trim() || null;
    }
  }
  return null;
}

function extractMetricsFromTrace(trace) {
  const result = { processing_time_s: null, total_tokens: null };
  if (!Array.isArray(trace)) {
    return result;
  }
  const processingPrefix = "arxiv_agent:metrics:processing_time_s:";
  const tokenPrefix = "arxiv_agent:metrics:total_tokens:";
  for (const entry of trace) {
    if (typeof entry === "string" && entry.startsWith(processingPrefix)) {
      const raw = entry.slice(processingPrefix.length).trim();
      const num = Number(raw);
      result.processing_time_s = Number.isFinite(num) ? num : raw;
    }
    if (typeof entry === "string" && entry.startsWith(tokenPrefix)) {
      const raw = entry.slice(tokenPrefix.length).trim();
      const num = Number(raw);
      result.total_tokens = Number.isFinite(num) ? num : raw;
    }
  }
  return result;
}

function extractPlannerEvents(trace) {
  if (!Array.isArray(trace)) {
    return [];
  }
  const prefixes = ["tool_planner:event:", "router_planner:event:"];
  const events = [];
  for (const entry of trace) {
    if (typeof entry !== "string") {
      continue;
    }
    const matchedPrefix = prefixes.find((prefix) => entry.startsWith(prefix));
    if (!matchedPrefix) {
      continue;
    }
    const raw = entry.slice(matchedPrefix.length);
    try {
      const parsed = JSON.parse(raw);
      events.push(parsed);
    } catch (_) {
      // Ignore malformed planner event entries.
    }
  }
  return events;
}

function renderPlannerEvents(trace) {
  const events = extractPlannerEvents(trace);
  for (const event of events) {
    const eventName = String(event?.event ?? "");
    if (eventName === "plan" || eventName === "plan_revised") {
      const plan = Array.isArray(event.plan) ? event.plan : [];
      if (plan.length === 0) {
        continue;
      }
      const lines = [];
      for (let i = 0; i < plan.length; i += 1) {
        const step = plan[i] ?? {};
        const stepName = String(step.tool_name ?? step.agent_id ?? "unknown");
        const purpose = String(step.purpose ?? "").trim();
        const goal = String(step.goal ?? "").trim();
        const dependsOn = Array.isArray(step.depends_on) ? step.depends_on : [];
        const confidence = Number(step.confidence);
        const note = String(step.note ?? "").trim();
        const confidenceText = Number.isFinite(confidence) ? `confidence=${confidence.toFixed(2)}` : "";
        const dependsText = dependsOn.length > 0 ? `depends_on=${dependsOn.join(",")}` : "";
        const suffix = [purpose, goal, dependsText, confidenceText, note].filter(Boolean).join(" | ");
        if (suffix) {
          lines.push(`${i + 1}. ${stepName} (${suffix})`);
        } else {
          lines.push(`${i + 1}. ${stepName}`);
        }
      }
      const panelTitle = eventName === "plan" ? "Execution plan" : "Revised plan";
      addCollapsedPlannerPanel(panelTitle, lines);
      continue;
    }

    if (eventName === "step_output") {
      const stepNo = Number(event.step);
      const stepName = String(event.tool_name ?? event?.metadata?.agent_id ?? "unknown");
      const summary = event.result && typeof event.result === "object" ? event.result : {};
      const lines = [
        `step: ${Number.isFinite(stepNo) ? stepNo : "?"}`,
        `source: ${stepName}`,
        JSON.stringify(summary),
      ];
      if (event.metadata && typeof event.metadata === "object") {
        lines.push(JSON.stringify(event.metadata));
      }
      addCollapsedPlannerPanel(
        `Step ${Number.isFinite(stepNo) ? stepNo : "?"} output`,
        lines
      );
    }
  }
}

function formatMetricsText(handlingAgent, processingTime, totalTokens) {
  const agentLabel = handlingAgent ?? "N/A";
  const timeLabel = processingTime != null ? processingTime : "N/A";
  const tokenLabel = totalTokens != null ? totalTokens : "N/A";
  return `Agent: ${agentLabel} | processing time (s): ${timeLabel} | tokens: ${tokenLabel}`;
}

async function loadUser() {
  const response = await fetch("/web/me");
  if (!response.ok) {
    window.location.href = "/login";
    return;
  }

  const data = await response.json();
  sessionId = data.session_id;
  welcomeText.textContent = `Signed in as ${data.username}`;
  chatLog.innerHTML = "";

  if (Array.isArray(data.history) && data.history.length > 0) {
    for (const entry of data.history) {
      const role = entry.creator === "agent" ? "agent" : "user";
      addMessage(entry.message, role, {
        handling_agent: entry.handling_agent,
        processing_time_s: entry.processing_time_s,
        total_tokens: entry.total_tokens,
      });
    }
  } else {
    addMessage("Hi there...", "agent");
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) {
    return;
  }

  addMessage(message, "user");
  chatInput.value = "";

  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });

  if (!response.ok) {
    addMessage("Request failed. Please try again.", "agent");
    return;
  }

  const data = await response.json();
  sessionId = data.session_id;
  renderPlannerEvents(data.trace);
  const traceMetrics = extractMetricsFromTrace(data.trace);
  addMessage(data.answer, "agent", {
    handling_agent: extractHandlingAgentFromTrace(data.trace),
    processing_time_s: traceMetrics.processing_time_s,
    total_tokens: traceMetrics.total_tokens,
  });
});

logoutButton.addEventListener("click", async () => {
  await fetch("/web/logout", { method: "POST" });
  window.location.href = "/login";
});

newConversationButton.addEventListener("click", async () => {
  const response = await fetch("/web/conversations/new", { method: "POST" });
  if (!response.ok) {
    addMessage("Could not create a new conversation. Please try again.", "agent");
    return;
  }

  const data = await response.json();
  sessionId = data.session_id;
  chatLog.innerHTML = "";
  addMessage("Hi there...", "agent");
});

clearConversationButton.addEventListener("click", async () => {
  const response = await fetch("/web/conversations/clear", { method: "POST" });
  if (!response.ok) {
    addMessage("Could not clear this conversation. Please try again.", "agent");
    return;
  }

  const data = await response.json();
  sessionId = data.session_id;
  chatLog.innerHTML = "";
  addMessage("Conversation cleared.", "agent");
});

loadUser();
