/**
 * A small JSON tokeniser for the tool argument blocks.
 *
 * Hand written rather than a highlighting library: the only grammar the replay needs is
 * JSON, the tokens are five classes wide, and shipping a general purpose highlighter to
 * the browser to colour a twelve line object is not a trade worth making.
 */

export interface Token {
  text: string;
  /** A `tok-*` class from globals.css, or null for whitespace and structure. */
  cls: string | null;
}

const PATTERN =
  /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],])/g;

/** Above this many characters, highlighting is skipped and the text is rendered plain. */
export const HIGHLIGHT_LIMIT = 20_000;

/**
 * Hard ceiling on how much of one tool output reaches the browser.
 *
 * Applied on the server when the replay props are built, and again in the pane that
 * renders them. A task whose workspace holds a forty thousand line log can produce a step
 * output far larger than anything a person will read in a browser, and the page has to
 * stay usable rather than faithful past this point. The full text is always in the run
 * record on disk.
 */
export const OUTPUT_DISPLAY_CAP = 262_144;

export function tokenizeJson(source: string): Token[] {
  const tokens: Token[] = [];
  let last = 0;

  for (const match of source.matchAll(PATTERN)) {
    const start = match.index;
    if (start == null) continue;
    if (start > last) {
      tokens.push({ text: source.slice(last, start), cls: null });
    }

    const [whole, stringLiteral, colon, keyword, numberLiteral, punctuation] = match;
    if (stringLiteral != null) {
      if (colon != null) {
        tokens.push({ text: stringLiteral, cls: "tok-key" });
        tokens.push({ text: colon, cls: "tok-punct" });
      } else {
        tokens.push({ text: stringLiteral, cls: "tok-str" });
      }
    } else if (keyword != null) {
      tokens.push({ text: keyword, cls: keyword === "null" ? "tok-null" : "tok-bool" });
    } else if (numberLiteral != null) {
      tokens.push({ text: numberLiteral, cls: "tok-num" });
    } else if (punctuation != null) {
      tokens.push({ text: punctuation, cls: "tok-punct" });
    }
    last = start + whole.length;
  }

  if (last < source.length) {
    tokens.push({ text: source.slice(last), cls: null });
  }
  return tokens;
}

/** Stringify tool arguments for display, never throwing on odd input. */
export function stringifyArgs(args: unknown): string {
  try {
    return JSON.stringify(args, null, 2) ?? String(args);
  } catch {
    return String(args);
  }
}

/** Count lines without materialising an array, for logs that run to tens of thousands. */
export function countLines(text: string): number {
  if (text.length === 0) return 0;
  let lines = 1;
  for (let i = text.indexOf("\n"); i !== -1; i = text.indexOf("\n", i + 1)) {
    lines += 1;
  }
  return lines;
}

/** The first `limit` lines of a string, without splitting the whole thing. */
export function headLines(text: string, limit: number): string {
  let index = -1;
  for (let seen = 0; seen < limit; seen += 1) {
    const next = text.indexOf("\n", index + 1);
    if (next === -1) return text;
    index = next;
  }
  return text.slice(0, index);
}
