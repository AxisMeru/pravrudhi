// A dropped file has nowhere to go but the message. This engine has no upload route and none is being added
// here, so the file is read in the browser and its text becomes part of the turn the user sends. That makes the
// limits below the whole of the protection: nothing downstream re-checks a size or a type, and a refusal that is
// not shown is a file that silently never arrived. Every rejection in this module is therefore returned as a
// value carrying its own reason, for the surface to put in front of the user rather than swallow.
//
// What is accepted follows what a conversation about code and evidence needs: text, source, markdown, JSON and
// CSV. Anything else would arrive as noise — unreadable in the transcript, unquotable by the model, and paid for
// in tokens by everyone who reads the thread afterwards.

/** Why a dropped file did not become an attachment. Each one maps to a single sentence shown to the user. */
export type AttachmentRefusalReason =
  | "too-large"
  | "binary"
  | "undecodable"
  | "over-budget"
  | "duplicate"
  | "unreadable";

/** The parts of a dropped `File` that survive the drop: the object itself is not kept once its text is read. */
export interface AttachmentFile {
  name: string;
  size: number;
  type: string;
}

export interface AttachmentRefusal {
  id: string;
  reason: AttachmentRefusalReason;
  file: AttachmentFile;
  /** The reason in the words the user sees. States the limit that was hit, not just that one was. */
  why: string;
}

export interface Attachment {
  id: string;
  name: string;
  size: number;
  /** What the file was read as, and what the fence in the outgoing message should say. */
  language: string;
  lines: number;
  text: string;
}

export type AttachmentOutcome = { ok: true; attachment: Attachment } | { ok: false; refusal: AttachmentRefusal };

// One dropped file's entire cost lands on a single chat turn: it is pasted into the message body and sent as
// JSON over one POST. 256 KiB is around a quarter of a million characters, already a long document in a message
// box. Past that the turn stops being a question about evidence and becomes a dump the model skims and the
// transcript has to carry forever, and the request itself starts to risk the engine's own body limits.
export const MAX_ATTACHMENT_BYTES = 256 * 1024;

// Files are read one at a time but sent together, so the per-file limit alone would allow a drop of four files
// to quadruple the turn. The budget is what the per-file limit is to a single file: the message, not the file,
// is the unit that has to stay readable.
export const MAX_TOTAL_ATTACHMENT_BYTES = 512 * 1024;

// Four files is a comparison — this gate against that gate, this log against that one. More than that is a
// directory, and a directory belongs in a worktree the engine can read with its own tools, not in a message.
export const MAX_ATTACHMENTS = 4;

// Enough of the head of a file to tell text from a binary without decoding all of it first.
const SNIFF_BYTES = 8 * 1024;

let sequence = 0;
function nextId(prefix: string): string {
  sequence += 1;
  return `${prefix}-${sequence}`;
}

export function describe(file: File): AttachmentFile {
  return { name: file.name, size: file.size, type: file.type };
}

export function refusal(
  file: AttachmentFile,
  reason: AttachmentRefusalReason,
  why: string,
): AttachmentRefusal {
  return { id: nextId(`refusal-${reason}`), reason, file, why };
}

