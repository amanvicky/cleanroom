/* Shared GitHub repo fetching for the demo (triage tab + live runner tab).
   Uses only CORS-enabled endpoints: api.github.com + raw.githubusercontent.com. */

export const MAX_FILES = 250;
export const MAX_FILE_BYTES = 200 * 1024;
export const SKIP_PARTS = ["node_modules/", "dist/", "vendor/", ".min.js", ".lock", "package-lock.json"];
export const FETCH_POOL = 8;

export const TEXT_EXT = new Set([
  "py", "js", "mjs", "cjs", "ts", "jsx", "tsx", "json", "sh", "bash",
  "yml", "yaml", "toml", "cfg", "ini", "env", "txt", "md", "rst",
  "rb", "go", "rs", "php", "pl", "ps1", "bat", "html", "xml", "csv", "css",
]);

export function parseRepo(raw) {
  let s = raw.trim().replace(/\.git$/, "").replace(/\/$/, "");
  s = s.replace(/^https?:\/\/(www\.)?github\.com\//i, "").replace(/^github\.com\//i, "");
  const m = s.match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
  if (!m) return null;
  return { owner: m[1], repo: m[2] };
}

export async function gh(path) {
  const res = await fetch("https://api.github.com" + path);
  if (res.status === 403 && res.headers.get("X-RateLimit-Remaining") === "0") {
    throw new Error("GitHub API rate limit reached (60/hour anonymous). The fixture presets still work.");
  }
  if (!res.ok) throw new Error("GitHub API said " + res.status + " for " + path);
  return res.json();
}

export async function pool(items, worker) {
  const out = new Array(items.length);
  let next = 0;
  const runners = Array.from({ length: Math.min(FETCH_POOL, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await worker(items[i], i);
    }
  });
  await Promise.all(runners);
  return out;
}

export async function fetchRepoFiles(raw, onProgress, opts) {
  const maxFileBytes = (opts && opts.maxFileBytes) || MAX_FILE_BYTES;
  const parsed = parseRepo(raw);
  if (!parsed) throw new Error("That doesn't look like a GitHub repo URL.");
  const { owner, repo } = parsed;

  const meta = await gh("/repos/" + owner + "/" + repo);
  const branch = meta.default_branch || "main";
  const tree = await gh("/repos/" + owner + "/" + repo + "/git/trees/" + branch + "?recursive=1");
  if (!tree.tree) throw new Error("Could not read the file tree (is the repo empty?)");

  const candidates = tree.tree.filter(t => {
    if (t.type !== "blob") return false;
    if (t.size > maxFileBytes) return false;
    const p = t.path;
    if (SKIP_PARTS.some(part => p.includes(part))) return false;
    const dot = p.lastIndexOf(".");
    if (dot === -1) return false;
    return TEXT_EXT.has(p.slice(dot + 1).toLowerCase());
  }).slice(0, MAX_FILES);

  let done = 0;
  const files = (await pool(candidates, async t => {
    const res = await fetch("https://raw.githubusercontent.com/" + owner + "/" + repo +
                            "/" + branch + "/" + t.path.split("/").map(encodeURIComponent).join("/"));
    const text = res.ok ? await res.text() : "";
    done += 1;
    if (onProgress) onProgress(done, candidates.length);
    return [t.path, text];
  })).filter(f => f[1]);

  return { files, label: owner + "/" + repo + " @ " + branch };
}
