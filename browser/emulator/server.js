"use strict";

// JSON-lines protocol: one {"action","target","value"} request per line on stdin,
// one {"success","data","error"} response per line on stdout.
const readline = require("node:readline");
const { EmulatorSession, EmulatorError } = require("./session");

function serve(input = process.stdin, output = process.stdout, session = new EmulatorSession()) {
  const lines = readline.createInterface({ input });
  let queue = Promise.resolve();
  lines.on("line", (line) => {
    queue = queue.then(async () => {
      let response;
      try {
        const request = JSON.parse(line);
        const data = await session.execute(request.action, request.target, request.value);
        response = { success: true, data };
      } catch (error) {
        const message = error instanceof EmulatorError ? error.message : String(error);
        response = { success: false, error: message };
      }
      output.write(JSON.stringify(response) + "\n");
    });
  });
  return lines;
}

if (require.main === module) serve();

module.exports = { serve };