// "256 KB" rather than "262144": the number in a refusal has to be one the user can compare a file against. It
// rounds up, never down, so a file one byte over a limit cannot be reported as the same size as the limit it broke.
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil((bytes / 1024) * 10) / 10} KB`;
  return `${Math.ceil((bytes / (1024 * 1024)) * 10) / 10} MB`;
}

function extensionOf(name: string): string {
  const slash = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  const base = name.slice(slash + 1);
  const dot = base.lastIndexOf(".");
  return dot <= 0 ? "" : base.slice(dot + 1).toLowerCase();
}

// The fence's info string, so the block says what it is holding. A name with no extension is still text — a
// Makefile, a LICENSE, a `notes` file — and falls through to "text" once its bytes have passed the sniff.
const LANGUAGE_BY_EXTENSION: Readonly<Record<string, string>> = {
  md: "markdown",
  markdown: "markdown",
  mdx: "markdown",
  json: "json",
  jsonc: "json",
  jsonl: "json",
  ndjson: "json",
  csv: "csv",
  tsv: "csv",
  txt: "text",
  text: "text",
  log: "text",
  rst: "text",
  adoc: "text",
  org: "text",
  svg: "xml",
  xml: "xml",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  py: "python",
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "ini",
  sql: "sql",
  go: "go",
  rs: "rust",
  c: "c",
  h: "c",
  cc: "cpp",
  cpp: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  java: "java",
  kt: "kotlin",
  rb: "ruby",
  php: "php",
  swift: "swift",
  lua: "lua",
  r: "r",
  pl: "perl",
  diff: "diff",
  patch: "diff",
  env: "bash",
  cfg: "ini",
  conf: "ini",
};

const LANGUAGE_BY_FILENAME: Readonly<Record<string, string>> = {
  makefile: "makefile",
  dockerfile: "dockerfile",
  license: "text",
  readme: "markdown",
  notice: "text",
};

export function languageOf(file: AttachmentFile): string {
  const base = file.name.slice(Math.max(file.name.lastIndexOf("/"), file.name.lastIndexOf("\\")) + 1).toLowerCase();
  return LANGUAGE_BY_EXTENSION[extensionOf(base)] ?? LANGUAGE_BY_FILENAME[base] ?? "text";
}

// Refused on the name alone, before a byte is read: these are the shapes that never carry a conversation. The
// list is not exhaustive and does not need to be — it exists to save reading a 200 MB archive only to reject it,
// and the byte sniff below catches everything it misses.
const BINARY_EXTENSIONS: ReadonlySet<string> = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "avif", "ico", "bmp", "tif", "tiff", "heic", "psd",
  "zip", "gz", "tgz", "bz2", "xz", "zst", "7z", "rar", "tar", "whl", "egg",
  "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf",
  "exe", "dll", "so", "dylib", "bin", "o", "a", "dmg", "iso", "img", "msi", "apk",
  "mp3", "wav", "flac", "aac", "ogg", "m4a", "mp4", "mov", "avi", "mkv", "webm",
  "woff", "woff2", "ttf", "otf", "eot",
  "pyc", "pyo", "class", "jar", "wasm",
  "sqlite", "db", "parquet", "arrow", "npy", "npz", "pkl", "pickle", "h5", "hdf5", "onnx", "safetensors", "pt", "pth",
]);

const BINARY_MIME_PREFIXES = ["image/", "video/", "audio/", "font/", "model/"];

const BINARY_MIME_TYPES: ReadonlySet<string> = new Set([
  "application/zip",
  "application/gzip",
  "application/x-tar",
  "application/x-7z-compressed",
  "application/x-rar-compressed",
  "application/pdf",
  "application/msword",
  "application/vnd.ms-excel",
  "application/java-archive",
  "application/wasm",
  "application/x-executable",
  "application/x-shockwave-flash",
]);

// The declared type is a hint, not a verdict: a text file dragged off a desktop very often arrives as
// `application/octet-stream` or with no type at all, so neither of those is treated as binary here. Both are
// decided by the bytes instead.
function declaredBinaryWhy(file: AttachmentFile): string | null {
  const type = file.type.toLowerCase();
  // SVG is XML with opinions in it: it reads as text and quotes as text, so it is not refused with the images.
  if (type === "image/svg+xml") return null;
  if (BINARY_MIME_TYPES.has(type)) return `the browser reports it as ${type}, which is not text`;
  if (BINARY_MIME_PREFIXES.some((prefix) => type.startsWith(prefix))) {
    return `the browser reports it as ${type}, which is not text`;
  }
  const extension = extensionOf(file.name);
  if (extension && BINARY_EXTENSIONS.has(extension)) {
    return `a .${extension} file is not text, and would arrive in the message as noise`;
  }
  return null;
}

interface EncodingGuess {
  label: string;
  /** Bytes to skip so the mark itself does not become a stray character at the head of the message. */
  bom: number;
}

// A UTF-16 file is text that a UTF-8 decoder turns into noise, and Windows-authored CSV exports are usually
// UTF-16. The mark decides it outright; without one, NULs in an every-other-byte pattern say the same thing.
function guessEncoding(bytes: Uint8Array): EncodingGuess {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return { label: "utf-8", bom: 3 };
  }
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) return { label: "utf-16le", bom: 2 };
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) return { label: "utf-16be", bom: 2 };
  // Four byte pairs is enough to see the pattern, and both halves of it have to hold: NULs on one side of every
  // pair, readable characters on the other. Counting the NULs alone would call a binary that happens to be full
  // of them a wide-character text file, and refuse it later for a reason that is not the real one.
  if (bytes.length >= 8) {
    const pairs = Math.min(32, Math.floor(bytes.length / 2));
    const wideText = (nulSide: 0 | 1) => {
      let nuls = 0;
      let readable = 0;
      for (let i = 0; i < pairs; i += 1) {
        if (bytes[i * 2 + nulSide] === 0) nuls += 1;
        const other = bytes[i * 2 + (1 - nulSide)];
        if (other === 9 || other === 10 || other === 13 || (other >= 32 && other < 127)) readable += 1;
      }
      return nuls > pairs * 0.8 && readable > pairs * 0.8;
    };
    if (wideText(1)) return { label: "utf-16le", bom: 0 };
    if (wideText(0)) return { label: "utf-16be", bom: 0 };
  }
  return { label: "utf-8", bom: 0 };
}

// A refusal's reason and its words travel together, so nothing downstream has to recognise a sentence to work
// out which of the two it is looking at.
interface NoiseVerdict {
  reason: "binary" | "undecodable";
  why: string;
}

const NOISE = ", so it would arrive in the message as noise.";

/**
 * Reads the head of a file and says whether it is already noise, in words meant for the person who dropped it.
 * Only ever called on bytes taken to be single-byte text, so a NUL is never legitimate in them.
 */
function headNoise(bytes: Uint8Array): NoiseVerdict | null {
  let nul = 0;
  let control = 0;
  for (const byte of bytes) {
    if (byte === 0) nul += 1;
    else if (byte < 32 && byte !== 9 && byte !== 10 && byte !== 13) control += 1;
  }
  if (nul > 0) return { reason: "binary", why: `it contains NUL bytes${NOISE}` };
  if (bytes.length > 0 && control / bytes.length > 0.02) {
    return { reason: "binary", why: `it is mostly control characters${NOISE}` };
  }
  return null;
}

// The head can be clean and the rest of the file not: a log with a binary tail, a truncated multi-byte
// character, a text file that is really something else. The decoded text gets the same question asked again.
function decodedNoise(text: string): NoiseVerdict | null {
  let replaced = 0;
  let control = 0;
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code === 0xfffd) replaced += 1;
    else if (code === 0) return { reason: "binary", why: `it contains NUL bytes${NOISE}` };
    else if (code < 32 && code !== 9 && code !== 10 && code !== 13) control += 1;
  }
  // A replacement character is the decoder admitting it could not read the bytes. This surface carries evidence
  // into the message verbatim, so substituting a character the file never contained is worse than refusing it.
  if (replaced > 0) {
    return {
      reason: "undecodable",
      why:
        "its bytes are not valid text in the encoding they claim, and the parts that failed to decode could only " +
        "have been sent as replacement characters — a character the file never contained is not evidence.",
    };
  }
  if (text.length > 0 && control / text.length > 0.02) {
    return { reason: "binary", why: `it is mostly control characters${NOISE}` };
  }
  return null;
}

/**
 * The whole policy, in one call: what the file says it is, then how big it is, then what its bytes are. The
 * first two cost nothing and are decided before the file is read into memory at all — a 10 MB archive is refused
 * as an archive rather than as something too large, because "too large" would suggest that a smaller one of the
 * same kind would be welcome.
 */
export async function readAttachment(file: File): Promise<AttachmentOutcome> {
  const described = describe(file);

  const declared = declaredBinaryWhy(described);
  if (declared) {
    return {
      ok: false,
      refusal: refusal(
        described,
        "binary",
        `${declared}. Text, source, markdown, JSON and CSV are what this conversation can carry; anything else would arrive as noise.`,
      ),
    };
  }

  if (file.size > MAX_ATTACHMENT_BYTES) {
    return {
      ok: false,
      refusal: refusal(
        described,
        "too-large",
        `${formatBytes(file.size)} is over the ${formatBytes(MAX_ATTACHMENT_BYTES)} limit for one attached file. ` +
          `There is no upload route on this engine: the file's text goes into the message itself, so the limit is the message staying readable. ` +
          `Paste the part that matters, or point the engine at the path and let it read the file with its own tools.`,
      ),
    };
  }

  if (file.size === 0) {
    return {
      ok: false,
      refusal: refusal(described, "unreadable", "it is empty, so there is nothing to put in the message"),
    };
  }

  let bytes: Uint8Array;
  try {
    bytes = new Uint8Array(await file.arrayBuffer());
  } catch (error) {
    return {
      ok: false,
      refusal: refusal(
        described,
        "unreadable",
        `the browser could not read it (${error instanceof Error ? error.message : String(error)}). ` +
          `A file moved or closed between the drop and the read does this; dropping it again usually works.`,
      ),
    };
  }

  const encoding = guessEncoding(bytes);
  if (encoding.label === "utf-8") {
    const head = headNoise(bytes.subarray(0, SNIFF_BYTES));
    if (head) {
      return { ok: false, refusal: refusal(described, head.reason, head.why) };
    }
  }

  let text: string;
  try {
    text = new TextDecoder(encoding.label).decode(bytes.subarray(encoding.bom));
  } catch {
    return {
      ok: false,
      refusal: refusal(
        described,
        "undecodable",
        `it could not be decoded as ${encoding.label}, so there is no text to put in the message.`,
      ),
    };
  }

  const noise = decodedNoise(text);
  if (noise) {
    return { ok: false, refusal: refusal(described, noise.reason, noise.why) };
  }

  // Trailing whitespace is trimmed and nothing else is touched: line endings and interior spacing are part of
  // what the file says, and rewriting them would make the attachment a paraphrase of the evidence.
  const cleaned = text.replace(/\s+$/, "");

  return {
    ok: true,
    attachment: {
      id: nextId("attachment"),
      name: described.name,
      size: described.size,
      language: languageOf(described),
      lines: cleaned.length === 0 ? 0 : cleaned.split("\n").length,
      text: cleaned,
    },
  };
}

