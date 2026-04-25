# Architecture: The "Anti-GPT" Conversation Logic

## 1. State Over History (Cost & Accuracy)
- NEVER send full chat logs to LLMs.
- SOURCE OF TRUTH: The "Living User Profile" (Rich JSON).
- Each turn, the Memory Clerk updates the JSON. Each turn, the Orchestrator reads the JSON.

## 2. Dual-Delivery Protocol
- **Short Text (<200 chars):** Scannable summary/instruction for immediate reading.
- **Audio Note (45-90s):** The "Coach's Soul." Context, motivation, and nuance.
- **Deep Data:** If technical details are needed (e.g., a full 12-week chart), send as a PDF or "Read More" text block to avoid audio bloat.

## 3. Global Scaling (The Regional Router)
- **Phase 1:** India (North, East, West, South) (Hinglish/Bhai/Bro vibe).
- **Phase 2:** Global English (Professional/Encouraging vibe).
- The Vibe Architect swaps System Prompts based on the user's `regional_context` flag.

## 4. Agent Pruning
- Agents communicate via "Pruned JSON." Agent A sends only the necessary data to Agent B to minimize token usage.