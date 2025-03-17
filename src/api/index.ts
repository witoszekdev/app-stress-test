import { createAppRegisterHandler, createManifestHandler } from "@saleor/app-sdk/handlers/fetch-api";
import { Hono } from "hono";
import packageJson from "../../package.json";
import { getApl } from "../saleor-app";
import { Bindings } from "../bindings";
import webhookRotues from "./webhooks";
import { getOrderCreatedWebhook } from "./webhooks/order-created";

const app = new Hono<{ Bindings: Bindings }>();
app.get("/manifest", c => createManifestHandler({
  async manifestFactory({ appBaseUrl }) {
    const uuid = crypto.randomUUID();
    return {
      name: `Test app - ${uuid}`,
      tokenTargetUrl: `${appBaseUrl}/api/register`,
      appUrl: `${appBaseUrl}/app`,
      permissions: [
        "MANAGE_ORDERS",
      ],
      id: `saleor.app.stress-test:${uuid}`,
      version: packageJson.version,
      webhooks: [
        getOrderCreatedWebhook(c).getWebhookManifest(appBaseUrl)
      ],
      extensions: [],
      author: "Jonatan Witoszek",
    }
  }
})(c.req.raw));

// app.post("/register", c => new Response("Error", { status: 500 }));

app.post("/register", c => createAppRegisterHandler({
  apl: getApl(c),
})(c.req.raw));

app.route("/webhooks", webhookRotues)

export default app;
