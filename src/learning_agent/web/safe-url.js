/* 浏览器侧外部链接兜底：只允许 HTTP(S)，阻止 javascript/data 等协议。 */
(function attachSafeUrl(root) {
  "use strict";

  function safeExternalUrl(value) {
    const text = String(value || "").trim();
    if (!text || text.length > 2048 || /\s/.test(text)) {
      return null;
    }
    let parsed;
    try {
      const base = root.location?.origin || "http://127.0.0.1";
      parsed = new URL(text, base);
    } catch {
      return null;
    }
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    parsed.hash = "";
    return parsed.href;
  }

  const api = Object.freeze({ safeExternalUrl });
  root.LearningAgentUrls = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
