# Rich User State (Default JSON)

```json
{
  "identity": {
    "name": "",
    "preferred_title": "",
    "language_mix": "Hinglish",
    "audio_preference": "voice_and_text",
    "gender": "",
    "regional_context": "IN",
    "timezone": "",
    "timezone_source": ""
  },
  "physiology": {
    "biometrics": {
      "weight": 0,
      "target": 0,
      "height": 0,
      "body_fat_est": 0
    },
    "injuries": [],
    "medical_flags": []
  },
  "psychology": {
    "motivation_style": "gentle_nudge",
    "core_why": "",
    "engagement_score": 0.5,
    "quit_signals": []
  },
  "lifestyle": {
    "training_environment": "",
    "available_equipment": [],
    "preferred_workout_time": "",
    "dietary_restrictions": []
  },
  "logs": {
    "current_day": {
      "cals": 0,
      "water": 0,
      "workout_complete": false
    },
    "nutrition_log": [
      {
        "summary": "",
        "estimated_calories": 0,
        "estimated_macros": {"protein_g": 0, "carbs_g": 0, "fat_g": 0},
        "confidence": "medium",
        "source": "text",
        "meal_slot": "breakfast",
        "event_time_source": "message_time_inferred",
        "timezone": "Asia/Kolkata",
        "local_date": "",
        "logged_at_local": "",
        "logged_at": ""
      }
    ],
    "activity_log": [
      {
        "name": "",
        "duration_mins": 0,
        "burn_cals": 0,
        "source": "text",
        "session_slot": "evening_session",
        "event_time_source": "message_time_inferred",
        "timezone": "Asia/Kolkata",
        "local_date": "",
        "logged_at_local": "",
        "logged_at": ""
      }
    ],
    "volume_trends": [],
    "last_3_workout_summaries": []
  },
  "plans": {
    "active": {
      "plan_id": "",
      "version": 0,
      "status": "none",
      "type": "hybrid",
      "horizon": "weekly",
      "horizon_weeks": 0,
      "constraints": {},
      "current_block": {},
      "week_blocks": [],
      "day_actions": [],
      "pending_change_request": {}
    },
    "change_log": []
  },
  "onboarding": {
    "is_active": true,
    "pending_fields": [],
    "last_seen_at": "",
    "last_updated_at": "",
    "completed_at": "",
    "session_expired_at": ""
  }
}
```
