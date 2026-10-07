import json

# -------------------------
# Debug + Safety
# -------------------------
def debug_step(step: str, details=None):
    print(f"\n [DEBUG] {step}")
    if details is not None:
        try:
            print(json.dumps(details, indent=2, default=str))
        except:
            print(details)