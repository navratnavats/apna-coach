# The Living User Profile: Master Schema

{
  "identity": {
    "name": "string",
    "preferred_title": "Bhai/Dost/Sir/Ji",
    "language_mix": "Hinglish/English/Pure Hindi (Devanagari)",
    "audio_preference": "voice_and_text", // Can switch to "text_only" if user is at work/college
    "gender": "M/F/O",
    "regional_context": "IN/US/UK/EU"
  },
  "physiology": {
    "biometrics": { "weight": 0, "target": 0, "height": 0, "body_fat_est": 0 },
    "injuries": [{
      "part": "string", 
      "severity": "low/mid/high", 
      "history": "string",
      "pain_triggers": []
    }],
    "medical_flags": ["menstrual_cycle_tracking", "heart_condition", "asthma"]
  },
  "psychology": {
    "motivation_style": "tough_love/gentle_nudge",
    "core_why": "Wedding/Beach/Health",
    "engagement_score": 0.0 to 1.0,
    "quit_signals": ["missed_logging_2days", "negative_sentiment"]
  },
  "lifestyle": {
    "training_environment": "home/commercial_gym/park/traveling",
    "available_equipment": ["dumbbells", "resistance_band", "pull_up_bar", "none"],
    "preferred_workout_time": "18:00",
    "dietary_restrictions": ["veg", "non-veg", "vegan", "picky_eater"]
  },
  "logs": {
    "current_day": { "cals": 0, "water": 0, "workout_complete": false },
    "volume_trends": [],
    "last_3_workout_summaries": []
  }
}