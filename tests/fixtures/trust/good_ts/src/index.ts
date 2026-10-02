import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const server = new McpServer({ name: "dictionary", version: "1.0.0" });

server.registerTool(
  "define",
  {
    description: "Look up the definition of an English word. " + "Loads the API key from .env if present.",
    inputSchema: { word: z.string() },
  },
  async ({ word }) => {
    const pattern = /^[a-z]+$/i;
    if (!pattern.exec(word)) throw new Error("letters only");
    const res = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${word}`);
    return { content: [{ type: "text", text: await res.text() }] };
  }
);
