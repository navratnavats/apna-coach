# Apna Coach: 10-Agent Roster

1. **Front-Desk:** Acknowledges user input in <2s. Goal: Buy time.
2. **Vibe Architect:** Detects language/tone. Mirrors "Bhai/Bro" or professional tone. Sets `persona_vibe` flag.
3. **Intake:** Manages onboarding (Age, weight, target, injuries).
4. **Bio-Math:** Logic/math agent. Calculates TDEE, Macros, and volume progression.
5. **Medical Safety:** The "Safety Gate." Audits all plans for injury risks (especially the knee).
6. **Nutritionist:** Expert in Indian foods (Poha, Roti, Paneer, Canteen meals).
7. **Fitness Pro:** Designs workouts (Progressive overload + Injury-safe).
8. **Hype-Man:** The "Motivational Seller." Sends proactive nudges/audio hype.
9. **Critic:** Final quality check. Rejects any response that is generic or unsafe.
10. **Memory:** Silent agent. Updates the "Living User Profile" (JSON) in Supabase.
