import process from "node:process";

import { FilteredStdioServerTransport } from "@wonderwhy-er/desktop-commander/dist/custom-stdio.js";
import { configManager } from "@wonderwhy-er/desktop-commander/dist/config-manager.js";
import { server, flushDeferredMessages } from "@wonderwhy-er/desktop-commander/dist/server.js";
import { featureFlagManager } from "@wonderwhy-er/desktop-commander/dist/utils/feature-flags.js";

globalThis.disableOnboarding = true;
const transport = new FilteredStdioServerTransport();
globalThis.mcpTransport = transport;

await configManager.loadConfig();
await featureFlagManager.initialize();

server.oninitialized = () => {
  transport.enableNotifications();
  flushDeferredMessages();
};

await server.connect(transport);

process.on("SIGTERM", async () => {
  await server.close().catch(() => {});
  process.exit(0);
});
