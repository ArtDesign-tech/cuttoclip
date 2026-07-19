import { randomBytes } from "node:crypto";
import { loadGatewayEnvironment } from "./env.js";

loadGatewayEnvironment();

const { loadConfig } = await import("./app.js");
const { GatewayStore } = await import("./store.js");
const config = loadConfig();
const store = new GatewayStore(config.dbPath);

try {
  const [resource, action, ...rest] = process.argv.slice(2);
  if (resource === "invite" && action === "create") {
    const label = option(rest, "--label");
    const hours = Number(option(rest, "--expires-hours") ?? "168");
    if (!label || !Number.isInteger(hours) || hours < 1 || hours > 24 * 90) {
      throw new Error("Usage: npm.cmd run admin:gateway -- invite create --label <name> [--expires-hours 168]");
    }
    const code = randomBytes(32).toString("base64url");
    const expiresAt = new Date(Date.now() + hours * 60 * 60 * 1_000).toISOString();
    store.createInvite(code, label, expiresAt);
    console.log(`Invite for ${label} (displayed once): ${code}`);
    console.log(`Expires: ${expiresAt}`);
  } else if (resource === "installation" && action === "list") {
    console.table(store.listInstallations());
  } else if (resource === "installation" && action === "revoke") {
    const id = option(rest, "--id");
    if (!id) throw new Error("Usage: npm.cmd run admin:gateway -- installation revoke --id <installation-id>");
    if (!store.revokeInstallation(id)) throw new Error("Installation was not found.");
    console.log(`Installation ${id} revoked.`);
  } else {
    throw new Error("Commands: invite create, installation list, installation revoke");
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
} finally {
  store.close();
}

function option(args: string[], name: string) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}
