import { loadGatewayEnvironment } from "./env.js";
import { buildApp, loadConfig } from "./app.js";

loadGatewayEnvironment();
const config = loadConfig();
const app = await buildApp({ config });

try {
  await app.listen({ port: config.port, host: config.host });
} catch (error) {
  app.log.error(error);
  process.exitCode = 1;
}

let closing = false;
const shutdown = async (signal: string) => {
  if (closing) return;
  closing = true;
  app.log.info({ signal }, "Gateway shutting down");
  const deadline = setTimeout(() => process.exit(1), 30_000);
  try {
    await app.close();
    process.exitCode = 0;
  } finally {
    clearTimeout(deadline);
  }
};

process.once("SIGINT", () => void shutdown("SIGINT"));
process.once("SIGTERM", () => void shutdown("SIGTERM"));