/**
 * The checks that need to know what is already attached, so they run before a file is read rather than after.
 * `current` is the list as it stands, which is why the hook calls this once per file and keeps a running total.
 */
export function admissionRefusal(file: AttachmentFile, current: Attachment[]): AttachmentRefusal | null {
  const already = current.find((a) => a.name === file.name && a.size === file.size);
  if (already) {
    return refusal(
      file,
      "duplicate",
      `${file.name} (${formatBytes(file.size)}) is already attached. Remove it first if the drop was meant to replace it.`,
    );
  }
  if (current.length >= MAX_ATTACHMENTS) {
    return refusal(
      file,
      "over-budget",
      `${MAX_ATTACHMENTS} files are already attached, which is the limit for one message. More than that is a directory, and a directory belongs somewhere the engine can read it with its own tools.`,
    );
  }
  const total = current.reduce((sum, a) => sum + a.size, 0) + file.size;
  if (total > MAX_TOTAL_ATTACHMENT_BYTES) {
    return refusal(
      file,
      "over-budget",
      `attaching ${file.name} would take this message to ${formatBytes(total)}, over the ${formatBytes(
        MAX_TOTAL_ATTACHMENT_BYTES,
      )} limit for everything attached to one turn. The files are sent as message text, so the limit is the turn staying readable.`,
    );
  }
  return null;
}

