import assert from "node:assert/strict";
import test from "node:test";
import { readEvents } from "../src/biu_agent/web/stream.js";

test("reconstructs multiple SSE frames with CRLF and every UTF-8 byte split", async () => {
  const source = 'event: text.delta\r\ndata: {"text":"第二张","seq":1}\r\n\r\nevent: turn.done\ndata: {"seq":2}\n\n';
  const bytes = new TextEncoder().encode(source);
  const input = new ReadableStream({start(controller) {
    for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
    controller.close();
  }});
  const received = [];
  await readEvents(input, (name, data) => received.push([name, data]));
  assert.deepEqual(received, [["text.delta", {text:"第二张", seq:1}], ["turn.done",{seq:2}]]);
});
