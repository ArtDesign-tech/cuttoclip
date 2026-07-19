# CutToClip Beta build key template.
#
# HOW TO USE:
#   1. Copy this file to "build-beta.local.ps1" (same folder). That copy is
#      gitignored, so your real keys never get committed.
#   2. Paste your Groq/Gemini keys below (comma-separate multiple keys — the
#      worker rotates through them when one is rate-limited or rejected).
#   3. Run the build:  .\scripts\build-beta.ps1
#
# Leave a value empty ("") to bake in NO default key for that provider; testers
# then enter their own key during onboarding.
#
# SECURITY: keys placed here get embedded into the .exe at compile time and can
# be extracted by anyone who has the installer. Only use throwaway / temp-account
# keys you are willing to rotate.

$env:CUTTOCLIP_EMBEDDED_GROQ_KEYS   = "gsk_replace_me_1,gsk_replace_me_2"
$env:CUTTOCLIP_EMBEDDED_GEMINI_KEYS = "AIza_replace_me_1,AIza_replace_me_2"
