from __future__ import annotations


def _onboarding_label_map() -> dict[str, str]:
    return {
        "name": "Name",
        "age": "Age",
        "height": "Height (cm)",
        "weight": "Current weight (kg)",
        "target_weight": "Target weight (kg)",
        "gender": "Gender",
        "core_why": "Aapka main reason: kya achieve karna hai, kyun karna hai, aur kab tak karna hai. Voice note chalega.",
        "injuries": "Koi injury/pain/body related issue like dibates, sprains, HighBloodPressure, etc. hai to body part, pain kitna hai, kis movement me trigger hota hai, aur medicine/dawa ho to batayiye. Nahi hai to 'none'. Voice note chalega.",
        "equipment": "Aap home pe train karte ho ya gym me? Kaunsa samaan available hai (dumbbells, bands, machine, etc.) batayiye. Voice note chalega.",
    }


def _render_onboarding_fields(fields: list[str]) -> str:
    label_map = _onboarding_label_map()
    ordered = []
    for key in fields:
        label = label_map.get(key, key)
        if label not in ordered:
            ordered.append(label)
    return "\n".join([f"- {item}" for item in ordered]) if ordered else "- Basic details"


def with_address(address: str, body: str) -> str:
    safe_address = (address or "").strip() or "Buddy"
    return f"{safe_address}, {body}"


def ack_audio() -> str:
    return "Audio sun raha hoon, thoda sa time do."


def ack_image() -> str:
    return "Photo mil gayi, macros nikaal raha hoon. Ek sec."


def ack_long_text() -> str:
    return "Lamba sawal hai, aapka data check karke exact answer deta hoon."


def ack_default() -> str:
    return "Got it, processing kar raha hoon."


def policy_out_of_scope() -> str:
    return (
        "Ye request app scope ke bahar hai. "
        "Fitness, nutrition, workout, recovery, ya plan related puchhiye."
    )


def unknown_query_clarifier(address: str) -> str:
    return (
        f"{address}, mujhe message exact clear nahi hua. Ek line me bolo aapko kya chahiye:\n"
        "- aaj ka burn/deficit numbers\n"
        "- aaj kya khaya (food recall)\n"
        "- workout plan\n"
        "- existing plan edit/status\n"
        "- profile update"
    )


def onboarding_missing_biometrics() -> str:
    return "Onboarding almost done. Bas ek final cheez: height, age, aur gender clear kijiye."


def image_rejection() -> str:
    return (
        "Main aapka fitness coach hoon, random image analyzer nahi. "
        "Khana, gym equipment, ya physique related photo bhejiye."
    )


def pipeline_busy_retry() -> str:
    return "AI service abhi thoda busy hai. Kripya 20-30 seconds me dobara ping kijiye."


def trial_limit_warning(*, used_turns: int, daily_limit: int) -> str:
    return (
        f"\n\nQuick heads-up: Aaj aap {used_turns}/{daily_limit} trial turns use kar chuke hain. "
        "Limit ke kareeb hain."
    )


def trial_limit_wall(*, daily_limit: int) -> str:
    return (
        f"Aaj ka trial limit ({daily_limit} turns) complete ho gaya. "
        "Kal midnight ke baad quota reset ho jayega. Agar urgent fitness query hai, "
        "ek short note bhejiye, main concise guidance dunga."
    )


def intake_profile_complete(address: str) -> str:
    return with_address(address, "profile complete hai. Aaj ka plan banate hain.")


def intake_basic_stats(address: str) -> str:
    return (
        f"Welcome {address}! Plan lock karne se pehle basic stats dijiye: "
        "age, height (cm), aur current weight (kg) kya hai?"
    )


def intake_name() -> str:
    return "Welcome! Start karte hain - aapko kis naam se address karun?"


def intake_bulk_details(address: str, missing_fields: list[str]) -> str:
    asks = _render_onboarding_fields(missing_fields)
    return (
        f"{address}, onboarding fast-track karte hain.\n"
        "Aap ye details ek message me ya multiple chunks me bhej sakte hain:\n"
        f"{asks}\n"
        "Agar type karna mushkil ho to voice note bhej dijiye.\n"
        "Main har message ke baad remaining fields track karta rahunga.\n"
        "Quick options: 'kya bacha hai / what is left', 'onboarding status', "
        "'is step ka matlab / what does this step mean', "
        "'onboarding edit', 'onboarding restart'."
    )