// A fence longer than any run of backticks inside the text, so a markdown or shell file cannot close its own
// block early and spill the rest of the message into the transcript as prose.
function fenceFor(text: string): string {
  let longest = 0;
  let run = 0;
  for (const char of text) {
    if (char === "`") {
      run += 1;
      if (run > longest) longest = run;
    } else {
      run = 0;
    }
  }
  return "`".repeat(Math.max(3, longest + 1));
}

/** One attachment as it appears in the outgoing message: a header naming it, then its text in a fence. */
export function attachmentBlock(attachment: Attachment): string {
  const fence = fenceFor(attachment.text);
  const header = `Attached file: ${attachment.name} (${formatBytes(attachment.size)}, ${
    attachment.lines === 1 ? "1 line" : `${attachment.lines.toLocaleString("en-US")} lines`
  }, ${attachment.language})`;
  return `${header}\n${fence}${attachment.language}\n${attachment.text}\n${fence}`;
}

/**
 * The message the user actually sends: what they typed, then each attached file's text. With nothing typed the
 * attachments alone are the message, because a dropped file is a thing to ask about without a question attached.
 */
export function composeMessage(typed: string, attachments: Attachment[]): string {
  const parts = [typed.trim(), ...attachments.map(attachmentBlock)].filter((part) => part !== "");
  return parts.join("\n\n");
}
