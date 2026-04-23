from __future__ import annotations


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


def intake_confirm_gender(address: str) -> str:
    return with_address(address, "ek cheez confirm kijiye: male ya female?")


def intake_target_weight(address: str) -> str:
    return f"Target weight kya set karna hai {address}? (kg me batayiye)"


def intake_core_why() -> str:
    return (
        "Solid. Ab batayiye aapka main goal kya hai aur kyun? "
        "(example: sister wedding tak lean hona, 21km run improve karna)"
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

