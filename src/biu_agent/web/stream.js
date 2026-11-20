// Fetch streams may split both a UTF-8 character and a CRLF boundary.
export async function readEvents(stream, consume) {
  const reader = stream.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    name = "",
    lines = [];
  function process(input) {
    buffer += input;
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end).replace(/\r$/, "");
      buffer = buffer.slice(end + 1);
      if (!line) {
        if (name && lines.length) consume(name, JSON.parse(lines.join("\n")));
        name = "";
        lines = [];
      } else if (line.startsWith("event:")) {
        name = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        lines.push(line.slice(5).trimStart());
      }
    }
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      process(decoder.decode(value, { stream: true }));
    }
    process(decoder.decode());
  } finally {
    reader.releaseLock();
  }
}
