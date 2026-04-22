# Architecture: The Anti-GPT Conversation Logic

- **State over History:** NEVER send full chat logs to LLMs. Use the "Living User Profile" (JSON) from Supabase as the source of truth.
- **Dual-Delivery:** Every response = Short Text (<200 chars) + Audio Note (<60s).
- **Agent Pruning:** Agents communicate via pruned JSON to save tokens.
- **Proactive Nudging:** System uses a Cron Job to "poke" the user for workouts.
