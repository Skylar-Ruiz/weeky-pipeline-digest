import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

const MAX_PAGE_TEXT = 60_000;

const REPORT_LABELS = {
  weekly: "Weekly Dashboard Digest (pipeline, MQLs, SALs, Opps Created, pacing against Q1 targets)",
  events: "Events Pipeline Report (event campaigns, events-attributed pipeline by stage)",
  email:  "Email Performance Report (email programs, open rates, CTR, newsletter deep dive)",
};

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });

  const { question, pageText, reportType } = req.body ?? {};

  if (!question || typeof question !== "string" || !question.trim()) {
    return res.status(400).json({ error: "Missing question" });
  }
  if (!pageText || typeof pageText !== "string") {
    return res.status(400).json({ error: "Missing page content" });
  }

  const context = pageText.slice(0, MAX_PAGE_TEXT);
  const label = REPORT_LABELS[reportType] ?? "Digest Report";

  const systemPrompt = `You are an AI assistant embedded in Delight's ${label}.

Your job is to answer questions from marketing and revenue leaders about the data in this report.

Rules:
- Answer ONLY using facts present in the report text provided. Do not invent numbers.
- Be concise and direct — executives want the answer in 2–4 sentences, not a paragraph.
- If a number is not in the report, say "That figure isn't in this report" rather than guessing.
- Use the same terminology as the report (e.g. "Opps Created", "Discovery", "Qualified").
- Format currency as $X.XM or $XK to match the report style.
- If the user asks for context or "why", give a brief interpretation grounded in the report data.
- This is an internal analytics digest for delight.ai, covering FY2027 Q1 performance.`;

  try {
    const response = await client.messages.create({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 512,
      system: systemPrompt,
      messages: [{
        role: "user",
        content: `REPORT CONTENT:\n${context}\n\nQUESTION: ${question.trim()}`,
      }],
    });

    const answer = response.content[0]?.text ?? "Sorry, I couldn't generate an answer.";
    return res.status(200).json({ answer });
  } catch (err) {
    console.error("Claude API error:", err);
    return res.status(err.status ?? 500).json({ error: "Failed to get answer from AI. Please try again." });
  }
}