def intake_resume_after_timeout(address: str, missing_fields: list[str]) -> str:
    line_items = _render_onboarding_fields(missing_fields)
    return (
        f"{address}, onboarding session resume karte hain. Koi tension nahi — "
        f"abhi sirf remaining details chahiye:\n{line_items}\n"
        "Quick options: 'kya bacha hai / what is left', 'onboarding status', "
        "'is step ka matlab / what does this step mean', "
        "'onboarding edit', 'onboarding restart'."
    )


def onboarding_policy_redirect(address: str, pending_fields: list[str]) -> str:
    pending = _render_onboarding_fields(pending_fields)
    return (
        f"{address}, onboarding complete karne ke liye please profile details par hi reply kijiye: "
        f"\n{pending}\nNormal coaching questions onboarding complete hote hi full start ho jayenge.\n"
        "Quick options: 'kya bacha hai / what is left', 'onboarding status', "
        "'is step ka matlab / what does this step mean', "
        "'onboarding edit', 'onboarding restart'."
    )


def onboarding_capability_locked(address: str, pending_fields: list[str]) -> str:
    pending = _render_onboarding_fields(pending_fields)
    return (
        f"{address}, main features detail me onboarding complete hote hi share karunga.\n"
        f"Abhi pehle ye fields complete karte hain:\n{pending}\n"
        "Aap ek message ya chunks me details bhej sakte ho."
    )


def onboarding_restart_done(address: str) -> str:
    return (
        f"{address}, onboarding reset kar diya. Chaliye fresh start karte hain.\n"
        "Aap details ek message me ya chunks me bhej sakte hain."
    )


def onboarding_help(address: str) -> str:
    return (
        f"{address}, onboarding quick commands (Hinglish friendly):\n"
        "- kya bacha hai / what is left\n"
        "- onboarding status / complete hua?\n"
        "- is step ka matlab / what does this step mean\n"
        "- onboarding edit\n"
        "- onboarding restart\n"
        "Aap normal text/voice me details bhejoge to main auto-map karta rahunga."
    )


def onboarding_status(address: str, *, is_complete: bool, pending_fields: list[str]) -> str:
    if is_complete:
        return f"{address}, onboarding complete hai. Aap full coaching mode me ho. ✅"
    pending = _render_onboarding_fields(pending_fields)
    return (
        f"{address}, onboarding abhi complete nahi hua.\n"
        f"Remaining fields:\n{pending}\n"
        "Aap ek-ek karke, chunks me, ya detailed audio me bhej sakte ho."
    )


def onboarding_field_explanations(address: str, pending_fields: list[str]) -> str:
    explain_map = {
        "name": "- Name: jis naam se aapko address karna hai.",
        "age": "- Age: years me.",
        "height": "- Height: cm me (ft/in bologe to bhi main map kar lunga).",
        "weight": "- Current weight: abhi ka body weight kg me.",
        "target_weight": "- Target weight: aap kis weight tak jana chahte ho (kg).",
        "gender": "- Gender: male/female (sirf calculation accuracy ke liye).",
        "core_why": "- Core why: aap transformation kyun chahte ho + by when. Example: Dec wedding tak 10kg fat loss.",
        "injuries": "- Injuries: body part, pain level, kis movement pe trigger hota hai, meds if any.",
        "equipment": "- Equipment/setup: home/gym + available items (dumbbells, bands, etc).",
    }
    lines = [explain_map.get(k) for k in pending_fields if explain_map.get(k)]
    if not lines:
        lines = ["- Basic profile details to personalize coaching."]
    return (
        f"{address}, is step ka matlab ye hai:\n"
        + "\n".join(lines)
        + "\nAap details text ya voice note dono me bhej sakte ho."
    )


def onboarding_edit_help(address: str) -> str:
    return (
        f"{address}, bilkul. Aap onboarding fields edit kar sakte hain.\n"
        "Update format (one per line) use kijiye:\n"
        "- name: ...\n"
        "- age: ...\n"
        "- height: ... cm\n"
        "- weight: ... kg\n"
        "- target: ... kg\n"
        "- gender: male/female\n"
        "- core why: ...\n"
        "- injuries: ... (or none)\n"
        "- equipment: ...\n"
        "Jo field change karni hai sirf wahi bhejiye."
    )


