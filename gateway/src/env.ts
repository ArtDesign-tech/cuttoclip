import { config as loadDotenv } from "dotenv";
import { fileURLToPath } from "node:url";

/** Loads gateway/.env consistently from source, built output, and manual scripts. */
export function loadGatewayEnvironment() {
  const path = fileURLToPath(new URL("../.env", import.meta.url));
  loadDotenv({ path, quiet: true });
  return path;
}
