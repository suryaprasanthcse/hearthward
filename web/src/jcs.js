// HW-JCS (blueprint C-2): RFC 8785 restricted to keys matching [a-z0-9_]+ and integers within 2^53 - 1.
// Inside that profile the output is the sorted, compact JSON the server and the offline verifier produce.

const KEY = /^[a-z0-9_]+$/;

export function checkProfile(value, path = "$", depth = 0) {
  if (depth > 64) throw new Error(`${path}: nested too deeply`);
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error(`${path}: only integers within 2^53 - 1 are allowed`);
    return;
  }
  if (typeof value === "string") {
    if (!value.isWellFormed()) throw new Error(`${path}: string is not well-formed Unicode`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => checkProfile(item, `${path}[${i}]`, depth + 1));
    return;
  }
  if (typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    for (const [key, item] of Object.entries(value)) {
      if (!KEY.test(key)) throw new Error(`${path}: key ${JSON.stringify(key)} is outside [a-z0-9_]+`);
      checkProfile(item, `${path}.${key}`, depth + 1);
    }
    return;
  }
  throw new Error(`${path}: ${typeof value} is outside the profile`);
}

function render(value) {
  if (Array.isArray(value)) return `[${value.map(render).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${render(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function jcs(value) {
  checkProfile(value);
  return new TextEncoder().encode(render(value));
}

// Strict parse for bytes that were signed: reject duplicate keys and non-integer number tokens, then check the
// profile. JSON.parse keeps the last duplicate silently and reads 1.0 as the integer 1, so the raw text is scanned.
export function parseStrict(bytes) {
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  scanText(text);
  const value = JSON.parse(text);
  checkProfile(value);
  return value;
}

function scanText(text) {
  const stack = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "{") stack.push(new Set());
    else if (c === "}") stack.pop();
    else if (c === '"') {
      let j = i + 1;
      while (text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      const token = JSON.parse(text.slice(i, j + 1));
      let k = j + 1;
      while (/\s/.test(text[k] ?? "")) k += 1;
      if (text[k] === ":" && stack.length) {
        const seen = stack[stack.length - 1];
        if (seen.has(token)) throw new Error(`duplicate key ${JSON.stringify(token)}`);
        seen.add(token);
      }
      i = j;
    } else if (c === "-" || (c >= "0" && c <= "9")) {
      const number = /-?\d+(\.\d+)?([eE][+-]?\d+)?/y;
      number.lastIndex = i;
      const token = number.exec(text)?.[0] ?? c;
      if (/[.eE]/.test(token)) throw new Error(`only integers are allowed, found ${token}`);
      i += token.length - 1;
    }
    i += 1;
  }
}