def intake_confirm_gender(address: str) -> str:
    return with_address(address, "ek cheez confirm kijiye: male ya female?")


def intake_target_weight(address: str) -> str:
    return f"Target weight kya set karna hai {address}? (kg me batayiye)"


def intake_core_why() -> str:
    return (
        "Solid. Ab *core why* batayiye — matlab aapki transformation ka real reason.\n"
        "Format: goal + reason + deadline\n"
        "Example:\n"
        "- December me sister wedding hai, 10kg fat loss chahiye\n"
        "- 21km run complete karna hai within 4 months"
    )


def intake_injury(address: str) -> str:
    return (
        f"Koi injury/medical issue hai kya {address}? "
        "Agar kuch nahi hai toh seedha 'none' likh dijiye."
    )


def intake_equipment(address: str) -> str:
    return (
        f"Aap kis setup pe train karte hain {address}? "
        "Gym ya ghar pe kya equipment hai (dumbbells/bands/pull-up bar etc)?"
    )


def intake_generic(address: str) -> str:
    return with_address(
        address,
        "onboarding ke kuch details pending hain. Ek message me jitna ho sake share kijiye.",
    )


def graduation_with_targets(address: str, cals: int, protein: int) -> str:
    return (
        f"Stats locked in {address}. TDEE calculated. Aapka daily target {cals} cals "
        f"aur minimum {protein}g protein hai. Ab asli kaam shuru karte hain. 💪"
    )


def graduation_generic(address: str) -> str:
    return (
        f"Stats locked in {address}. Profile complete ho gaya. Ab se har din "
        "main aapko data-driven plan aur feedback dunga. 💪"
    )


def coach_historical_not_found(address: str) -> str:
    return (
        f"{address}, us date ka exact archive nahi mila. Date dubara clear batayiye "
        "(example: Tuesday, yesterday, 2026-04-22)."
    )


def coach_missing_equipment(address: str) -> str:
    return (
        f"{address}, main solid plan dene ke liye ready hoon, but mujhe pata hi nahi "
        "aap kis setup pe train karte hain. Gym access hai, ya ghar pe dumbbells, "
        "kettlebell, bands, pull-up bar, ya yoga mat hai? Dhyaan se batayiye."
    )


def coach_burn_recalc_hint() -> str:
    return "Action: Agar kisi activity me no-rest/extra intensity tha, batayiye — main recalc kar dunga."


def dietitian_no_logs(address: str) -> str:
    return f"{address}, aaj kuch khaya nahi ya log karna bhool gaye? Aise progress nahi hogi."


def dietitian_over_budget_line() -> str:
    return "Calorie budget exceed ho gaya hai. Kal subah extra 2km walk/run add kijiye."


def dietitian_default_line(address: str) -> str:
    return f"{address}, aaj ka nutrition theek gaya. Kal logging aur protein consistency pe focus kar."


def dietitian_quick_review(address: str) -> str:
    return (
        f"{address}, quick review: aaj ka logging complete rakha, great. Kal protein thoda "
        "aur consistent rakhte hain aur water target hit karte hain."
    )


def dietitian_review_ready(address: str) -> str:
    return (
        f"{address}, aaj ka nutrition review ready hai. Kal se thoda aur disciplined logging "
        "aur protein focus rakhenge."
    )


def morning_missing_equipment(address: str) -> str:
    return (
        f"Subah ho gayi {address}! Plan banane ko ready hoon, but pehle batayiye aap kis "
        "setup pe hain - gym access hai ya ghar pe dumbbells/bands/pull-up bar?"
    )


def morning_quick_hit_no_llm(address: str) -> str:
    return (
        f"Subah ho gayi {address}! Aaj 15-min quick hit: 1) Goblet Squat 3x12, "
        "2) Dumbbell Row 3x12/side, 3) Band Face Pull 3x15. Dhyaan se form pe focus kar."
    )


def morning_quick_hit_llm_error(address: str) -> str:
    return (
        f"Subah ho gayi {address}! Aaj 15-min quick hit: 1) DB Romanian Deadlift 3x10, "
        "2) Resistance Band Row 3x12, 3) Glute Bridge 3x15. Injury-safe pace me kar."
    )


def morning_default_plan(address: str) -> str:
    return (
        f"Subah ho gayi {address}! Aaj 15-min plan ready: 3 exercises, controlled reps, "
        "aur form pe full focus."
    )

