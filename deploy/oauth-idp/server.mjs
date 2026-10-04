import { createServer } from "node:http";
import { pathToFileURL } from "node:url";
import { createIdp } from "./idp.mjs";

const list = (value) =>
  String(value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

export function configFromEnv(env = process.env) {
  return {
    secret: env.IDP_SECRET,
    publicUrl: env.IDP_PUBLIC_URL,
    githubClientId: env.IDP_GITHUB_CLIENT_ID,
    githubClientSecret: env.IDP_GITHUB_CLIENT_SECRET,
    allowedGithubIds: list(env.IDP_ALLOWED_GITHUB_IDS),
    allowedResources: list(env.IDP_ALLOWED_RESOURCES),
    allowedRedirectHosts: env.IDP_ALLOWED_REDIRECT_HOSTS ? list(env.IDP_ALLOWED_REDIRECT_HOSTS) : undefined,
    scopes: list(env.IDP_SCOPES),
    ownerPassphrase: env.IDP_OWNER_PASSPHRASE || undefined,
    notBefore: env.IDP_NOT_BEFORE ? Number(env.IDP_NOT_BEFORE) : 0,
  };
}

export function startServer(env = process.env) {
  const idp = createIdp(configFromEnv(env));
  if (!idp.ready) process.stderr.write(`${JSON.stringify({ event: "idp_misconfigured", missing: idp.missing })}\n`);
  const server = createServer((request, response) => {
    idp.handle(request, response).catch(() => response.destroy());
  });
  server.listen(Number(env.PORT ?? 8080));
  return { server, idp };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { server } = startServer();
  process.on("SIGTERM", () => server.close(() => process.exit(0)));
}
