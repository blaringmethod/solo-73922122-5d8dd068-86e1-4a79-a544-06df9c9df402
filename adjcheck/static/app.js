/* Progressive enhancement for the correction form.
 *
 * Without JavaScript the form still works (server-side precheck / apply with
 * flash messages).  With JavaScript we call the JSON API and render the plan
 * without a round-trip through HTML.
 *
 * Coordinates are signed 64-bit integer metres and must stay exact end to
 * end.  JavaScript Number only represents integers up to 2^53 exactly, so at
 * large coordinates a one-metre-wide patch can be rounded to two metres.  The
 * whole path therefore uses BigInt: text -> BigInt, request body, response
 * parsing (a BigInt-aware JSON parser) and rendering.  No coordinate is ever
 * passed through Number.
 */
(() => {
  "use strict";

  const INT64_MIN = -(2n ** 63n);
  const INT64_MAX = 2n ** 63n - 1n;
  const INTEGER_RE = /^-?\d+$/;

  /** Parse one integer-metre token exactly, validating the int64 metre range. */
  function parseCoord(value, line) {
    const text = String(value).trim();
    if (!INTEGER_RE.test(text)) {
      throw new Error(`坐标必须是整数米：${line}`);
    }
    const big = BigInt(text);
    if (big < INT64_MIN || big > INT64_MAX) {
      throw new Error(
        `坐标 ${text} 超出有符号 64 位整数米范围 [${INT64_MIN}, ${INT64_MAX}]：${line}`,
      );
    }
    return big;
  }

  function parseBatch(text) {
    return text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const parts = line.split(/[\s,;，；]+/).filter(Boolean);
        if (parts.length !== 5) {
          throw new Error(`行格式应为“图幅号 x0 y0 x1 y1”：${line}`);
        }
        const nums = parts.slice(1).map((v) => parseCoord(v, line));
        if (!(nums[0] < nums[2] && nums[1] < nums[3])) {
          throw new Error(`矩形必须满足 x0<x1 且 y0<y1：${line}`);
        }
        return {
          no: parts[0],
          x0: nums[0],
          y0: nums[1],
          x1: nums[2],
          y1: nums[3],
        };
      });
  }

  /**
   * Serialize a value containing BigInts as a JSON number literal (no quotes).
   * Only the placeholders we just inserted can match the marker pattern, so
   * user text containing the same characters cannot affect the output.
   */
  function stringifyBigInt(value) {
    const markers = new Map();
    let next = 0;
    const mark = (big) => {
      const token = `__adjcheck_bigint_${markers.size}_${next++}__`;
      markers.set(token, big.toString());
      return token;
    };
    const toPlain = (node) => {
      if (typeof node === "bigint") return mark(node);
      if (Array.isArray(node)) return node.map(toPlain);
      if (node && typeof node === "object") {
        const out = {};
        for (const [k, v] of Object.entries(node)) out[k] = toPlain(v);
        return out;
      }
      return node;
    };
    let text = JSON.stringify(toPlain(value));
    for (const [token, digits] of markers) {
      text = text.replace(`"${token}"`, digits);
    }
    return text;
  }

  /**
   * BigInt-aware JSON parser.  Every integral number literal is returned as a
   * BigInt (including values beyond 2^53), while non-integral literals stay
   * Number.  The server only emits integers for coordinates/areas, so every
   * value rendered below is exact; strings/null/booleans behave as usual.
   */
  function parseJsonBigInt(text) {
    let i = 0;
    const n = text.length;

    const skipWs = () => {
      while (i < n && " \t\r\n".includes(text[i])) i += 1;
    };
    const fail = (msg) => {
      throw new Error(`JSON 解析失败（${msg}），位置 ${i}`);
    };

    function parseValue() {
      skipWs();
      const ch = text[i];
      if (ch === "{") return parseObject();
      if (ch === "[") return parseArray();
      if (ch === '"') return parseString();
      if (ch === "t" || ch === "f") return parseBool();
      if (ch === "n") return parseNull();
      if (ch === "-" || (ch >= "0" && ch <= "9")) return parseNumber();
      return fail("意外字符");
    }

    function parseObject() {
      const obj = {};
      i += 1; // {
      skipWs();
      if (text[i] === "}") {
        i += 1;
        return obj;
      }
      while (true) {
        skipWs();
        if (text[i] !== '"') fail("对象键必须是字符串");
        const key = parseString();
        skipWs();
        if (text[i] !== ":") fail("缺少冒号");
        i += 1;
        obj[key] = parseValue();
        skipWs();
        const sep = text[i];
        if (sep === ",") {
          i += 1;
          continue;
        }
        if (sep === "}") {
          i += 1;
          return obj;
        }
        fail("对象内缺少逗号或右花括号");
      }
    }

    function parseArray() {
      const arr = [];
      i += 1; // [
      skipWs();
      if (text[i] === "]") {
        i += 1;
        return arr;
      }
      while (true) {
        arr.push(parseValue());
        skipWs();
        const sep = text[i];
        if (sep === ",") {
          i += 1;
          continue;
        }
        if (sep === "]") {
          i += 1;
          return arr;
        }
        fail("数组内缺少逗号或右方括号");
      }
    }

    function parseString() {
      let out = "";
      i += 1; // opening quote
      while (i < n) {
        const ch = text[i];
        if (ch === '"') {
          i += 1;
          return out;
        }
        if (ch === "\\") {
          const esc = text[i + 1];
          if (esc === "u") {
            out += String.fromCharCode(parseInt(text.slice(i + 2, i + 6), 16));
            i += 6;
          } else {
            const simple = { b: "\b", f: "\f", n: "\n", r: "\r", t: "\t", '"': '"', "\\": "\\" };
            out += simple[esc] ?? esc;
            i += 2;
          }
        } else {
          out += ch;
          i += 1;
        }
      }
      fail("字符串未闭合");
    }

    function parseBool() {
      if (text.startsWith("true", i)) {
        i += 4;
        return true;
      }
      if (text.startsWith("false", i)) {
        i += 5;
        return false;
      }
      return fail("非法字面量");
    }

    function parseNull() {
      if (text.startsWith("null", i)) {
        i += 4;
        return null;
      }
      return fail("非法字面量");
    }

    function parseNumber() {
      const start = i;
      if (text[i] === "-") i += 1;
      while (i < n && text[i] >= "0" && text[i] <= "9") i += 1;
      let isFloat = false;
      if (text[i] === ".") {
        isFloat = true;
        i += 1;
        while (i < n && text[i] >= "0" && text[i] <= "9") i += 1;
      }
      if (text[i] === "e" || text[i] === "E") {
        isFloat = true;
        i += 1;
        if (text[i] === "+" || text[i] === "-") i += 1;
        while (i < n && text[i] >= "0" && text[i] <= "9") i += 1;
      }
      const literal = text.slice(start, i);
      return isFloat ? Number(literal) : BigInt(literal);
    }

    const result = parseValue();
    skipWs();
    if (i !== n) fail("尾部存在多余字符");
    return result;
  }

  /** BigInts stringify without the trailing "n"; null/undefined render as "-". */
  const show = (value) => (value === null || value === undefined ? "-" : value.toString());

  const stats = (s) =>
    `缺片 ${show(s.missing_count)} 区 / ${show(s.missing_area)} m²；` +
    `重叠 ${show(s.overlap_count)} 区 / ${show(s.overlap_area)} m²`;

  // Exposed for the Node unit tests; harmless in the browser.
  const api = {
    INT64_MIN,
    INT64_MAX,
    parseCoord,
    parseBatch,
    stringifyBigInt,
    parseJsonBigInt,
    show,
    stats,
  };
  if (typeof window === "undefined") {
    module.exports = api;
    return;
  }
  window.adjcheck = api;

  const form = document.querySelector("form[data-correct-form]");
  if (!form || !window.fetch) return;

  const panel = document.getElementById("plan-panel");
  const out = document.getElementById("plan-output");
  const textarea = form.querySelector("textarea[name=batch]");
  const actionUrl = form.dataset.checkUrl;
  const applyUrl = form.dataset.applyUrl;
  const versionInput = form.querySelector("input[name=version]");

  function render(payload, applied) {
    const p = payload.plan;
    panel.hidden = false;
    const statusText = applied
      ? "已写入"
      : p.accepted
        ? "预检通过（尚未写入）"
        : p.status === "stale"
          ? "数据已过期（未写入）"
          : "已退回（未写入）";
    const cls = p.accepted ? "ok" : "error";
    out.innerHTML = "";
    const box = document.createElement("div");
    box.className = `plan ${cls}`;
    const rows = [
      ["结论", statusText],
      ["说明", (payload.message ? payload.message + "：" : "") + p.reasons.join("；")],
      ["更正前", stats(p.before)],
      ["更正后（预计）", stats(p.after)],
      [
        "消除旧问题",
        `缺片 ${show(p.eliminated.missing_area)} m²；重叠 ${show(p.eliminated.overlap_area)} m²`,
      ],
      [
        "新位置问题",
        `缺片 ${show(p.new_problems.missing_area)} m²；重叠 ${show(p.new_problems.overlap_area)} m²`,
      ],
      ["重算范围", p.window.map((r) => `[${r.map(show).join(" ")}]`).join(" ") || "无"],
      ["本批图幅", p.batch.map((b) => `${b.no} [${b.rect.map(show).join(" ")}]`).join("；")],
    ];
    for (const [k, v] of rows) {
      const row = document.createElement("div");
      row.className = "plan-row";
      const kk = document.createElement("span");
      kk.className = "k";
      kk.textContent = k;
      const vv = document.createElement("span");
      vv.className = "v";
      vv.textContent = v;
      row.append(kk, vv);
      box.append(row);
    }
    out.append(box);
    if (applied && payload.state_url) {
      setTimeout(() => window.location.assign(payload.state_url), 900);
    }
  }

  async function submit(kind) {
    let batch;
    try {
      batch = parseBatch(textarea.value);
    } catch (err) {
      panel.hidden = false;
      out.innerHTML = `<div class="plan error"><div class="plan-row"><span class="k">输入错误</span><span class="v"></span></div></div>`;
      out.querySelector(".v").textContent = err.message;
      return;
    }
    let version;
    try {
      version = parseCoord(versionInput.value, "version");
    } catch (err) {
      panel.hidden = false;
      out.textContent = `请求失败：${err.message}`;
      return;
    }
    const url = kind === "apply" ? applyUrl : actionUrl;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: stringifyBigInt({ batch, version }),
    });
    const raw = await resp.text();
    let payload;
    try {
      payload = parseJsonBigInt(raw);
    } catch (err) {
      payload = { error: `服务器返回了无法解析的响应：${err.message}` };
    }
    if (!resp.ok && !payload.plan) {
      panel.hidden = false;
      out.innerHTML = "";
      const box = document.createElement("div");
      box.className = "plan error";
      const row = document.createElement("div");
      row.className = "plan-row";
      const kk = document.createElement("span");
      kk.className = "k";
      kk.textContent = "未处理";
      const vv = document.createElement("span");
      vv.className = "v";
      vv.textContent = payload.error || resp.statusText;
      row.append(kk, vv);
      box.append(row);
      out.append(box);
      return;
    }
    render(payload, kind === "apply" && payload.applied === true);
  }

  form.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    if (!submitter) return;
    event.preventDefault();
    const kind = submitter.value === "apply" ? "apply" : "check";
    if (kind === "apply" && !window.confirm("确认提交本批更正？")) return;
    submit(kind).catch((err) => {
      panel.hidden = false;
      out.textContent = `请求失败：${err.message}`;
    });
  });
})();
